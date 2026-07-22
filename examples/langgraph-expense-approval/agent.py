"""Example LangGraph agent guarded by a Rufo policy.

Run it behind the Rufo runtime with:
    rufo deploy agent.py --policy policy.yaml
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
