from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from rufo_control_plane.auth import AuthContext


class ApproveDeviceRequest(BaseModel):
    user_code: str


def build_router(store, clerk_auth_dependency) -> APIRouter:
    """Unauthenticated start/poll endpoints (the device_code/user_code pair
    *is* the credential a CLI holds before it has any other token -- same
    trust model as GitHub/Google's device authorization grant), plus a
    Clerk-authenticated approve endpoint the dashboard's /device page calls.
    """
    router = APIRouter(prefix="/auth/device", tags=["auth"])

    @router.post("/start")
    def start_device_auth() -> dict:
        return store.create_device_code()

    @router.get("/poll")
    def poll_device_auth(device_code: str) -> dict:
        return store.poll_device_code(device_code)

    @router.post("/approve")
    def approve_device_auth(
        req: ApproveDeviceRequest, auth: AuthContext = Depends(clerk_auth_dependency)
    ) -> dict:
        approved = store.approve_device_code(req.user_code, auth.org_id, auth.user_id)
        if not approved:
            raise HTTPException(status_code=404, detail="code not found, already used, or expired")
        return {"status": "approved"}

    return router
