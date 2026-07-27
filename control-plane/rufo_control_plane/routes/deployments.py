from __future__ import annotations

import hashlib
import os
import re
import secrets

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from google.cloud import run_v2

from rufo_control_plane.auth import AuthContext
from rufo_control_plane.gcp.cloud_run import delete_service, deploy_service, get_service
from rufo_control_plane.gcp.load_balancer import add_path_rule, ensure_backend_service, ensure_serverless_neg
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


def infra_env_for(settings: Settings) -> tuple[dict[str, str], list[str] | None]:
    """Extra env vars + Cloud SQL instances every deployed agent should get so
    `get_checkpointer()` picks up a durable PostgresSaver automatically --
    unused (empty env, no instances) until db_instance_connection_name is set."""
    database_url = settings.agent_database_url
    if database_url is None:
        return {}, None
    return {"DATABASE_URL": database_url}, [settings.db_instance_connection_name]


def path_prefix_for(org_slug_or_id: str, agent_name: str) -> str:
    """Org-scoped so two different orgs naming an agent the same thing don't
    collide: /agents/<org-slug>/<agent-slug>."""
    org_part = _sanitize(org_slug_or_id, max_len=40) or "org"
    agent_part = _sanitize(agent_name, max_len=40) or "agent"
    return f"/agents/{org_part}/{agent_part}"


def publish_agent_endpoint(settings: Settings, service_id: str, region: str, path_prefix: str) -> str:
    """Adds the deployed agent as one more path rule on the shared platform
    Load Balancer (same one rufo-control-plane/rufo-dashboard already sit
    behind) and returns the resulting branded public URL. Idempotent --
    safe to call again on every redeploy of the same agent."""
    neg_self_link = ensure_serverless_neg(settings.gcp_project_id, region, service_id)
    backend_service_name = ensure_backend_service(settings.gcp_project_id, service_id, neg_self_link)
    add_path_rule(
        project_id=settings.gcp_project_id,
        url_map_name=settings.url_map_name,
        path_matcher_name=settings.path_matcher_name,
        path_prefix=path_prefix,
        backend_service_name=backend_service_name,
    )
    return f"https://{settings.public_domain}{path_prefix}"


def _deployment_and_admin_token(store: ControlPlaneStore, org_id: str, deployment_id: str) -> tuple[dict, str]:
    deployment = store.get_deployment(org_id, deployment_id)
    if deployment is None:
        raise HTTPException(status_code=404, detail="deployment not found")
    token = store.get_deployment_admin_token(deployment["service_id"])
    if token is None:
        raise HTTPException(
            status_code=502, detail="no admin token on file for this deployment -- redeploy to generate one"
        )
    return deployment, token


def _forward(resp: httpx.Response):
    """Proxies the deployed agent's own status code/body back to the caller
    rather than always 200 -- e.g. a 404 for an unknown approval_id should
    reach the dashboard as a 404, not get swallowed into a generic success."""
    try:
        body = resp.json()
    except ValueError:
        body = {"detail": resp.text}
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=body.get("detail", body) if isinstance(body, dict) else body)
    return body


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

        infra_env, cloudsql_instances = infra_env_for(settings)
        env.update(infra_env)

        path_prefix = path_prefix_for(auth.org_slug or auth.org_id, req.agent_name)
        env["RUFO_PUBLIC_PATH_PREFIX"] = path_prefix

        # Generated fresh on every deploy (not just once) -- cheap, and
        # rotates the token automatically on redeploy rather than a stale
        # one persisting indefinitely. Keyed by service_id, not
        # deployment_id, since the deployment row doesn't exist until
        # upsert_deployment runs below.
        admin_token = secrets.token_urlsafe(32)
        env["RUFO_ADMIN_TOKEN"] = admin_token
        store.set_deployment_admin_token(service_id, admin_token)

        try:
            deploy_service(
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
                cloudsql_instances=cloudsql_instances,
                # Branded endpoint only works if the LB can reach this service
                # without an IAM invoker binding, same setup as the platform's
                # own control-plane/dashboard services -- direct .run.app
                # access is blocked at the network layer instead.
                ingress=run_v2.IngressTraffic.INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER,
                invoker_iam_disabled=True,
            )
            public_uri = publish_agent_endpoint(settings, service_id, settings.gcp_region, path_prefix)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"Cloud Run deploy failed: {exc}") from exc

        return store.upsert_deployment(
            org_id=auth.org_id,
            agent_name=req.agent_name,
            service_id=service_id,
            region=settings.gcp_region,
            image=req.image,
            uri=public_uri,
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

    # -- approval/audit management, proxied to the deployed agent's own
    #    otherwise-unauthenticated routes using the admin token generated at
    #    deploy time. This is how an org's Clerk/PAT-authenticated caller
    #    manages approvals for a hosted agent -- they never see the raw
    #    admin token themselves. --------------------------------------------

    @router.get("/{deployment_id}/approvals")
    def list_agent_approvals(
        deployment_id: str, status: str | None = None, auth: AuthContext = Depends(auth_dependency)
    ) -> list[dict]:
        deployment, token = _deployment_and_admin_token(store, auth.org_id, deployment_id)
        try:
            resp = httpx.get(
                f"{deployment['uri']}/approvals",
                params={"status": status} if status else {},
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"could not reach deployed agent: {exc}") from exc
        return _forward(resp)

    @router.get("/{deployment_id}/approvals/{approval_id}")
    def get_agent_approval(
        deployment_id: str, approval_id: str, auth: AuthContext = Depends(auth_dependency)
    ) -> dict:
        deployment, token = _deployment_and_admin_token(store, auth.org_id, deployment_id)
        try:
            resp = httpx.get(
                f"{deployment['uri']}/approvals/{approval_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"could not reach deployed agent: {exc}") from exc
        return _forward(resp)

    @router.post("/{deployment_id}/approvals/{approval_id}/decide")
    def decide_agent_approval(
        deployment_id: str,
        approval_id: str,
        body: dict = Body(...),
        auth: AuthContext = Depends(auth_dependency),
    ) -> dict:
        deployment, token = _deployment_and_admin_token(store, auth.org_id, deployment_id)
        try:
            resp = httpx.post(
                f"{deployment['uri']}/approvals/{approval_id}/decide",
                json=body,
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"could not reach deployed agent: {exc}") from exc
        return _forward(resp)

    @router.post("/{deployment_id}/resume")
    def resume_agent(deployment_id: str, body: dict = Body(...), auth: AuthContext = Depends(auth_dependency)) -> dict:
        deployment, token = _deployment_and_admin_token(store, auth.org_id, deployment_id)
        try:
            resp = httpx.post(
                f"{deployment['uri']}/resume",
                json=body,
                headers={"Authorization": f"Bearer {token}"},
                timeout=60,
            )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"could not reach deployed agent: {exc}") from exc
        return _forward(resp)

    @router.get("/{deployment_id}/audit")
    def list_agent_audit(
        deployment_id: str, run_id: str | None = None, limit: int = 200, auth: AuthContext = Depends(auth_dependency)
    ) -> list[dict]:
        deployment, token = _deployment_and_admin_token(store, auth.org_id, deployment_id)
        params = {"limit": limit}
        if run_id:
            params["run_id"] = run_id
        try:
            resp = httpx.get(
                f"{deployment['uri']}/audit",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"could not reach deployed agent: {exc}") from exc
        return _forward(resp)

    return router
