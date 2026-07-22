from rufo_core import Action, CallContext, PolicyEngine
from rufo_core.loader import parse_policy

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
    reason: "refunds over $100 require human approval"
limits:
  - scope: "tool:send_email"
    max_calls: 2
    per_seconds: 60
"""


def make_engine():
    return PolicyEngine(parse_policy(POLICY))


def ctx(run_id="run-1"):
    return CallContext(run_id=run_id, agent_name="test-agent")


def test_default_allow():
    engine = make_engine()
    decision = engine.evaluate("search_web", {}, ctx())
    assert decision.action == Action.ALLOW


def test_deny_glob_match():
    engine = make_engine()
    decision = engine.evaluate("delete_customer_record", {}, ctx())
    assert decision.action == Action.DENY
    assert decision.rule_matched == "block_deletes"


def test_conditional_require_approval():
    engine = make_engine()
    small = engine.evaluate("refund_payment", {"amount": 50}, ctx())
    large = engine.evaluate("refund_payment", {"amount": 500}, ctx())
    assert small.action == Action.ALLOW  # condition false -> falls through to default
    assert large.action == Action.REQUIRE_APPROVAL
    assert "100" in large.reason


def test_rate_limit_denies_after_threshold():
    engine = make_engine()
    c = ctx()
    first = engine.evaluate("send_email", {}, c)
    second = engine.evaluate("send_email", {}, c)
    third = engine.evaluate("send_email", {}, c)
    assert first.action == Action.ALLOW
    assert second.action == Action.ALLOW
    assert third.action == Action.DENY
    assert "rate limit" in third.reason
