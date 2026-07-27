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
    #
    # The context manager object itself (not just the yielded PostgresSaver)
    # must be kept alive: it owns the generator whose `with Connection.connect(...)`
    # block closes the connection on __exit__. Discarding it left the
    # generator with no references, so the garbage collector finalized it --
    # calling __exit__ and closing the connection out from under the saver,
    # sometimes before its first query. Caught by a real Cloud Run deploy
    # (`psycopg.OperationalError: the connection is closed`), not by any
    # local test, since GC timing under a quick script never triggered it.
    cm = PostgresSaver.from_conn_string(database_url)
    saver = cm.__enter__()
    saver.setup()
    saver._rufo_keep_alive_cm = cm
    return saver
