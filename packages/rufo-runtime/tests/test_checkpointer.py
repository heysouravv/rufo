import gc
import os
from contextlib import contextmanager

import pytest


class _FakeResource:
    def __init__(self):
        self.closed = False


@contextmanager
def _fake_from_conn_string():
    """Mirrors PostgresSaver.from_conn_string's exact shape: a @contextmanager
    generator with a nested `with` block that closes the resource on exit."""
    resource = _FakeResource()
    try:
        yield resource
    finally:
        resource.closed = True


def test_discarding_the_context_manager_lets_gc_close_the_resource():
    """Reproduces the real bug: entering a @contextmanager generator and
    keeping only the yielded value (not the generator/CM itself) leaves the
    CM with zero references. GC finalizes it, running its `finally` block
    and closing the resource -- exactly what happened to a real Cloud SQL
    connection in production (psycopg.OperationalError: the connection is
    closed), invisible in a quick local script but real under GC pressure."""
    resource = _fake_from_conn_string().__enter__()
    gc.collect()
    assert resource.closed is True


def test_keeping_the_context_manager_alive_prevents_premature_close():
    cm = _fake_from_conn_string()
    resource = cm.__enter__()
    resource._keep_alive_cm = cm  # the fix: tie the CM's lifetime to the resource's
    gc.collect()
    assert resource.closed is False


def test_get_checkpointer_returns_memory_saver_without_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from rufo_runtime.checkpointer import get_checkpointer
    from langgraph.checkpoint.memory import MemorySaver

    saver = get_checkpointer()
    assert isinstance(saver, MemorySaver)


@pytest.mark.skipif(
    "DATABASE_URL" not in os.environ, reason="requires a real Postgres connection"
)
def test_get_checkpointer_postgres_saver_survives_gc():
    from rufo_runtime.checkpointer import get_checkpointer

    saver = get_checkpointer()
    gc.collect()
    # would raise psycopg.OperationalError before the keep-alive fix
    saver.list(config={"configurable": {"thread_id": "gc-survival-check"}})
