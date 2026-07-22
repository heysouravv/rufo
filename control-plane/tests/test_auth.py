import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from rufo_control_plane.auth import ClerkVerifier, frontend_api_from_publishable_key


def test_frontend_api_from_publishable_key():
    # "example.clerk.accounts.dev$" base64-encoded, matching Clerk's real format.
    import base64

    domain = "example.clerk.accounts.dev"
    encoded = base64.b64encode(f"{domain}$".encode()).decode().rstrip("=")
    pk = f"pk_test_{encoded}"
    assert frontend_api_from_publishable_key(pk) == domain


def test_frontend_api_from_publishable_key_rejects_malformed():
    with pytest.raises(ValueError):
        frontend_api_from_publishable_key("not-a-valid-key")


@pytest.fixture
def rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _make_token(private_key, kid: str, claims: dict) -> str:
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


def test_verify_accepts_valid_token_with_org(monkeypatch, rsa_keypair):
    private_key, public_key = rsa_keypair
    verifier = ClerkVerifier.__new__(ClerkVerifier)  # bypass __init__ (no network JWKS fetch)
    verifier.frontend_api = "example.clerk.accounts.dev"

    class FakeSigningKey:
        key = public_key

    class FakeJwkClient:
        def get_signing_key_from_jwt(self, token):
            return FakeSigningKey()

    verifier._jwk_client = FakeJwkClient()

    token = _make_token(
        private_key,
        kid="test-key",
        claims={"sub": "user_123", "org_id": "org_456", "org_role": "admin", "iat": int(time.time()), "exp": int(time.time()) + 300},
    )

    ctx = verifier.verify(token)
    assert ctx.user_id == "user_123"
    assert ctx.org_id == "org_456"
    assert ctx.org_role == "admin"


def test_verify_rejects_token_without_org(rsa_keypair):
    private_key, public_key = rsa_keypair
    verifier = ClerkVerifier.__new__(ClerkVerifier)
    verifier.frontend_api = "example.clerk.accounts.dev"

    class FakeSigningKey:
        key = public_key

    class FakeJwkClient:
        def get_signing_key_from_jwt(self, token):
            return FakeSigningKey()

    verifier._jwk_client = FakeJwkClient()

    token = _make_token(
        private_key,
        kid="test-key",
        claims={"sub": "user_123", "iat": int(time.time()), "exp": int(time.time()) + 300},
    )

    with pytest.raises(HTTPException) as exc_info:
        verifier.verify(token)
    assert exc_info.value.status_code == 403


def test_verify_rejects_expired_token(rsa_keypair):
    private_key, public_key = rsa_keypair
    verifier = ClerkVerifier.__new__(ClerkVerifier)
    verifier.frontend_api = "example.clerk.accounts.dev"

    class FakeSigningKey:
        key = public_key

    class FakeJwkClient:
        def get_signing_key_from_jwt(self, token):
            return FakeSigningKey()

    verifier._jwk_client = FakeJwkClient()

    token = _make_token(
        private_key,
        kid="test-key",
        claims={"sub": "user_123", "org_id": "org_456", "iat": int(time.time()) - 600, "exp": int(time.time()) - 300},
    )

    with pytest.raises(HTTPException) as exc_info:
        verifier.verify(token)
    assert exc_info.value.status_code == 401
