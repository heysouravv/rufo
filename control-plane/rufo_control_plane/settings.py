from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_REPO_ROOT_ENV)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    gcp_project_id: str
    gcp_region: str = "us-central1"
    artifact_registry_repo: str = "rufo-agents"

    clerk_secret_key: str = ""
    clerk_publishable_key: str = Field(
        default="",
        validation_alias=AliasChoices("CLERK_PUBLISHABLE_KEY", "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY"),
    )

    db_path: str = "./rufo_control_plane_data/control_plane.db"

    # Postgres (Cloud SQL) -- when db_instance_connection_name is set, the
    # control plane uses PostgresControlPlaneStore instead of the SQLite
    # ControlPlaneStore; local dev without it keeps working exactly as before.
    db_instance_connection_name: str = ""
    db_user: str = "rufo"
    db_password: str = ""
    db_name: str = "rufo"
    encryption_key: str = Field(default="", validation_alias=AliasChoices("RUFO_CP_ENCRYPTION_KEY", "ENCRYPTION_KEY"))

    cors_origins: str = "http://localhost:8901,https://rufo.eldridgemorgan.com"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def agent_database_url(self) -> str | None:
        """DSN for a deployed agent's PostgresSaver, reaching Cloud SQL over
        the Unix socket Cloud Run mounts when `cloudsql_instances` is passed
        to deploy_service -- not a public-IP connection string."""
        if not self.db_instance_connection_name:
            return None
        return (
            f"postgresql://{self.db_user}:{self.db_password}@/{self.db_name}"
            f"?host=/cloudsql/{self.db_instance_connection_name}"
        )


def load_settings() -> Settings:
    return Settings()
