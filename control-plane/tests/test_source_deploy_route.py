import io
import tarfile
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from rufo_control_plane.auth import AuthContext
from rufo_control_plane.gcp.cloud_run import CloudRunDeployResult
from rufo_control_plane.routes.source_deploy import build_router
from rufo_control_plane.settings import Settings
from rufo_control_plane.store import ControlPlaneStore


def _make_tar(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


_RUFO_TOML = b"""
[agent]
name = "demo-agent"
entrypoint = "agent.py:agent"

[dependencies]
langgraph = "^0.2"
"""

_RUFO_YAML = b"""
port: 8080
env:
  - OPENAI_API_KEY
"""

_RUFO_YAML_UNRESOLVABLE_SECRET = b"""
port: 8080
env:
  - RUFO_TEST_SECRET_NOT_SET_ANYWHERE
"""


def _make_client(tmp_path):
    store = ControlPlaneStore(tmp_path / "test.db")
    settings = Settings(gcp_project_id="test-project", gcp_region="us-central1")

    def fake_auth() -> AuthContext:
        return AuthContext(user_id="user_1", org_id="org_1")

    app = FastAPI()
    app.include_router(build_router(settings, store, fake_auth))
    return TestClient(app), store


@patch("rufo_control_plane.routes.source_deploy.deploy_service")
@patch("rufo_control_plane.routes.source_deploy.submit_build_and_wait")
@patch("rufo_control_plane.routes.source_deploy.upload_source", return_value="org/build123.tar.gz")
@patch("rufo_control_plane.routes.source_deploy.ensure_staging_bucket", return_value="test-project-rufo-agent-sources")
def test_deploy_from_source_succeeds(mock_bucket, mock_upload, mock_build, mock_deploy, tmp_path):
    client, store = _make_client(tmp_path)
    store.set_secret("org_1", "OPENAI_API_KEY", "sk-real")
    mock_deploy.return_value = CloudRunDeployResult(
        service_name="projects/test-project/locations/us-central1/services/rufo-x", uri="https://rufo-x.run.app"
    )

    tar_bytes = _make_tar({"rufo.toml": _RUFO_TOML, "rufo.yaml": _RUFO_YAML, "agent.py": b"agent = None\n"})
    resp = client.post("/deployments/from-source", files={"source": ("agent.tar.gz", tar_bytes, "application/gzip")})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["agent_name"] == "demo-agent"
    assert body["uri"] == "https://rufo-x.run.app"

    mock_build.assert_called_once()
    deploy_kwargs = mock_deploy.call_args.kwargs
    assert deploy_kwargs["env"]["OPENAI_API_KEY"] == "sk-real"
    assert store.list_deployments("org_1")


def test_deploy_from_source_rejects_missing_manifest(tmp_path):
    client, _ = _make_client(tmp_path)
    tar_bytes = _make_tar({"agent.py": b"agent = None\n"})
    resp = client.post("/deployments/from-source", files={"source": ("agent.tar.gz", tar_bytes, "application/gzip")})
    assert resp.status_code == 400
    assert "rufo.toml" in resp.json()["detail"]


@patch("rufo_control_plane.routes.source_deploy.submit_build_and_wait")
def test_deploy_from_source_rejects_missing_secret(mock_build, tmp_path):
    client, _ = _make_client(tmp_path)
    tar_bytes = _make_tar(
        {"rufo.toml": _RUFO_TOML, "rufo.yaml": _RUFO_YAML_UNRESOLVABLE_SECRET, "agent.py": b"agent = None\n"}
    )
    resp = client.post("/deployments/from-source", files={"source": ("agent.tar.gz", tar_bytes, "application/gzip")})
    assert resp.status_code == 400
    assert "RUFO_TEST_SECRET_NOT_SET_ANYWHERE" in resp.json()["detail"]
    # never reached the (expensive) build step -- secrets are checked first
    mock_build.assert_not_called()


@patch("rufo_control_plane.routes.source_deploy.submit_build_and_wait", side_effect=RuntimeError("build failed: boom"))
@patch("rufo_control_plane.routes.source_deploy.upload_source", return_value="org/build123.tar.gz")
@patch("rufo_control_plane.routes.source_deploy.ensure_staging_bucket", return_value="test-project-rufo-agent-sources")
def test_deploy_from_source_returns_502_on_build_failure(mock_bucket, mock_upload, mock_build, tmp_path):
    client, store = _make_client(tmp_path)
    store.set_secret("org_1", "OPENAI_API_KEY", "sk-real")
    tar_bytes = _make_tar({"rufo.toml": _RUFO_TOML, "rufo.yaml": _RUFO_YAML, "agent.py": b"agent = None\n"})
    resp = client.post("/deployments/from-source", files={"source": ("agent.tar.gz", tar_bytes, "application/gzip")})
    assert resp.status_code == 502
