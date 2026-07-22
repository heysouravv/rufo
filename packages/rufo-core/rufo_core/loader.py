from __future__ import annotations

from pathlib import Path

import yaml

from rufo_core.errors import PolicyLoadError
from rufo_core.models import Action, Limit, PolicySpec, Rule


def load_policy(path: str | Path) -> PolicySpec:
    path = Path(path)
    if not path.exists():
        raise PolicyLoadError(f"policy file not found: {path}")
    return parse_policy(path.read_text())


def parse_policy(raw_yaml: str) -> PolicySpec:
    try:
        doc = yaml.safe_load(raw_yaml) or {}
    except yaml.YAMLError as exc:
        raise PolicyLoadError(f"invalid YAML: {exc}") from exc

    if not isinstance(doc, dict):
        raise PolicyLoadError("policy file must define a mapping at the top level")

    version = doc.get("version", 1)

    defaults = doc.get("defaults", {}) or {}
    try:
        default_action = Action(defaults.get("action", "allow"))
    except ValueError as exc:
        raise PolicyLoadError(f"invalid default action: {defaults.get('action')!r}") from exc

    rules: list[Rule] = []
    for i, raw_rule in enumerate(doc.get("rules", []) or []):
        rules.append(_parse_rule(raw_rule, index=i))

    limits: list[Limit] = []
    for i, raw_limit in enumerate(doc.get("limits", []) or []):
        limits.append(_parse_limit(raw_limit, index=i))

    return PolicySpec(
        version=version,
        default_action=default_action,
        rules=rules,
        limits=limits,
    )


def _parse_rule(raw: dict, index: int) -> Rule:
    if "match" not in raw or "tool" not in raw["match"]:
        raise PolicyLoadError(f"rule[{index}] missing match.tool")
    if "action" not in raw:
        raise PolicyLoadError(f"rule[{index}] missing action")
    try:
        action = Action(raw["action"])
    except ValueError as exc:
        raise PolicyLoadError(f"rule[{index}] invalid action: {raw['action']!r}") from exc

    return Rule(
        name=raw.get("name", f"rule_{index}"),
        tool_pattern=raw["match"]["tool"],
        action=action,
        conditions=list(raw.get("conditions", []) or []),
        reason=raw.get("reason"),
    )


def _parse_limit(raw: dict, index: int) -> Limit:
    for key in ("scope", "max_calls", "per_seconds"):
        if key not in raw:
            raise PolicyLoadError(f"limit[{index}] missing {key}")
    return Limit(
        scope=raw["scope"],
        max_calls=int(raw["max_calls"]),
        per_seconds=int(raw["per_seconds"]),
    )
