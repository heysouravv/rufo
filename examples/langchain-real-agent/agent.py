"""Standalone LangChain (no LangGraph) agent guarded by the *same* Rufo
policy + approval queue used by the LangGraph example -- run against an
already-running `rufo-runtime` instance (its /approvals + /audit endpoints)
to prove one deployment's policy plane governs agents in either framework.

Usage:
    python agent.py "<prompt>" --run-id my-run --runtime http://localhost:8124
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from rufo_core import PolicyEngine
from rufo_core.loader import load_policy
from rufo_sdk.approvals import HTTPApprovalBackend
from rufo_sdk.errors import ApprovalRejected, ToolCallDenied
from rufo_sdk.langchain_adapter import guard_langchain_tool

_engine = PolicyEngine(load_policy(Path(__file__).parent / "policy.yaml"))


@tool
def check_account_balance(account_id: str) -> str:
    """Look up the current balance of an account by its id."""
    return f"account {account_id} has a balance of $4,300.00"


@tool
def wire_transfer(account_id: str, amount: float, payee: str) -> str:
    """Wire a dollar amount from an account to a named payee."""
    return f"wired ${amount} from account {account_id} to {payee}"


@tool
def close_account(account_id: str) -> str:
    """Permanently close an account."""
    return f"account {account_id} closed"


def build_tools(runtime_url: str, run_id: str):
    backend = HTTPApprovalBackend(runtime_url)
    return [
        check_account_balance,
        guard_langchain_tool(wire_transfer, engine=_engine, approvals=backend, run_id_fn=lambda: run_id),
        guard_langchain_tool(close_account, engine=_engine, approvals=backend, run_id_fn=lambda: run_id),
    ]


def run(prompt: str, run_id: str, runtime_url: str) -> None:
    tools = build_tools(runtime_url, run_id)
    tools_by_name = {t.name: t for t in tools}
    model = ChatOpenAI(model="gpt-4o-mini", temperature=0).bind_tools(tools)

    messages = [HumanMessage(content=prompt)]
    for _ in range(6):  # simple bounded loop, no framework-level recursion limit needed here
        ai_message = model.invoke(messages)
        messages.append(ai_message)

        if not ai_message.tool_calls:
            print(f"\n[final answer] {ai_message.content}")
            return

        for call in ai_message.tool_calls:
            tool_obj = tools_by_name[call["name"]]
            print(f"\n[agent] calling {call['name']}({call['args']}) -- waiting on Rufo policy...")
            try:
                result = tool_obj.invoke(call["args"])
            except ToolCallDenied as exc:
                result = f"DENIED: {exc.reason}"
            except ApprovalRejected as exc:
                result = f"REJECTED: {exc.reason}"
            print(f"[agent] {call['name']} -> {result}")
            messages.append(ToolMessage(content=str(result), tool_call_id=call["id"]))

    print("\n[agent] stopped after max iterations")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt")
    parser.add_argument("--run-id", default="langchain-run")
    parser.add_argument("--runtime", default="http://localhost:8124")
    args = parser.parse_args()
    run(args.prompt, args.run_id, args.runtime)
    sys.exit(0)
