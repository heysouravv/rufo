from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from rufo_core.errors import PolicyLoadError


@dataclass(frozen=True)
class ScalingConfig:
    min_instances: int = 0
    max_instances: int = 5


@dataclass(frozen=True)
class EndpointConfig:
    """Config for the OpenAI-compatible /v1/chat/completions surface.

    Every deploy gets both surfaces (workflow: /invoke+/resume, endpoint:
    /v1/chat/completions) automatically -- `enabled: false` is how a policy
    author opts a specific agent out of the synchronous surface entirely
    (e.g. because its whole point is an approval-gated workflow).
    """

    enabled: bool = True
    model_name: str = "agent"
    stream: bool = True


@dataclass(frozen=True)
class McpConnection:
    name: str
    url: str
    mode: str = "direct"  # "direct" (public endpoint) | "tunnel" (via rufo-connector)
    connector: str | None = None
    auth: str | None = None


@dataclass(frozen=True)
class DeployConfig:
    port: int = 8080
    scaling: ScalingConfig = field(default_factory=ScalingConfig)
    endpoint: EndpointConfig = field(default_factory=EndpointConfig)
    mcp: list[McpConnection] = field(default_factory=list)
    env: list[str] = field(default_factory=list)


def load_deploy_config(path: str | Path) -> DeployConfig:
    path = Path(path)
    if not path.exists():
        raise PolicyLoadError(f"deploy config not found: {path}")
    try:
        doc = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise PolicyLoadError(f"invalid YAML in {path}: {exc}") from exc
    return parse_deploy_config(doc)


def parse_deploy_config(doc: dict) -> DeployConfig:
    scaling_raw = doc.get("scaling", {}) or {}
    endpoint_raw = doc.get("endpoint", {}) or {}
    mcp_raw = doc.get("mcp", []) or []

    for i, m in enumerate(mcp_raw):
        if "name" not in m or "url" not in m:
            raise PolicyLoadError(f"mcp[{i}] missing required 'name'/'url'")

    return DeployConfig(
        port=int(doc.get("port", 8080)),
        scaling=ScalingConfig(
            min_instances=int(scaling_raw.get("min_instances", 0)),
            max_instances=int(scaling_raw.get("max_instances", 5)),
        ),
        endpoint=EndpointConfig(
            enabled=bool(endpoint_raw.get("enabled", True)),
            model_name=endpoint_raw.get("model_name", "agent"),
            stream=bool(endpoint_raw.get("stream", True)),
        ),
        mcp=[
            McpConnection(
                name=m["name"],
                url=m["url"],
                mode=m.get("mode", "direct"),
                connector=m.get("connector"),
                auth=m.get("auth"),
            )
            for m in mcp_raw
        ],
        env=list(doc.get("env", []) or []),
    )
