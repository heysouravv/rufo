from __future__ import annotations

import base64
import time
from dataclasses import dataclass

import httpx
import jwt
from fastapi import Header, HTTPException
from jwt import PyJWKClient


@dataclass(frozen=True)
class AuthContext:
    user_id: str
    org_id: str
    org_role: str | None = None


def frontend_api_from_publishable_key(publishable_key: str) -> str:
    """Clerk publishable keys are `pk_{test,live}_<base64(frontend-api-domain + '$')>`."""
    if "_" not in publishable_key:
        raise ValueError(f"malformed Clerk publishable key: {publishable_key!r}")
    _, _, encoded = publishable_key.split("_", 2)
    padded = encoded + "=" * (-len(encoded) % 4)
    decoded = base64.b64decode(padded).decode("utf-8")
    return decoded.rstrip("$")


class ClerkVerifier:
    """Networkless verification of Clerk session JWTs via the instance's JWKS.

    Per Clerk's documented manual-verification approach: fetch
    https://<frontend-api-domain>/.well-known/jwks.json (cached by PyJWKClient),
    verify the RS256 signature, then check `exp`/`nbf` and pull `org_id`/`sub`
    out of the verified claims -- no per-request call to Clerk's backend API.
    """

    def __init__(self, publishable_key: str) -> None:
        self.frontend_api = frontend_api_from_publishable_key(publishable_key)
        self._jwk_client = PyJWKClient(f"https://{self.frontend_api}/.well-known/jwks.json")

    def verify(self, token: str) -> AuthContext:
        try:
            signing_key = self._jwk_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                options={"require": ["exp", "iat", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise HTTPException(status_code=401, detail=f"invalid session token: {exc}") from exc

        org_id = claims.get("org_id")
        if not org_id:
            raise HTTPException(
                status_code=403,
                detail="session token has no active organization -- select/create an org in Clerk first",
            )

        return AuthContext(user_id=claims["sub"], org_id=org_id, org_role=claims.get("org_role"))


def require_auth(verifier: ClerkVerifier):
    """FastAPI dependency factory: `Depends(require_auth(verifier))`."""

    def _dep(authorization: str | None = Header(default=None)) -> AuthContext:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="missing Bearer token")
        token = authorization.removeprefix("Bearer ").strip()
        return verifier.verify(token)

    return _dep
