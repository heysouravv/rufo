from __future__ import annotations

import functools

from rufo_core import Action, PolicyEngine

from rufo_sdk._binding import bind_args
from rufo_sdk.approvals import ApprovalBackend, CLIApprovalBackend
from rufo_sdk.errors import ApprovalRejected, ToolCallDenied
from rufo_sdk.middleware import PolicyMiddleware


def guard(
    fn,
    *,
    engine: PolicyEngine,
    approvals: ApprovalBackend | None = None,
    tool_name: str | None = None,
    agent_name: str = "default",
    run_id_fn=None,
    approval_timeout: float = 300.0,
):
    """Wrap any callable (plain function, CrewAI tool, raw LangChain tool func)
    so every call is routed through the policy engine first.

    This is the lowest common denominator adapter: frameworks with a native
    pause primitive (LangGraph) should prefer `guard_langgraph_tool` instead,
    since this one blocks the calling thread while waiting for a decision.
    """
    middleware = PolicyMiddleware(engine, agent_name=agent_name)
    name = tool_name or getattr(fn, "__name__", "unknown_tool")
    backend = approvals or CLIApprovalBackend()

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        call_args = bind_args(fn, args, kwargs)
        run_id = run_id_fn() if run_id_fn else "local"
        decision = middleware.check(name, call_args, run_id=run_id)

        if decision.action == Action.DENY:
            raise ToolCallDenied(name, decision.reason)

        if decision.action == Action.REQUIRE_APPROVAL:
            approval_id = backend.request_approval(name, call_args, decision.reason, run_id)
            outcome = backend.wait_for_decision(
                approval_id, timeout_seconds=approval_timeout, poll_interval_seconds=2.0
            )
            if not outcome.approved:
                raise ApprovalRejected(name, outcome.reason)

        return fn(*args, **kwargs)

    return wrapper
