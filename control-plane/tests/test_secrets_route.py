from fastapi import FastAPI
from fastapi.testclient import TestClient
from rufo_control_plane.auth import AuthContext
from rufo_control_plane.routes.secrets import build_router
from rufo_control_plane.store import ControlPlaneStore


def _make_client(tmp_path):
    store = ControlPlaneStore(tmp_path / "test.db")

    def fake_auth() -> AuthContext:
        return AuthContext(user_id="user_1", org_id="org_1")

    app = FastAPI()
    app.include_router(build_router(store, fake_auth))
    return TestClient(app), store


def test_set_list_delete_secret(tmp_path):
    client, store = _make_client(tmp_path)

    resp = client.post("/secrets", json={"name": "OPENAI_API_KEY", "value": "sk-real"})
    assert resp.status_code == 200

    resp = client.get("/secrets")
    assert resp.json() == ["OPENAI_API_KEY"]

    # value is never exposed back through the API
    assert store.get_secret("org_1", "OPENAI_API_KEY") == "sk-real"

    resp = client.delete("/secrets/OPENAI_API_KEY")
    assert resp.status_code == 200
    assert client.get("/secrets").json() == []


def test_delete_missing_secret_returns_404(tmp_path):
    client, _ = _make_client(tmp_path)
    resp = client.delete("/secrets/NOPE")
    assert resp.status_code == 404


def test_secrets_are_org_scoped(tmp_path):
    store = ControlPlaneStore(tmp_path / "test.db")
    store.set_secret("org_a", "KEY", "value-a")
    store.set_secret("org_b", "KEY", "value-b")
    assert store.get_secret("org_a", "KEY") == "value-a"
    assert store.get_secret("org_b", "KEY") == "value-b"
