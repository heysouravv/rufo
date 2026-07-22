from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

import requests

from rufo_sdk.errors import ApprovalTimeout


@dataclass(frozen=True)
class ApprovalDecision:
    approved: bool
    reason: str | None = None


class ApprovalBackend(Protocol):
    def request_approval(self, tool_name: str, args: dict, reason: str, run_id: str) -> str:
        """Create a pending approval and return its id."""
        ...

    def wait_for_decision(
        self, approval_id: str, timeout_seconds: float, poll_interval_seconds: float
    ) -> ApprovalDecision:
        """Block until a human decides, or raise ApprovalTimeout."""
        ...


class HTTPApprovalBackend:
    """Talks to a running rufo-runtime instance's approval queue."""

    def __init__(self, base_url: str, session: requests.Session | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()

    def request_approval(self, tool_name: str, args: dict, reason: str, run_id: str) -> str:
        resp = self.session.post(
            f"{self.base_url}/approvals",
            json={"tool_name": tool_name, "args": args, "reason": reason, "run_id": run_id},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()["id"]

    def wait_for_decision(
        self, approval_id: str, timeout_seconds: float = 300.0, poll_interval_seconds: float = 2.0
    ) -> ApprovalDecision:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            resp = self.session.get(f"{self.base_url}/approvals/{approval_id}", timeout=10)
            resp.raise_for_status()
            body = resp.json()
            if body["status"] != "pending":
                return ApprovalDecision(
                    approved=body["status"] == "approved",
                    reason=body.get("decision_reason"),
                )
            time.sleep(poll_interval_seconds)
        raise ApprovalTimeout(approval_id, timeout_seconds)


class CLIApprovalBackend:
    """Prompts on stdin for a decision. Useful for local dev without a runtime server."""

    def request_approval(self, tool_name: str, args: dict, reason: str, run_id: str) -> str:
        print(f"\n[rufo] approval required for tool '{tool_name}'")
        print(f"[rufo] reason: {reason}")
        print(f"[rufo] args: {args}")
        return f"cli-{tool_name}-{time.time_ns()}"

    def wait_for_decision(
        self, approval_id: str, timeout_seconds: float = 300.0, poll_interval_seconds: float = 2.0
    ) -> ApprovalDecision:
        answer = input("[rufo] approve? [y/N]: ").strip().lower()
        return ApprovalDecision(approved=answer == "y")
