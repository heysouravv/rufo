from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    args TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    decision_reason TEXT,
    created_at REAL NOT NULL,
    decided_at REAL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    tool_name TEXT,
    payload TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


class Store:
    """SQLite-backed persistence for approvals and the audit trail.

    Single-file, single-process: fine for one runtime instance. A Postgres
    backend is the natural upgrade for multi-instance deployments.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def create_approval(self, run_id: str, tool_name: str, args: dict, reason: str) -> str:
        approval_id = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO approvals (id, run_id, tool_name, args, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (approval_id, run_id, tool_name, json.dumps(args), reason, time.time()),
        )
        self._conn.commit()
        self.log_event(run_id, "approval_requested", tool_name, {"approval_id": approval_id, "reason": reason})
        return approval_id

    def get_approval(self, approval_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        return dict(row) if row else None

    def list_approvals(self, status: str | None = None) -> list[dict]:
        if status:
            rows = self._conn.execute("SELECT * FROM approvals WHERE status = ? ORDER BY created_at DESC", (status,)).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM approvals ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    def decide_approval(self, approval_id: str, approved: bool, reason: str | None) -> dict | None:
        status = "approved" if approved else "rejected"
        self._conn.execute(
            "UPDATE approvals SET status = ?, decision_reason = ?, decided_at = ? WHERE id = ?",
            (status, reason, time.time(), approval_id),
        )
        self._conn.commit()
        approval = self.get_approval(approval_id)
        if approval:
            self.log_event(approval["run_id"], f"approval_{status}", approval["tool_name"], {"approval_id": approval_id, "reason": reason})
        return approval

    def log_event(self, run_id: str, event_type: str, tool_name: str | None, payload: dict) -> None:
        self._conn.execute(
            "INSERT INTO audit_log (id, run_id, event_type, tool_name, payload, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), run_id, event_type, tool_name, json.dumps(payload), time.time()),
        )
        self._conn.commit()

    def list_audit(self, run_id: str | None = None, limit: int = 200) -> list[dict]:
        if run_id:
            rows = self._conn.execute(
                "SELECT * FROM audit_log WHERE run_id = ? ORDER BY created_at DESC LIMIT ?", (run_id, limit)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM audit_log ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
