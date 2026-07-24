from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class SecretCipher:
    """Application-level encryption for per-org secrets stored in Postgres.

    Uses a single master key (RUFO_CP_ENCRYPTION_KEY, a Cloud Run env var
    set exactly like CLERK_SECRET_KEY already is -- no new GCP service).
    Fernet (AES-128-CBC + HMAC) is deliberately simple: this protects
    secrets at rest in the database, not against a compromised control
    plane process, which would need the master key anyway.
    """

    def __init__(self, master_key: str) -> None:
        if not master_key:
            raise ValueError(
                "RUFO_CP_ENCRYPTION_KEY is not set -- generate one with "
                "`python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"`"
            )
        self._fernet = Fernet(master_key.encode())

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("secret could not be decrypted -- wrong master key?") from exc
