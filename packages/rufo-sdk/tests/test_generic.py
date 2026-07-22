import pytest
from rufo_core import PolicyEngine
from rufo_core.loader import parse_policy

from rufo_sdk.approvals import ApprovalDecision
from rufo_sdk.errors import ApprovalRejected, ToolCallDenied
from rufo_sdk.generic import guard

POLICY = """
version: 1
defaults:
  action: allow
rules:
  - name: block_deletes
    match:
      tool: "delete_*"
    action: deny
  - name: approve_large_refunds
    match:
      tool: "refund_payment"
    action: require_approval
    conditions:
      - "args['amount'] > 100"
"""


class FakeApprovalBackend:
    def __init__(self, approved: bool):
        self.approved = approved
        self.requested = []

    def request_approval(self, tool_name, args, reason, run_id):
        self.requested.append((tool_name, args, reason, run_id))
        return "approval-1"

    def wait_for_decision(self, approval_id, timeout_seconds, poll_interval_seconds):
        return ApprovalDecision(approved=self.approved, reason="test")


def make_engine():
    return PolicyEngine(parse_policy(POLICY))


def test_allowed_call_passes_through():
    def search(query: str) -> str:
        return f"results for {query}"

    guarded = guard(search, engine=make_engine())
    assert guarded(query="cats") == "results for cats"


def test_denied_call_raises():
    def delete_customer(customer_id: str) -> None:
        pass

    guarded = guard(delete_customer, engine=make_engine())
    with pytest.raises(ToolCallDenied):
        guarded(customer_id="123")


def test_require_approval_approved_proceeds():
    def refund_payment(amount: float) -> str:
        return f"refunded {amount}"

    backend = FakeApprovalBackend(approved=True)
    guarded = guard(refund_payment, engine=make_engine(), approvals=backend)
    result = guarded(amount=500)
    assert result == "refunded 500"
    assert backend.requested[0][0] == "refund_payment"


def test_require_approval_rejected_raises():
    def refund_payment(amount: float) -> str:
        return f"refunded {amount}"

    backend = FakeApprovalBackend(approved=False)
    guarded = guard(refund_payment, engine=make_engine(), approvals=backend)
    with pytest.raises(ApprovalRejected):
        guarded(amount=500)
