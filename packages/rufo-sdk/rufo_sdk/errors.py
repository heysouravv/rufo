class ToolCallDenied(Exception):
    """Raised inside an agent run when the policy engine denies a tool call."""

    def __init__(self, tool_name: str, reason: str) -> None:
        self.tool_name = tool_name
        self.reason = reason
        super().__init__(f"tool call '{tool_name}' denied by policy: {reason}")


class ApprovalTimeout(Exception):
    """Raised when an approval-gated tool call is not decided within the timeout."""

    def __init__(self, tool_name: str, timeout_seconds: float) -> None:
        self.tool_name = tool_name
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"tool call '{tool_name}' was not approved within {timeout_seconds}s"
        )


class ApprovalRejected(Exception):
    """Raised when a human explicitly rejects a pending approval request."""

    def __init__(self, tool_name: str, reason: str | None) -> None:
        self.tool_name = tool_name
        self.reason = reason
        super().__init__(f"tool call '{tool_name}' rejected by approver: {reason or 'no reason given'}")
