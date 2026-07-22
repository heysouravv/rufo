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

    cors_origins: str = "http://localhost:8901,https://rufo.eldridgemorgan.com"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


def load_settings() -> Settings:
    return Settings()
