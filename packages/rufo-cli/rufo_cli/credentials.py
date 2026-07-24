from __future__ import annotations

import json
from pathlib import Path

CREDENTIALS_PATH = Path.home() / ".rufo" / "credentials.json"


def save_token(url: str, token: str) -> None:
    CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_PATH.write_text(json.dumps({"url": url, "token": token}))
    CREDENTIALS_PATH.chmod(0o600)


def load_token() -> dict | None:
    if not CREDENTIALS_PATH.exists():
        return None
    return json.loads(CREDENTIALS_PATH.read_text())
