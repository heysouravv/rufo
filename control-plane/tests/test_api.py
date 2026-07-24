import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from rufo_control_plane.auth import AuthContext, ClerkVerifier, require_auth


@pytest.fixture
def rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture
def client(rsa_keypair):
    private_key, public_key = rsa_keypair
    verifier = ClerkVerifier.__new__(ClerkVerifier)
    verifier.frontend_api = "example.clerk.accounts.dev"

    class FakeJwkClient:
        def get_signing_key_from_jwt(self, token):
            class Key:
                key = public_key

            return Key()

    verifier._jwk_client = FakeJwkClient()

    app = FastAPI()
    dep = require_auth(verifier)

    @app.get("/whoami")
    def whoami(auth: AuthContext = Depends(dep)) -> dict:
        return {"user_id": auth.user_id, "org_id": auth.org_id}

    app.state.private_key = private_key
    return TestClient(app)


def _token(private_key, **overrides):
    claims = {"sub": "user_1", "org_id": "org_1", "iat": int(time.time()), "exp": int(time.time()) + 300}
    claims.update(overrides)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "k1"})


def test_missing_authorization_header_returns_401(client):
    resp = client.get("/whoami")
    assert resp.status_code == 401


def test_malformed_authorization_header_returns_401(client):
    resp = client.get("/whoami", headers={"Authorization": "Basic abc123"})
    assert resp.status_code == 401


def test_valid_token_returns_org_scoped_identity(client):
    token = _token(client.app.state.private_key)
    resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == {"user_id": "user_1", "org_id": "org_1"}


def test_token_without_org_id_returns_403(client):
    token = _token(client.app.state.private_key, org_id=None)
    del_key = {k: v for k, v in {"sub": "user_1", "iat": int(time.time()), "exp": int(time.time()) + 300}.items()}
    token = jwt.encode(del_key, client.app.state.private_key, algorithm="RS256", headers={"kid": "k1"})
    resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_api_token_accepted_when_store_provided(rsa_keypair):
    """rufo_pat_ tokens (from `rufo login`) should authenticate the same
    routes as a Clerk JWT, without ever touching Clerk's JWKS."""
    private_key, public_key = rsa_keypair
    verifier = ClerkVerifier.__new__(ClerkVerifier)
    verifier.frontend_api = "example.clerk.accounts.dev"

    class FakeStore:
        def verify_api_token(self, token):
            return ("org_from_token", "user_from_token") if token == "rufo_pat_valid" else None

    app = FastAPI()
    dep = require_auth(verifier, FakeStore())

    @app.get("/whoami")
    def whoami(auth: AuthContext = Depends(dep)) -> dict:
        return {"user_id": auth.user_id, "org_id": auth.org_id}

    client = TestClient(app)

    resp = client.get("/whoami", headers={"Authorization": "Bearer rufo_pat_valid"})
    assert resp.status_code == 200
    assert resp.json() == {"user_id": "user_from_token", "org_id": "org_from_token"}

    resp = client.get("/whoami", headers={"Authorization": "Bearer rufo_pat_invalid"})
    assert resp.status_code == 401


def test_api_token_rejected_without_store(rsa_keypair):
    private_key, public_key = rsa_keypair
    verifier = ClerkVerifier.__new__(ClerkVerifier)
    verifier.frontend_api = "example.clerk.accounts.dev"

    app = FastAPI()
    dep = require_auth(verifier)  # no store -> API tokens not accepted

    @app.get("/whoami")
    def whoami(auth: AuthContext = Depends(dep)) -> dict:
        return {"user_id": auth.user_id}

    client = TestClient(app)
    resp = client.get("/whoami", headers={"Authorization": "Bearer rufo_pat_anything"})
    assert resp.status_code == 401
