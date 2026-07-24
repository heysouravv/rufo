from __future__ import annotations

import hashlib
import os
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from rufo_control_plane.auth import AuthContext
from rufo_control_plane.gcp.cloud_run import delete_service, deploy_service, get_service
from rufo_control_plane.settings import Settings
from rufo_control_plane.store import ControlPlaneStore


class DeployRequest(BaseModel):
    agent_name: str
    image: str
    port: int = 8080
    env: dict[str, str] = {}
    # Names only, never values: the client asks for a secret *by name*.
    # Resolved against the calling org's own secret store first (set via
    # POST /api/secrets -- e.g. a customer's own OPENAI_API_KEY), falling
    # back to the control plane's own process environment only if the org
    # hasn't set one -- keeps the original demo agent working without every
    # org needing to duplicate its key, while giving real orgs a place to
    # bring their own.
    secret_env_from_server: list[str] = []
    min_instances: int = 0
    max_instances: int = 5


def _sanitize(value: str, max_len: int) -> str:
    """Cloud Run service IDs: lowercase, digits, hyphens; must start with a letter
    and not end with one; must be under 50 chars total (Google's actual limit,
    not the 63 RFC1035 label limit used elsewhere in GCP)."""
    slug = re.sub(r"[^a-z0-9-]", "-", value.lower()).strip("-")
    return slug[:max_len].rstrip("-")


def _org_fingerprint(org_id: str) -> str:
    """A short, deterministic, length-stable stand-in for org_id -- embedding
    the raw org_id risked overflowing Cloud Run's <50-char service_id limit
    on its own, and its length varies by org."""
    return hashlib.sha256(org_id.encode()).hexdigest()[:10]


def _service_id_for(org_id: str, agent_name: str) -> str:
    agent_slug = _sanitize(agent_name, max_len=20) or "agent"
    service_id = f"rufo-{_org_fingerprint(org_id)}-{agent_slug}"
    return service_id[:49].rstrip("-")


def build_router(settings: Settings, store: ControlPlaneStore, auth_dependency) -> APIRouter:
    router = APIRouter(prefix="/deployments", tags=["deployments"])

    @router.post("")
    def create_deployment(req: DeployRequest, auth: AuthContext = Depends(auth_dependency)) -> dict:
        service_id = _service_id_for(auth.org_id, req.agent_name)

        env = dict(req.env)
        for name in req.secret_env_from_server:
            value = store.get_secret(auth.org_id, name) or os.environ.get(name)
            if not value:
                raise HTTPException(
                    status_code=400,
                    detail=f"no value for secret '{name}' -- set it with POST /api/secrets first",
                )
            env[name] = value

        try:
            result = deploy_service(
                project_id=settings.gcp_project_id,
                region=settings.gcp_region,
                service_id=service_id,
                image=req.image,
                port=req.port,
                env=env,
                labels={
                    "app": "rufo",
                    "managed-by": "rufo-control-plane",
                    "org": _org_fingerprint(auth.org_id),
                    "agent": _sanitize(req.agent_name, max_len=63) or "agent",
                },
                min_instances=req.min_instances,
                max_instances=req.max_instances,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"Cloud Run deploy failed: {exc}") from exc

        return store.upsert_deployment(
            org_id=auth.org_id,
            agent_name=req.agent_name,
            service_id=service_id,
            region=settings.gcp_region,
            image=req.image,
            uri=result.uri,
            status="running",
        )

    @router.get("")
    def list_deployments(auth: AuthContext = Depends(auth_dependency)) -> list[dict]:
        return store.list_deployments(auth.org_id)

    @router.get("/{deployment_id}")
    def get_deployment(deployment_id: str, auth: AuthContext = Depends(auth_dependency)) -> dict:
        deployment = store.get_deployment(auth.org_id, deployment_id)
        if deployment is None:
            raise HTTPException(status_code=404, detail="deployment not found")

        live = get_service(
            project_id=settings.gcp_project_id, region=settings.gcp_region, service_id=deployment["service_id"]
        )
        deployment["live_status"] = "not_found" if live is None else "ready"
        return deployment

    @router.delete("/{deployment_id}")
    def delete_deployment(deployment_id: str, auth: AuthContext = Depends(auth_dependency)) -> dict:
        deployment = store.get_deployment(auth.org_id, deployment_id)
        if deployment is None:
            raise HTTPException(status_code=404, detail="deployment not found")

        delete_service(
            project_id=settings.gcp_project_id, region=settings.gcp_region, service_id=deployment["service_id"]
        )
        store.delete_deployment(auth.org_id, deployment_id)
        return {"status": "deleted", "id": deployment_id}

    return router
