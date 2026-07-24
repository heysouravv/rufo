from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from rufo_control_plane.auth import AuthContext


class SetSecretRequest(BaseModel):
    name: str
    value: str


def build_router(store, auth_dependency) -> APIRouter:
    router = APIRouter(prefix="/secrets", tags=["secrets"])

    @router.post("")
    def set_secret(req: SetSecretRequest, auth: AuthContext = Depends(auth_dependency)) -> dict:
        """Org-scoped secret storage -- e.g. a customer's own OPENAI_API_KEY.
        Never returned back once set; deploys reference it by name only via
        `secret_env_from_server`.
        """
        store.set_secret(auth.org_id, req.name, req.value)
        return {"name": req.name, "status": "set"}

    @router.get("")
    def list_secrets(auth: AuthContext = Depends(auth_dependency)) -> list[str]:
        return store.list_secret_names(auth.org_id)

    @router.delete("/{name}")
    def delete_secret(name: str, auth: AuthContext = Depends(auth_dependency)) -> dict:
        deleted = store.delete_secret(auth.org_id, name)
        if not deleted:
            raise HTTPException(status_code=404, detail="secret not found")
        return {"name": name, "status": "deleted"}

    return router
