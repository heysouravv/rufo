from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from rufo_control_plane.auth import ClerkVerifier, require_auth
from rufo_control_plane.routes.costs import build_router as build_costs_router
from rufo_control_plane.routes.deployments import build_router
from rufo_control_plane.routes.secrets import build_router as build_secrets_router
from rufo_control_plane.settings import load_settings
from rufo_control_plane.store import create_store

settings = load_settings()
store = create_store(settings)

app = FastAPI(title="rufo-control-plane")

# The dashboard talks to this API same-origin in production (both live behind
# rufo.eldridgemorgan.com via the Load Balancer's path routing), so CORS only
# matters for local dev -- the standalone Clerk test harness on :8901, or a
# locally-run dashboard on a different port. Configurable via RUFO_CP_CORS_ORIGINS
# for any origin not covered by the default.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mounted under /api so the Load Balancer's URL map can route with a single
# path-matcher rule (/api/* -> control-plane, everything else -> dashboard)
# instead of enumerating every route.
if settings.clerk_publishable_key:
    verifier = ClerkVerifier(settings.clerk_publishable_key)
    auth_dependency = require_auth(verifier)
    app.include_router(build_router(settings, store, auth_dependency), prefix="/api")
    app.include_router(build_costs_router(settings, store, auth_dependency), prefix="/api")
    app.include_router(build_secrets_router(store, auth_dependency), prefix="/api")
else:
    # No Clerk key configured yet -- expose nothing rather than an unauthenticated API.
    @app.get("/api/deployments", tags=["deployments"])
    def _deployments_disabled() -> dict:
        return {
            "error": "CLERK_PUBLISHABLE_KEY is not set -- the deployments API is disabled until Clerk is configured."
        }


@app.get("/api/healthz")
def healthz() -> dict:
    return {"status": "ok", "project": settings.gcp_project_id, "region": settings.gcp_region}
