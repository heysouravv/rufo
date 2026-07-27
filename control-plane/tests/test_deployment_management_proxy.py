from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from rufo_control_plane.auth import AuthContext
from rufo_control_plane.gcp.cloud_run import CloudRunDeployResult
from rufo_control_plane.routes.deployments import build_router
from rufo_control_plane.settings import Settings
from rufo_control_plane.store import ControlPlaneStore


def _make_client(tmp_path, org_id="org_1"):
    settings = Settings(gcp_project_id="test-project")
    store = ControlPlaneStore(tmp_path / "test.db")

    def fake_auth() -> AuthContext:
        return AuthContext(user_id="user_1", org_id=org_id)

    app = FastAPI()
    app.include_router(build_router(settings, store, fake_auth))
    return TestClient(app), store


def _deploy_test_agent(client, tmp_path):
    with (
        patch("rufo_control_plane.routes.deployments.deploy_service") as mock_deploy,
        patch(
            "rufo_control_plane.routes.deployments.publish_agent_endpoint",
            return_value="https://rufo.example.com/agents/org-1/test-agent",
        ),
    ):
        mock_deploy.return_value = CloudRunDeployResult(service_name="projects/p/locations/l/services/s", uri=None)
        resp = client.post("/deployments", json={"agent_name": "test-agent", "image": "example.com/image:latest"})
    assert resp.status_code == 200
    return resp.json()


def test_deploy_stores_admin_token_and_injects_env(tmp_path):
    client, store = _make_client(tmp_path)
    with (
        patch("rufo_control_plane.routes.deployments.deploy_service") as mock_deploy,
        patch(
            "rufo_control_plane.routes.deployments.publish_agent_endpoint",
            return_value="https://rufo.example.com/agents/org-1/test-agent",
        ),
    ):
        mock_deploy.return_value = CloudRunDeployResult(service_name="projects/p/locations/l/services/s", uri=None)
        resp = client.post("/deployments", json={"agent_name": "test-agent", "image": "example.com/image:latest"})

    assert resp.status_code == 200
    deployment = resp.json()
    sent_env = mock_deploy.call_args.kwargs["env"]
    assert "RUFO_ADMIN_TOKEN" in sent_env
    assert len(sent_env["RUFO_ADMIN_TOKEN"]) > 20  # real token, not a placeholder

    stored_token = store.get_deployment_admin_token(deployment["service_id"])
    assert stored_token == sent_env["RUFO_ADMIN_TOKEN"]


@patch("rufo_control_plane.routes.deployments.httpx.get")
def test_list_approvals_proxies_with_admin_token(mock_get, tmp_path):
    client, store = _make_client(tmp_path)
    deployment = _deploy_test_agent(client, tmp_path)
    admin_token = store.get_deployment_admin_token(deployment["service_id"])

    mock_get.return_value = httpx.Response(200, json=[{"id": "appr-1", "status": "pending"}])
    resp = client.get(f"/deployments/{deployment['id']}/approvals")

    assert resp.status_code == 200
    assert resp.json() == [{"id": "appr-1", "status": "pending"}]
    call_kwargs = mock_get.call_args.kwargs
    assert call_kwargs["headers"]["Authorization"] == f"Bearer {admin_token}"
    assert mock_get.call_args.args[0] == "https://rufo.example.com/agents/org-1/test-agent/approvals"


@patch("rufo_control_plane.routes.deployments.httpx.post")
def test_decide_approval_proxies_body_and_forwards_status(mock_post, tmp_path):
    client, _ = _make_client(tmp_path)
    deployment = _deploy_test_agent(client, tmp_path)

    mock_post.return_value = httpx.Response(404, json={"detail": "approval not found"})
    resp = client.post(
        f"/deployments/{deployment['id']}/approvals/bogus-id/decide",
        json={"approved": True, "reason": "looks fine"},
    )

    assert resp.status_code == 404
    assert resp.json()["detail"] == "approval not found"
    assert mock_post.call_args.kwargs["json"] == {"approved": True, "reason": "looks fine"}


@patch("rufo_control_plane.routes.deployments.httpx.post")
def test_resume_proxies_successfully(mock_post, tmp_path):
    client, _ = _make_client(tmp_path)
    deployment = _deploy_test_agent(client, tmp_path)

    mock_post.return_value = httpx.Response(200, json={"status": "completed"})
    resp = client.post(
        f"/deployments/{deployment['id']}/resume",
        json={"thread_id": "t1", "approval_id": "a1", "approved": True},
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "completed"}


@patch("rufo_control_plane.routes.deployments.httpx.get")
def test_audit_proxy_passes_query_params(mock_get, tmp_path):
    client, _ = _make_client(tmp_path)
    deployment = _deploy_test_agent(client, tmp_path)

    mock_get.return_value = httpx.Response(200, json=[])
    resp = client.get(f"/deployments/{deployment['id']}/audit", params={"run_id": "run-1", "limit": 5})

    assert resp.status_code == 200
    assert mock_get.call_args.kwargs["params"] == {"limit": 5, "run_id": "run-1"}


def test_proxy_routes_are_org_scoped(tmp_path):
    """A different org's deployment_id must 404, not leak another org's
    admin token or approval data."""
    client_org1, _ = _make_client(tmp_path)
    deployment = _deploy_test_agent(client_org1, tmp_path)

    client_org2, _ = _make_client(tmp_path, org_id="org_2")
    resp = client_org2.get(f"/deployments/{deployment['id']}/approvals")
    assert resp.status_code == 404


def test_proxy_returns_502_when_agent_unreachable(tmp_path):
    client, _ = _make_client(tmp_path)
    deployment = _deploy_test_agent(client, tmp_path)

    with patch("rufo_control_plane.routes.deployments.httpx.get", side_effect=httpx.ConnectError("refused")):
        resp = client.get(f"/deployments/{deployment['id']}/approvals")
    assert resp.status_code == 502


def test_proxy_returns_502_when_no_admin_token_on_file(tmp_path):
    client, store = _make_client(tmp_path)
    deployment = _deploy_test_agent(client, tmp_path)
    # Simulate a pre-admin-token deployment record (redeploy would fix this).
    store._conn.execute("DELETE FROM deployment_admin_tokens WHERE service_id = ?", (deployment["service_id"],))
    store._conn.commit()

    resp = client.get(f"/deployments/{deployment['id']}/approvals")
    assert resp.status_code == 502
