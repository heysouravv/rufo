from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

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
