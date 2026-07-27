"""Real LangGraph agent (actual OpenAI tool-calling) guarded by Rufo.

Run behind the Rufo runtime with:
    rufo deploy agent.py --policy policy.yaml --port 8123
"""
from pathlib import Path

from dotenv import load_dotenv

# No fixed path: searches upward for a .env (convenient in the monorepo for
# local dev), but doesn't error in a source-based cloud deploy where this
# file is the only thing on disk and OPENAI_API_KEY arrives as a real env var.
load_dotenv()

from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from rufo_core import PolicyEngine
from rufo_core.loader import load_policy
from rufo_runtime.checkpointer import get_checkpointer
from rufo_sdk.langgraph_adapter import guard_langgraph_langchain_tool

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


tools = [
    check_account_balance,
    guard_langgraph_langchain_tool(wire_transfer, engine=_engine),
    guard_langgraph_langchain_tool(close_account, engine=_engine),
]

model = ChatOpenAI(model="gpt-4o-mini", temperature=0)

agent = create_react_agent(model, tools, checkpointer=get_checkpointer())
