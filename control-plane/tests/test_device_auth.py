from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from rufo_control_plane.auth import AuthContext
from rufo_control_plane.routes.device_auth import build_router
from rufo_control_plane.store import ControlPlaneStore


def _make_client(tmp_path):
    store = ControlPlaneStore(tmp_path / "test.db")

    def fake_clerk_auth() -> AuthContext:
        return AuthContext(user_id="user_1", org_id="org_1", org_slug="org-one")

    app = FastAPI()
    app.include_router(build_router(store, fake_clerk_auth))
    return TestClient(app), store


def test_full_device_auth_flow(tmp_path):
    client, store = _make_client(tmp_path)

    start_resp = client.post("/auth/device/start")
    assert start_resp.status_code == 200
    body = start_resp.json()
    device_code, user_code = body["device_code"], body["user_code"]

    # not yet approved
    poll_resp = client.get("/auth/device/poll", params={"device_code": device_code})
    assert poll_resp.json()["status"] == "pending"

    # dashboard approves (fake_clerk_auth simulates a signed-in user)
    approve_resp = client.post("/auth/device/approve", json={"user_code": user_code})
    assert approve_resp.status_code == 200

    # CLI's next poll gets a real token
    poll_resp = client.get("/auth/device/poll", params={"device_code": device_code})
    poll_body = poll_resp.json()
    assert poll_body["status"] == "approved"
    assert poll_body["token"].startswith("rufo_pat_")

    # the issued token verifies back to the approving org/slug/user
    result = store.verify_api_token(poll_body["token"])
    assert result == ("org_1", "org-one", "user_1")


def test_approve_unknown_code_returns_404(tmp_path):
    client, _ = _make_client(tmp_path)
    resp = client.post("/auth/device/approve", json={"user_code": "ZZZZ-ZZZZ"})
    assert resp.status_code == 404


def test_poll_unknown_device_code(tmp_path):
    client, _ = _make_client(tmp_path)
    resp = client.get("/auth/device/poll", params={"device_code": "ddc_bogus"})
    assert resp.json()["status"] == "not_found"


def test_invalid_api_token_returns_none(tmp_path):
    _, store = _make_client(tmp_path)
    assert store.verify_api_token("rufo_pat_not-a-real-token") is None
