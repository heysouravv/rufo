from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

from rufo_control_plane.api_tokens import (
    DEVICE_CODE_TTL_SECONDS,
    generate_api_token,
    generate_device_code,
    generate_user_code,
    hash_token,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS deployments (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    service_id TEXT NOT NULL,
    region TEXT NOT NULL,
    image TEXT NOT NULL,
    uri TEXT,
    status TEXT NOT NULL DEFAULT 'deploying',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(org_id, agent_name)
);
"""


class ControlPlaneStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def upsert_deployment(
        self, org_id: str, agent_name: str, service_id: str, region: str, image: str, uri: str | None, status: str
    ) -> dict:
        now = time.time()
        existing = self._conn.execute(
            "SELECT id FROM deployments WHERE org_id = ? AND agent_name = ?", (org_id, agent_name)
        ).fetchone()
        if existing:
            self._conn.execute(
                "UPDATE deployments SET service_id=?, region=?, image=?, uri=?, status=?, updated_at=? WHERE id=?",
                (service_id, region, image, uri, status, now, existing["id"]),
            )
            deployment_id = existing["id"]
        else:
            deployment_id = str(uuid.uuid4())
            self._conn.execute(
                "INSERT INTO deployments (id, org_id, agent_name, service_id, region, image, uri, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (deployment_id, org_id, agent_name, service_id, region, image, uri, status, now, now),
            )
        self._conn.commit()
        return self.get_deployment(org_id, deployment_id)

    def get_deployment(self, org_id: str, deployment_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM deployments WHERE id = ? AND org_id = ?", (deployment_id, org_id)
        ).fetchone()
        return dict(row) if row else None

    def list_deployments(self, org_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM deployments WHERE org_id = ? ORDER BY created_at DESC", (org_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_deployment(self, org_id: str, deployment_id: str) -> dict | None:
        deployment = self.get_deployment(org_id, deployment_id)
        if deployment is None:
            return None
        self._conn.execute("DELETE FROM deployments WHERE id = ? AND org_id = ?", (deployment_id, org_id))
        self._conn.commit()
        return deployment

    # -- org secrets (SQLite fallback, no encryption -- local dev only;
    #    production always uses PostgresControlPlaneStore, which encrypts) --

    def set_secret(self, org_id: str, name: str, value: str) -> None:
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS org_secrets (org_id TEXT, name TEXT, encrypted_value TEXT, "
            "created_at REAL, PRIMARY KEY (org_id, name))"
        )
        self._conn.execute(
            "INSERT INTO org_secrets (org_id, name, encrypted_value, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(org_id, name) DO UPDATE SET encrypted_value=excluded.encrypted_value",
            (org_id, name, value, time.time()),
        )
        self._conn.commit()

    def get_secret(self, org_id: str, name: str) -> str | None:
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS org_secrets (org_id TEXT, name TEXT, encrypted_value TEXT, "
            "created_at REAL, PRIMARY KEY (org_id, name))"
        )
        row = self._conn.execute(
            "SELECT encrypted_value FROM org_secrets WHERE org_id = ? AND name = ?", (org_id, name)
        ).fetchone()
        return row["encrypted_value"] if row else None

    def list_secret_names(self, org_id: str) -> list[str]:
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS org_secrets (org_id TEXT, name TEXT, encrypted_value TEXT, "
            "created_at REAL, PRIMARY KEY (org_id, name))"
        )
        rows = self._conn.execute(
            "SELECT name FROM org_secrets WHERE org_id = ? ORDER BY name", (org_id,)
        ).fetchall()
        return [r["name"] for r in rows]

    def delete_secret(self, org_id: str, name: str) -> bool:
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS org_secrets (org_id TEXT, name TEXT, encrypted_value TEXT, "
            "created_at REAL, PRIMARY KEY (org_id, name))"
        )
        cur = self._conn.execute("DELETE FROM org_secrets WHERE org_id = ? AND name = ?", (org_id, name))
        self._conn.commit()
        return cur.rowcount > 0

    # -- device auth (`rufo login`) -- local/test fallback, same shape as
    #    PostgresControlPlaneStore's version --------------------------------

    def _ensure_device_auth_tables(self) -> None:
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS device_codes (device_code TEXT PRIMARY KEY, user_code TEXT UNIQUE, "
            "status TEXT DEFAULT 'pending', org_id TEXT, user_id TEXT, api_token TEXT, "
            "created_at REAL, expires_at REAL)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS api_tokens (token_hash TEXT PRIMARY KEY, org_id TEXT, "
            "user_id TEXT, created_at REAL)"
        )

    def create_device_code(self) -> dict:
        self._ensure_device_auth_tables()
        now = time.time()
        device_code = generate_device_code()
        user_code = generate_user_code()
        self._conn.execute(
            "INSERT INTO device_codes (device_code, user_code, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (device_code, user_code, now, now + DEVICE_CODE_TTL_SECONDS),
        )
        self._conn.commit()
        return {"device_code": device_code, "user_code": user_code, "expires_in": DEVICE_CODE_TTL_SECONDS}

    def poll_device_code(self, device_code: str) -> dict:
        self._ensure_device_auth_tables()
        row = self._conn.execute(
            "SELECT status, api_token, expires_at FROM device_codes WHERE device_code = ?", (device_code,)
        ).fetchone()
        if row is None:
            return {"status": "not_found"}
        if row["status"] == "pending" and time.time() > row["expires_at"]:
            return {"status": "expired"}
        if row["status"] == "approved":
            return {"status": "approved", "token": row["api_token"]}
        return {"status": row["status"]}

    def approve_device_code(self, user_code: str, org_id: str, user_id: str) -> bool:
        self._ensure_device_auth_tables()
        row = self._conn.execute(
            "SELECT device_code, expires_at FROM device_codes WHERE user_code = ? AND status = 'pending'",
            (user_code,),
        ).fetchone()
        if row is None or time.time() > row["expires_at"]:
            return False

        token = generate_api_token()
        now = time.time()
        self._conn.execute(
            "INSERT INTO api_tokens (token_hash, org_id, user_id, created_at) VALUES (?, ?, ?, ?)",
            (hash_token(token), org_id, user_id, now),
        )
        self._conn.execute(
            "UPDATE device_codes SET status='approved', org_id=?, user_id=?, api_token=? WHERE device_code=?",
            (org_id, user_id, token, row["device_code"]),
        )
        self._conn.commit()
        return True

    def verify_api_token(self, token: str) -> tuple[str, str] | None:
        self._ensure_device_auth_tables()
        row = self._conn.execute(
            "SELECT org_id, user_id FROM api_tokens WHERE token_hash = ?", (hash_token(token),)
        ).fetchone()
        return (row["org_id"], row["user_id"]) if row else None


def create_store(settings):
    """Postgres in production (settings.db_instance_connection_name set),
    SQLite for local dev -- same public interface either way."""
    if settings.db_instance_connection_name:
        from rufo_control_plane.pg_store import PostgresControlPlaneStore

        return PostgresControlPlaneStore(
            instance_connection_name=settings.db_instance_connection_name,
            db_user=settings.db_user,
            db_password=settings.db_password,
            db_name=settings.db_name,
            encryption_key=settings.encryption_key,
        )
    return ControlPlaneStore(settings.db_path)
