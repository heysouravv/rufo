from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Action(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


@dataclass(frozen=True)
class Rule:
    name: str
    tool_pattern: str
    action: Action
    conditions: list[str] = field(default_factory=list)
    reason: str | None = None


@dataclass(frozen=True)
class Limit:
    scope: str  # "global" or "tool:<name>"
    max_calls: int
    per_seconds: int


@dataclass(frozen=True)
class PolicySpec:
    version: int
    default_action: Action
    rules: list[Rule]
    limits: list[Limit]


@dataclass(frozen=True)
class CallContext:
    run_id: str
    agent_name: str = "default"
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    action: Action
    tool_name: str
    reason: str
    rule_matched: str | None = None

    @property
    def allowed(self) -> bool:
        return self.action == Action.ALLOW

    @property
    def denied(self) -> bool:
        return self.action == Action.DENY

    @property
    def needs_approval(self) -> bool:
        return self.action == Action.REQUIRE_APPROVAL
