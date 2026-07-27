from __future__ import annotations

import time
import uuid

from google.cloud.sql.connector import Connector

from rufo_control_plane.api_tokens import (
    DEVICE_CODE_TTL_SECONDS,
    generate_api_token,
    generate_device_code,
    generate_user_code,
    hash_token,
)
from rufo_control_plane.secrets import SecretCipher

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
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL,
    UNIQUE(org_id, agent_name)
);

CREATE TABLE IF NOT EXISTS org_secrets (
    org_id TEXT NOT NULL,
    name TEXT NOT NULL,
    encrypted_value TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (org_id, name)
);

CREATE TABLE IF NOT EXISTS device_codes (
    device_code TEXT PRIMARY KEY,
    user_code TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    org_id TEXT,
    org_slug TEXT,
    user_id TEXT,
    api_token TEXT,
    created_at DOUBLE PRECISION NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS api_tokens (
    token_hash TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    org_slug TEXT,
    user_id TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL
);
"""

# ALTER ... ADD COLUMN IF NOT EXISTS, run unconditionally in _ensure_schema:
# CREATE TABLE IF NOT EXISTS above only helps a brand-new database -- the
# already-running production table predates org_slug and needs migrating.
_MIGRATIONS = [
    "ALTER TABLE device_codes ADD COLUMN IF NOT EXISTS org_slug TEXT",
    "ALTER TABLE api_tokens ADD COLUMN IF NOT EXISTS org_slug TEXT",
]

_DEPLOYMENT_COLUMNS = [
    "id",
    "org_id",
    "agent_name",
    "service_id",
    "region",
    "image",
    "uri",
    "status",
    "created_at",
    "updated_at",
]


def _row_to_dict(columns: list[str], row: tuple) -> dict:
    return dict(zip(columns, row))


class PostgresControlPlaneStore:
    """Same public interface as the SQLite-backed ControlPlaneStore (store.py)
    -- used in production, connected via the Cloud SQL Python Connector
    (handles IAM auth + TLS, no Cloud SQL Proxy sidecar needed) instead of a
    local file. Fixes the fragility of a local SQLite file being the only
    record tying a running Cloud Run deployment back to an org -- caught
    live when clearing `rufo_control_plane_data` during "cleanup" orphaned
    a still-running deployment.
    """

    def __init__(
        self,
        *,
        instance_connection_name: str,
        db_user: str,
        db_password: str,
        db_name: str,
        encryption_key: str,
    ) -> None:
        self._connector = Connector()
        self._instance_connection_name = instance_connection_name
        self._db_user = db_user
        self._db_password = db_password
        self._db_name = db_name
        self._cipher = SecretCipher(encryption_key)
        self._conn = self._connect()
        self._ensure_schema()

    def _connect(self):
        return self._connector.connect(
            self._instance_connection_name,
            "pg8000",
            user=self._db_user,
            password=self._db_password,
            db=self._db_name,
        )

    def _ensure_schema(self) -> None:
        cur = self._conn.cursor()
        cur.execute(SCHEMA)
        for migration in _MIGRATIONS:
            cur.execute(migration)
        self._conn.commit()
        cur.close()

    def _execute(self, query: str, params: tuple = ()):
        """Reconnect once on a dropped connection (Cloud SQL connections can
        idle out) rather than failing every request until the process
        restarts."""
        try:
            cur = self._conn.cursor()
            cur.execute(query, params)
            return cur
        except Exception:
            self._conn = self._connect()
            cur = self._conn.cursor()
            cur.execute(query, params)
            return cur

    # -- deployments ---------------------------------------------------

    def upsert_deployment(
        self, org_id: str, agent_name: str, service_id: str, region: str, image: str, uri: str | None, status: str
    ) -> dict:
        now = time.time()
        cur = self._execute(
            "SELECT id FROM deployments WHERE org_id = %s AND agent_name = %s", (org_id, agent_name)
        )
        existing = cur.fetchone()
        cur.close()

        if existing:
            deployment_id = existing[0]
            cur = self._execute(
                "UPDATE deployments SET service_id=%s, region=%s, image=%s, uri=%s, status=%s, updated_at=%s "
                "WHERE id=%s",
                (service_id, region, image, uri, status, now, deployment_id),
            )
        else:
            deployment_id = str(uuid.uuid4())
            cur = self._execute(
                "INSERT INTO deployments (id, org_id, agent_name, service_id, region, image, uri, status, "
                "created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (deployment_id, org_id, agent_name, service_id, region, image, uri, status, now, now),
            )
        self._conn.commit()
        cur.close()
        return self.get_deployment(org_id, deployment_id)

    def get_deployment(self, org_id: str, deployment_id: str) -> dict | None:
        cur = self._execute(
            f"SELECT {', '.join(_DEPLOYMENT_COLUMNS)} FROM deployments WHERE id = %s AND org_id = %s",
            (deployment_id, org_id),
        )
        row = cur.fetchone()
        cur.close()
        return _row_to_dict(_DEPLOYMENT_COLUMNS, row) if row else None

    def list_deployments(self, org_id: str) -> list[dict]:
        cur = self._execute(
            f"SELECT {', '.join(_DEPLOYMENT_COLUMNS)} FROM deployments WHERE org_id = %s ORDER BY created_at DESC",
            (org_id,),
        )
        rows = cur.fetchall()
        cur.close()
        return [_row_to_dict(_DEPLOYMENT_COLUMNS, r) for r in rows]

    def delete_deployment(self, org_id: str, deployment_id: str) -> dict | None:
        deployment = self.get_deployment(org_id, deployment_id)
        if deployment is None:
            return None
        cur = self._execute("DELETE FROM deployments WHERE id = %s AND org_id = %s", (deployment_id, org_id))
        self._conn.commit()
        cur.close()
        return deployment

    # -- org secrets -----------------------------------------------------

    def set_secret(self, org_id: str, name: str, value: str) -> None:
        encrypted = self._cipher.encrypt(value)
        cur = self._execute(
            "INSERT INTO org_secrets (org_id, name, encrypted_value, created_at) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (org_id, name) DO UPDATE SET encrypted_value = EXCLUDED.encrypted_value",
            (org_id, name, encrypted, time.time()),
        )
        self._conn.commit()
        cur.close()

    def get_secret(self, org_id: str, name: str) -> str | None:
        cur = self._execute(
            "SELECT encrypted_value FROM org_secrets WHERE org_id = %s AND name = %s", (org_id, name)
        )
        row = cur.fetchone()
        cur.close()
        if row is None:
            return None
        return self._cipher.decrypt(row[0])

    def list_secret_names(self, org_id: str) -> list[str]:
        cur = self._execute(
            "SELECT name FROM org_secrets WHERE org_id = %s ORDER BY name", (org_id,)
        )
        rows = cur.fetchall()
        cur.close()
        return [r[0] for r in rows]

    def delete_secret(self, org_id: str, name: str) -> bool:
        cur = self._execute(
            "DELETE FROM org_secrets WHERE org_id = %s AND name = %s", (org_id, name)
        )
        deleted = cur.rowcount > 0
        self._conn.commit()
        cur.close()
        return deleted

    # -- device auth (`rufo login`) --------------------------------------

    def create_device_code(self) -> dict:
        now = time.time()
        device_code = generate_device_code()
        user_code = generate_user_code()
        cur = self._execute(
            "INSERT INTO device_codes (device_code, user_code, created_at, expires_at) "
            "VALUES (%s, %s, %s, %s)",
            (device_code, user_code, now, now + DEVICE_CODE_TTL_SECONDS),
        )
        self._conn.commit()
        cur.close()
        return {
            "device_code": device_code,
            "user_code": user_code,
            "expires_in": DEVICE_CODE_TTL_SECONDS,
        }

    def poll_device_code(self, device_code: str) -> dict:
        cur = self._execute(
            "SELECT status, api_token, expires_at FROM device_codes WHERE device_code = %s",
            (device_code,),
        )
        row = cur.fetchone()
        cur.close()
        if row is None:
            return {"status": "not_found"}
        status, api_token, expires_at = row
        if status == "pending" and time.time() > expires_at:
            return {"status": "expired"}
        if status == "approved":
            return {"status": "approved", "token": api_token}
        return {"status": status}

    def approve_device_code(self, user_code: str, org_id: str, org_slug: str | None, user_id: str) -> bool:
        """Called from the dashboard's /device page once a signed-in Clerk
        user confirms the code the CLI printed. Issues a real API token and
        stores only its hash -- the raw value is returned exactly once, via
        the CLI's next poll, never persisted in plaintext."""
        cur = self._execute(
            "SELECT device_code, expires_at FROM device_codes WHERE user_code = %s AND status = 'pending'",
            (user_code,),
        )
        row = cur.fetchone()
        cur.close()
        if row is None:
            return False
        device_code, expires_at = row
        if time.time() > expires_at:
            return False

        token = generate_api_token()
        now = time.time()
        cur = self._execute(
            "INSERT INTO api_tokens (token_hash, org_id, org_slug, user_id, created_at) VALUES (%s, %s, %s, %s, %s)",
            (hash_token(token), org_id, org_slug, user_id, now),
        )
        cur.close()
        cur = self._execute(
            "UPDATE device_codes SET status = 'approved', org_id = %s, org_slug = %s, user_id = %s, api_token = %s "
            "WHERE device_code = %s",
            (org_id, org_slug, user_id, token, device_code),
        )
        self._conn.commit()
        cur.close()
        return True

    def verify_api_token(self, token: str) -> tuple[str, str | None, str] | None:
        """Returns (org_id, org_slug, user_id) if the token is valid, else None."""
        cur = self._execute(
            "SELECT org_id, org_slug, user_id FROM api_tokens WHERE token_hash = %s", (hash_token(token),)
        )
        row = cur.fetchone()
        cur.close()
        return (row[0], row[1], row[2]) if row else None
