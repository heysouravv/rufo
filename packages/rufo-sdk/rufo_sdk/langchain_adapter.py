from __future__ import annotations

from rufo_core import PolicyEngine

from rufo_sdk.approvals import ApprovalBackend
from rufo_sdk.generic import guard


def guard_langchain_tool(
    tool,
    *,
    engine: PolicyEngine,
    approvals: ApprovalBackend | None = None,
    agent_name: str = "default",
    run_id_fn=None,
    approval_timeout: float = 300.0,
):
    """Wrap a LangChain BaseTool/StructuredTool's underlying function with the
    policy engine. Requires `langchain-core` (install `rufo-sdk[langchain]`).

    Blocking: if the policy requires approval, this blocks the calling thread
    until a decision arrives. For graphs that can pause natively, prefer
    `guard_langgraph_tool`.
    """
    if getattr(tool, "func", None) is None:
        raise TypeError(
            "guard_langchain_tool requires a tool with a `.func` attribute "
            "(e.g. created via the @tool decorator or StructuredTool.from_function)"
        )

    tool.func = guard(
        tool.func,
        engine=engine,
        approvals=approvals,
        tool_name=tool.name,
        agent_name=agent_name,
        run_id_fn=run_id_fn,
        approval_timeout=approval_timeout,
    )
    return tool
