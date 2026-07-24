from __future__ import annotations

import os

from langgraph.checkpoint.memory import MemorySaver


def get_checkpointer():
    """Durable Postgres checkpointer when DATABASE_URL is set (hosted
    deploys, wired by the control plane via a Cloud Run Cloud-SQL-instance
    attachment -- a Unix socket at /cloudsql/<instance>, no VPC or proxy
    sidecar needed), otherwise the in-memory MemorySaver used for local dev.

    Agents should call this instead of hardcoding MemorySaver() directly --
    it's the only thing that needs to change for a workflow-surface
    (approval-gated) agent to survive a Cloud Run instance being reclaimed
    mid-run instead of silently losing its paused state.
    """
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        return MemorySaver()

    from langgraph.checkpoint.postgres import PostgresSaver

    # from_conn_string is a @contextmanager generator, not a plain
    # constructor -- entered manually and kept open for the process's
    # lifetime (this is called once at agent-module load time and the
    # runtime process lives as long as the Cloud Run instance does).
    saver = PostgresSaver.from_conn_string(database_url).__enter__()
    saver.setup()
    return saver
