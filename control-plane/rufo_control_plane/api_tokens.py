from __future__ import annotations

import hashlib
import secrets

# Avoids visually ambiguous characters (0/O, 1/I) since a human types this
# code by hand from the CLI's terminal output into the dashboard.
_USER_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

DEVICE_CODE_TTL_SECONDS = 600


def generate_device_code() -> str:
    return "ddc_" + secrets.token_urlsafe(32)


def generate_user_code() -> str:
    code = "".join(secrets.choice(_USER_CODE_ALPHABET) for _ in range(8))
    return f"{code[:4]}-{code[4:]}"


def generate_api_token() -> str:
    return "rufo_pat_" + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
