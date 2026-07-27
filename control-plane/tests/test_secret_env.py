from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from rufo_control_plane.auth import AuthContext
from rufo_control_plane.routes.deployments import build_router
from rufo_control_plane.settings import Settings
from rufo_control_plane.store import ControlPlaneStore


def _make_client(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real-secret-value")
    settings = Settings(gcp_project_id="test-project")
    store = ControlPlaneStore(tmp_path / "test.db")

    def fake_auth() -> AuthContext:
        return AuthContext(user_id="user_1", org_id="org_1")

    app = FastAPI()
    app.include_router(build_router(settings, store, fake_auth))
    return TestClient(app)


def test_secret_env_from_server_injects_real_value(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    with (
        patch("rufo_control_plane.routes.deployments.deploy_service") as mock_deploy,
        patch("rufo_control_plane.routes.deployments.publish_agent_endpoint", return_value="https://rufo.example.com/agents/org-1/test-agent"),
    ):
        mock_deploy.return_value = MagicMock(uri="https://example.run.app")
        resp = client.post(
            "/deployments",
            json={
                "agent_name": "test-agent",
                "image": "example.com/image:latest",
                "secret_env_from_server": ["OPENAI_API_KEY"],
            },
        )
    assert resp.status_code == 200
    sent_env = mock_deploy.call_args.kwargs["env"]
    assert sent_env["OPENAI_API_KEY"] == "sk-real-secret-value"


def test_secret_env_from_server_missing_secret_returns_400(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    resp = client.post(
        "/deployments",
        json={
            "agent_name": "test-agent",
            "image": "example.com/image:latest",
            "secret_env_from_server": ["SOME_SECRET_NOT_SET"],
        },
    )
    assert resp.status_code == 400
