from __future__ import annotations

from pathlib import Path

POLICY_TEMPLATE = """\
version: 1
defaults:
  action: allow

rules:
  - name: block_deletes
    match:
      tool: "delete_*"
    action: deny
    reason: "destructive tools are never auto-allowed"

  - name: approve_large_refunds
    match:
      tool: "refund_payment"
    action: require_approval
    conditions:
      - "args['amount'] > 100"
    reason: "refunds over $100 require human approval"

limits:
  - scope: global
    max_calls: 100
    per_seconds: 60
"""

MANIFEST_TEMPLATE = """\
[agent]
name = "my-agent"
entrypoint = "agent.py:agent"
runtime = "python@3.11"

[dependencies]
langgraph = "^0.2"

[policy]
file = "policy.yaml"
"""

DEPLOY_CONFIG_TEMPLATE = """\
port: 8080
scaling:
  min_instances: 0
  max_instances: 5

endpoint:
  # This scaffold's example agent takes {amount, reason} input, not chat
  # `messages` -- leave endpoint mode off unless your agent's state accepts
  # the `{"messages": [...]}` shape (e.g. built with create_react_agent).
  enabled: false
  model_name: "my-agent"
  stream: true

mcp: []

env: []
"""

AGENT_TEMPLATE = '''\
"""Example LangGraph agent guarded by a Rufo policy.

Run it behind the Rufo runtime with:
    rufo deploy .
"""
from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import MemorySaver
from rufo_core import PolicyEngine
from rufo_core.loader import load_policy
from rufo_sdk.langgraph_adapter import guard_langgraph_tool
from typing import TypedDict

_engine = PolicyEngine(load_policy("policy.yaml"))


def refund_payment(amount: float, reason: str) -> str:
    return f"refunded ${amount} for: {reason}"


guarded_refund = guard_langgraph_tool(refund_payment, engine=_engine, tool_name="refund_payment")


class State(TypedDict):
    amount: float
    reason: str
    result: str


def refund_node(state: State) -> State:
    result = guarded_refund(amount=state["amount"], reason=state["reason"])
    return {**state, "result": result}


graph = StateGraph(State)
graph.add_node("refund", refund_node)
graph.add_edge(START, "refund")
graph.add_edge("refund", END)

agent = graph.compile(checkpointer=MemorySaver())
'''


def scaffold(target_dir: Path) -> list[Path]:
    target_dir.mkdir(parents=True, exist_ok=True)
    written = []
    files = (
        ("policy.yaml", POLICY_TEMPLATE),
        ("agent.py", AGENT_TEMPLATE),
        ("rufo.toml", MANIFEST_TEMPLATE),
        ("rufo.yaml", DEPLOY_CONFIG_TEMPLATE),
    )
    for name, content in files:
        path = target_dir / name
        if path.exists():
            continue
        path.write_text(content)
        written.append(path)
    return written
