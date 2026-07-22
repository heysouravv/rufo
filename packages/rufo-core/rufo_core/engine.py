from __future__ import annotations

from fnmatch import fnmatch

from simpleeval import EvalWithCompoundTypes

from rufo_core.limiter import RateLimiter
from rufo_core.models import Action, CallContext, Decision, PolicySpec, Rule


class PolicyEngine:
    """Evaluates a PolicySpec against individual tool-call requests.

    Framework adapters (LangChain callback, LangGraph node wrapper, etc.)
    all funnel through `evaluate()` so the same policy file produces the
    same decision regardless of which agent framework is calling it.
    """

    def __init__(self, spec: PolicySpec, limiter: RateLimiter | None = None) -> None:
        self.spec = spec
        self.limiter = limiter or RateLimiter()

    def evaluate(self, tool_name: str, args: dict, context: CallContext) -> Decision:
        decision = self._match_rules(tool_name, args, context)
        return self._apply_limits(tool_name, decision)

    def _match_rules(self, tool_name: str, args: dict, context: CallContext) -> Decision:
        for rule in self.spec.rules:
            if not fnmatch(tool_name, rule.tool_pattern):
                continue
            if rule.conditions and not self._conditions_hold(rule, args, context):
                continue
            return Decision(
                action=rule.action,
                tool_name=tool_name,
                reason=rule.reason or f"matched rule '{rule.name}'",
                rule_matched=rule.name,
            )
        return Decision(
            action=self.spec.default_action,
            tool_name=tool_name,
            reason="no rule matched; applied default action",
            rule_matched=None,
        )

    def _conditions_hold(self, rule: Rule, args: dict, context: CallContext) -> bool:
        names = {"args": args, "context": context.extra, "agent_name": context.agent_name}
        evaluator = EvalWithCompoundTypes(names=names)
        for expr in rule.conditions:
            if not bool(evaluator.eval(expr)):
                return False
        return True

    def _apply_limits(self, tool_name: str, decision: Decision) -> Decision:
        if decision.action == Action.DENY:
            return decision
        for limit in self.spec.limits:
            scope_key, applies = self._scope_matches(limit.scope, tool_name)
            if not applies:
                continue
            if self.limiter.hit(scope_key, limit):
                return Decision(
                    action=Action.DENY,
                    tool_name=tool_name,
                    reason=(
                        f"rate limit exceeded for scope '{limit.scope}' "
                        f"({limit.max_calls} calls / {limit.per_seconds}s)"
                    ),
                    rule_matched=None,
                )
        return decision

    @staticmethod
    def _scope_matches(scope: str, tool_name: str) -> tuple[str, bool]:
        if scope == "global":
            return "global", True
        if scope.startswith("tool:"):
            pattern = scope.split(":", 1)[1]
            return scope, fnmatch(tool_name, pattern)
        return scope, False
