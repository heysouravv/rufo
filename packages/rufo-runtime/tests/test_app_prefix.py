import importlib
import sys

import pytest


def _write_fixture_agent(tmp_path):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "policy.yaml").write_text("version: 1\ndefaults:\n  action: allow\nrules: []\nlimits: []\n")
    (agent_dir / "agent.py").write_text(
        "from langgraph.graph import END, START, StateGraph\n"
        "from typing import TypedDict\n\n"
        "class State(TypedDict):\n"
        "    result: str\n\n"
        "def node(state: State) -> State:\n"
        "    return {'result': 'ok'}\n\n"
        "graph = StateGraph(State)\n"
        "graph.add_node('node', node)\n"
        "graph.add_edge(START, 'node')\n"
        "graph.add_edge('node', END)\n"
        "agent = graph.compile()\n"
    )
    return agent_dir


def _import_app(monkeypatch, tmp_path, *, public_path_prefix=None):
    """rufo_runtime.app runs its setup at *import* time, so each variant
    (prefixed vs unprefixed) needs a fresh module instance -- reload after
    setting env vars, rather than importing once and hoping."""
    agent_dir = _write_fixture_agent(tmp_path)
    monkeypatch.setenv("RUFO_POLICY_PATH", str(agent_dir / "policy.yaml"))
    monkeypatch.setenv("RUFO_AGENT_MODULE", str(agent_dir / "agent.py"))
    monkeypatch.setenv("RUFO_AGENT_ENTRYPOINT", "agent")
    monkeypatch.setenv("RUFO_DB_PATH", str(tmp_path / "rufo.db"))
    monkeypatch.delenv("RUFO_DEPLOY_CONFIG_PATH", raising=False)
    if public_path_prefix:
        monkeypatch.setenv("RUFO_PUBLIC_PATH_PREFIX", public_path_prefix)
    else:
        monkeypatch.delenv("RUFO_PUBLIC_PATH_PREFIX", raising=False)

    for mod_name in ("rufo_runtime.app", "rufo_runtime.settings"):
        sys.modules.pop(mod_name, None)
    import rufo_runtime.settings  # noqa: F401
    import rufo_runtime.app as app_module

    return importlib.reload(app_module)


@pytest.fixture(autouse=True)
def _cleanup_module_cache():
    yield
    for mod_name in ("rufo_runtime.app", "rufo_runtime.settings"):
        sys.modules.pop(mod_name, None)


def test_app_serves_at_root_without_prefix(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    app_module = _import_app(monkeypatch, tmp_path)
    client = TestClient(app_module.app)

    assert client.get("/healthz").status_code == 200
    assert client.post("/invoke", json={"input": {}}).status_code == 200


def test_app_mounts_under_public_path_prefix(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    app_module = _import_app(monkeypatch, tmp_path, public_path_prefix="/agents/acme/my-agent")
    client = TestClient(app_module.app)

    assert client.get("/agents/acme/my-agent/healthz").status_code == 200
    assert client.post("/agents/acme/my-agent/invoke", json={"input": {}}).status_code == 200

    # unprefixed root is not reachable once mounted under a prefix
    assert client.get("/healthz").status_code == 404
