from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from rufo_core.errors import PolicyLoadError


@dataclass(frozen=True)
class AgentManifest:
    """Parsed rufo.toml: what the agent is and what it needs to build/run."""

    name: str
    entrypoint_module: str
    entrypoint_attr: str
    runtime: str
    dependencies: dict[str, str] = field(default_factory=dict)
    policy_file: str = "policy.yaml"


def load_manifest(path: str | Path) -> AgentManifest:
    path = Path(path)
    if not path.exists():
        raise PolicyLoadError(f"manifest file not found: {path}")
    with path.open("rb") as f:
        doc = tomllib.load(f)
    return parse_manifest(doc)


def parse_manifest(doc: dict) -> AgentManifest:
    agent = doc.get("agent")
    if not agent:
        raise PolicyLoadError("rufo.toml missing [agent] section")

    entrypoint = agent.get("entrypoint")
    if not entrypoint or ":" not in entrypoint:
        raise PolicyLoadError(
            "agent.entrypoint must be in 'module.py:attribute_name' form, "
            f"got {entrypoint!r}"
        )
    module_part, attr = entrypoint.split(":", 1)

    policy = doc.get("policy", {}) or {}

    return AgentManifest(
        name=agent.get("name", "agent"),
        entrypoint_module=module_part,
        entrypoint_attr=attr,
        runtime=agent.get("runtime", "python@3.11"),
        dependencies=dict(doc.get("dependencies", {}) or {}),
        policy_file=policy.get("file", "policy.yaml"),
    )
