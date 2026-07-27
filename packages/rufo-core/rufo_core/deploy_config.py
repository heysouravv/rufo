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
class RateLimitConfig:
    """HTTP-layer cap on the two costly public endpoints (/invoke,
    /v1/chat/completions) -- independent of policy.yaml's own tool-level
    limits, which only fire on a guarded tool *call* and never cap a plain
    LLM chat with no tool use. Once an agent is publicly reachable (no GCP
    auth needed), that gap is a real, uncapped cost-DoS vector against
    whichever OPENAI_API_KEY the org configured -- caught in a pre-UAT
    security audit. On by default for every deploy; set max_calls: 0 to
    disable (not recommended for a publicly reachable agent).
    """

    max_calls: int = 60
    per_seconds: int = 60


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
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
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
    rate_limit_raw = doc.get("rate_limit", {}) or {}
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
        rate_limit=RateLimitConfig(
            max_calls=int(rate_limit_raw.get("max_calls", 60)),
            per_seconds=int(rate_limit_raw.get("per_seconds", 60)),
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
