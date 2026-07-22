from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from rufo_control_plane.auth import ClerkVerifier, require_auth
from rufo_control_plane.routes.deployments import build_router
from rufo_control_plane.settings import load_settings
from rufo_control_plane.store import ControlPlaneStore

settings = load_settings()
store = ControlPlaneStore(settings.db_path)

app = FastAPI(title="rufo-control-plane")

# Dev-only: allow the local browser-based Clerk test harness to call this API
# directly from a different localhost port. Tighten to real dashboard origins
# before this is ever exposed beyond localhost.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8901"],
    allow_methods=["*"],
    allow_headers=["*"],
)

if settings.clerk_publishable_key:
    verifier = ClerkVerifier(settings.clerk_publishable_key)
    auth_dependency = require_auth(verifier)
    app.include_router(build_router(settings, store, auth_dependency))
else:
    # No Clerk key configured yet -- expose nothing rather than an unauthenticated API.
    @app.get("/deployments", tags=["deployments"])
    def _deployments_disabled() -> dict:
        return {
            "error": "CLERK_PUBLISHABLE_KEY is not set -- the deployments API is disabled until Clerk is configured."
        }


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "project": settings.gcp_project_id, "region": settings.gcp_region}
