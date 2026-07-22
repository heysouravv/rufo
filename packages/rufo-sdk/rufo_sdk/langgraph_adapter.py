from __future__ import annotations

import functools

from rufo_core import Action, PolicyEngine

from rufo_sdk._binding import bind_args
from rufo_sdk.errors import ApprovalRejected, ToolCallDenied
from rufo_sdk.middleware import PolicyMiddleware


def guard_langgraph_tool(
    fn,
    *,
    engine: PolicyEngine,
    tool_name: str | None = None,
    agent_name: str = "default",
    run_id_fn=None,
):
    """Wrap a tool function used inside a LangGraph node/ToolNode.

    Unlike `guard`/`guard_langchain_tool`, a REQUIRE_APPROVAL decision here
    calls LangGraph's native `interrupt()` -- the whole graph pauses and its
    state is persisted via the graph's checkpointer, so an operator can
    resume the run (via `Command(resume=...)`) minutes or days later without
    holding a thread open. This is the same primitive LangGraph documents for
    building HITL approval gates, applied generically through Rufo's policy
    engine so the same policy.yaml also governs LangChain/CrewAI/etc tools.

    Requires `langgraph` (install `rufo-sdk[langgraph]`).
    """
    from langgraph.types import interrupt as lg_interrupt

    middleware = PolicyMiddleware(engine, agent_name=agent_name)
    name = tool_name or getattr(fn, "__name__", "unknown_tool")

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        call_args = bind_args(fn, args, kwargs)
        run_id = run_id_fn() if run_id_fn else "local"
        decision = middleware.check(name, call_args, run_id=run_id)

        if decision.action == Action.DENY:
            raise ToolCallDenied(name, decision.reason)

        if decision.action == Action.REQUIRE_APPROVAL:
            resume_value = lg_interrupt(
                {
                    "type": "rufo_approval_request",
                    "tool_name": name,
                    "args": call_args,
                    "reason": decision.reason,
                }
            )
            approved = isinstance(resume_value, dict) and resume_value.get("approved") is True
            if not approved:
                reason = resume_value.get("reason") if isinstance(resume_value, dict) else None
                raise ApprovalRejected(name, reason)

        return fn(*args, **kwargs)

    return wrapper


def guard_langgraph_langchain_tool(
    tool,
    *,
    engine: PolicyEngine,
    agent_name: str = "default",
    run_id_fn=None,
):
    """Wrap a LangChain BaseTool/StructuredTool's underlying function with
    `guard_langgraph_tool`, preserving the tool's name/description/args_schema
    so a model bound to it (e.g. via `create_react_agent`) still sees the
    original schema. Mutates and returns `tool`.
    """
    if getattr(tool, "func", None) is None:
        raise TypeError(
            "guard_langgraph_langchain_tool requires a tool with a `.func` attribute "
            "(e.g. created via the @tool decorator or StructuredTool.from_function)"
        )
    tool.func = guard_langgraph_tool(
        tool.func,
        engine=engine,
        tool_name=tool.name,
        agent_name=agent_name,
        run_id_fn=run_id_fn,
    )
    return tool
