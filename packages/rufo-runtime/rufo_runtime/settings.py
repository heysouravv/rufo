from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    policy_path: str
    agent_module: str
    agent_entrypoint: str
    db_path: str
    deploy_config_path: str | None = None
    public_path_prefix: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        try:
            policy_path = os.environ["RUFO_POLICY_PATH"]
            agent_module = os.environ["RUFO_AGENT_MODULE"]
        except KeyError as exc:
            raise RuntimeError(
                "RUFO_POLICY_PATH and RUFO_AGENT_MODULE must be set (see `rufo deploy --help`)"
            ) from exc
        return cls(
            policy_path=policy_path,
            agent_module=agent_module,
            agent_entrypoint=os.environ.get("RUFO_AGENT_ENTRYPOINT", "agent"),
            db_path=os.environ.get("RUFO_DB_PATH", "./rufo_data/rufo.db"),
            deploy_config_path=os.environ.get("RUFO_DEPLOY_CONFIG_PATH"),
            public_path_prefix=os.environ.get("RUFO_PUBLIC_PATH_PREFIX") or None,
        )
