from __future__ import annotations

from rufo_core import CallContext, PolicyEngine
from rufo_core.models import Decision


class PolicyMiddleware:
    """Thin, I/O-free wrapper around PolicyEngine shared by every framework adapter."""

    def __init__(self, engine: PolicyEngine, agent_name: str = "default") -> None:
        self.engine = engine
        self.agent_name = agent_name

    def check(self, tool_name: str, args: dict, run_id: str, extra: dict | None = None) -> Decision:
        context = CallContext(run_id=run_id, agent_name=self.agent_name, extra=extra or {})
        return self.engine.evaluate(tool_name, args, context)
