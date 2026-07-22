# Rufo

An open-source deployment platform for AI agents built on LangChain, LangGraph,
CrewAI, or plain Python: policy-as-code authorization, human-in-the-loop
approval gates, and an audit trail — enforced at the point agents actually run
in production, not just observed after the fact.

## Why

Agent observability tools (LangSmith, Langfuse, Arize Phoenix) trace and
evaluate runs after they happen. LangGraph ships a genuinely good
human-in-the-loop primitive (`interrupt()` + checkpointer), but it's
LangGraph-specific — it doesn't help a CrewAI, AutoGen, or raw LangChain
agent. Rufo is a policy engine plus a deployment runtime that sits underneath
any of these frameworks: one `policy.yaml` governs which tools an agent may
call, which calls require a human's sign-off, and how every decision gets
logged — and it's Rufo, not you, that hosts and serves the agent.

## Architecture

```
packages/
  rufo-core     policy engine: YAML rules -> allow / deny / require_approval
  rufo-sdk      framework adapters (LangGraph native interrupt(), LangChain, generic)
  rufo-runtime  FastAPI service that hosts your agent + approval queue + audit log
  rufo-cli      `rufo init` / `rufo deploy` / `rufo approvals` / `rufo logs`
examples/
  langgraph-expense-approval   hand-driven LangGraph example (no LLM, deterministic)
  langgraph-real-agent         real GPT-4o-mini tool-calling agent via create_react_agent
  langchain-real-agent         standalone LangChain agent (no LangGraph) sharing the
                                same runtime's approval queue over HTTP
```

**Verified live** (both examples require `OPENAI_API_KEY` in `.env` at the repo
root): a `create_react_agent` LangGraph agent correctly called an allowed tool,
attempted a policy-gated `wire_transfer`, paused via native `interrupt()`,
resumed after `rufo approvals decide`, and produced the correct final answer;
a separate `close_account` call was denied outright. Simultaneously, a
standalone LangChain agent (different process, no LangGraph at all) blocked
on the *same running runtime's* approval queue via `HTTPApprovalBackend` and
resumed the moment it was approved — one policy file and one runtime governing
two different frameworks at once.

- **rufo-core** has no framework dependencies. It evaluates `(tool_name, args,
  context)` against a declarative policy spec and returns a decision.
- **rufo-sdk** wraps individual tool functions so calls are routed through the
  policy engine before they execute. The LangGraph adapter uses LangGraph's
  own `interrupt()` primitive, so an approval-gated run pauses and persists
  via LangGraph's checkpointer exactly the way LangGraph's own docs recommend
  — Rufo just makes the same policy apply consistently across frameworks.
  The generic/LangChain adapters block on a synchronous approval poll instead
  (frameworks without a native pause primitive).
- **rufo-runtime** is the deployment target: `rufo deploy` boots a server that
  loads your compiled LangGraph graph and exposes **two surfaces from the same
  policy file**:
  - **Workflow** (`/invoke`, `/resume`, `/approvals`, `/audit`) — LangGraph's
    native interrupt/resume lifecycle, for long-running approval-gated agents.
  - **Endpoint** (`/v1/chat/completions`, `/v1/models`) — an OpenAI-compatible
    surface so any existing OpenAI-client code (`ChatOpenAI(base_url=...)`,
    Open WebUI, etc.) can call the *whole guarded agent* as if it were a model,
    with zero new integration code. Tool calls are always executed server-side
    and never leaked to the caller as `tool_calls` — otherwise policy
    enforcement would be bypassed. Since a synchronous chat completion can't
    pause indefinitely for a human, a `require_approval` decision hit through
    this surface fails immediately with a clear `409 rufo_approval_required`
    error rather than hanging or silently changing behavior — use the
    workflow surface for agents whose policy needs approval gates.

## Config: rufo.toml + rufo.yaml

`rufo deploy <dir>` (or a bare `rufo deploy .`) reads two manifests if present:

```toml
# rufo.toml -- what the agent needs to build/run
[agent]
name = "my-agent"
entrypoint = "agent.py:agent"
runtime = "python@3.11"

[dependencies]
langgraph = "^0.2"

[policy]
file = "policy.yaml"
```

```yaml
# rufo.yaml -- how it's deployed/networked
port: 8080
scaling:
  min_instances: 0
  max_instances: 5

endpoint:
  enabled: true              # set false if your agent's state isn't {"messages": [...]}
  model_name: "my-agent"
  stream: true

mcp:
  - name: crm
    url: "https://mcp.internal.acme.com"
    mode: tunnel             # "direct" for public MCP endpoints, "tunnel" for
    connector: "acme-1"      # private ones via the (planned) rufo-connector

env:
  - OPENAI_API_KEY
```

`rufo deploy agent.py --policy policy.yaml` (no manifest) still works exactly
as before for a bare `.py` file with no `rufo.toml` in its directory.

## Quickstart

```bash
uv sync --all-packages

cd examples/langgraph-expense-approval   # or: rufo init somewhere-new
uv run --package rufo-cli rufo deploy agent.py --policy policy.yaml --port 8123
```

In another shell:

```bash
# small refund -- auto-allowed
curl -s localhost:8123/invoke -d '{"input": {"amount": 50, "reason": "supplies"}, "thread_id": "t1"}'

# large refund -- pauses for approval (LangGraph interrupt(), persisted)
curl -s localhost:8123/invoke -d '{"input": {"amount": 500, "reason": "vendor"}, "thread_id": "t2"}'

uv run --package rufo-cli rufo approvals list --url http://localhost:8123
uv run --package rufo-cli rufo approvals decide <approval_id> --approve --url http://localhost:8123

curl -s -X POST localhost:8123/resume -d '{"thread_id": "t2", "approval_id": "<id>", "approved": true}'
uv run --package rufo-cli rufo logs --run-id t2 --url http://localhost:8123
```

### Real LLM agent (LangGraph + LangChain together)

Requires `OPENAI_API_KEY` set in a `.env` file at the repo root.

```bash
cd examples/langgraph-real-agent   # has rufo.toml + rufo.yaml already
uv run --package rufo-cli rufo deploy . --port 8124

# workflow surface, in another shell
curl -s localhost:8124/invoke -d '{"input": {"messages": [{"role": "user", "content": "Check the balance of account acc-123, then wire $500 from it to payee Acme Corp for invoice 88."}]}, "thread_id": "t1"}'
uv run --package rufo-cli rufo approvals list --url http://localhost:8124
uv run --package rufo-cli rufo approvals decide <approval_id> --approve --url http://localhost:8124
curl -s -X POST localhost:8124/resume -d '{"thread_id": "t1", "approval_id": "<id>", "approved": true}'

# in a third shell, a standalone LangChain agent sharing the SAME runtime's approval queue
cd examples/langchain-real-agent
python agent.py "Wire 250 dollars from account acc-999 to payee Globex Inc." --run-id t2 --runtime http://localhost:8124
# it blocks -- approve it from shell two:
uv run --package rufo-cli rufo approvals list --url http://localhost:8124
uv run --package rufo-cli rufo approvals decide <approval_id> --approve --url http://localhost:8124
```

### OpenAI-compatible endpoint surface

Same agent, same running runtime, no extra config beyond `endpoint.enabled: true`
in `rufo.yaml` (already set for `langgraph-real-agent`):

```bash
curl -s localhost:8124/v1/models

curl -s localhost:8124/v1/chat/completions -d '{
  "model": "langgraph-real-agent",
  "messages": [{"role": "user", "content": "What is the balance of account acc-777?"}]
}'
# -> a normal OpenAI chat.completion object; the balance-check tool ran server-side

curl -s localhost:8124/v1/chat/completions -d '{
  "model": "langgraph-real-agent",
  "messages": [{"role": "user", "content": "Wire $500 from acc-777 to Acme Corp."}]
}'
# -> 409 rufo_approval_required: this surface can't grant approval, fails fast
#    instead of hanging; use /invoke + /resume for this tool instead
```

## Policy spec

```yaml
version: 1
defaults:
  action: allow

rules:
  - name: block_deletes
    match:
      tool: "delete_*"      # glob-matched against the tool name
    action: deny

  - name: approve_large_refunds
    match:
      tool: "refund_payment"
    action: require_approval
    conditions:
      - "args['amount'] > 100"   # simpleeval expression over the call's args
    reason: "refunds over $100 require human approval"

limits:
  - scope: global               # or "tool:<glob>"
    max_calls: 100
    per_seconds: 60
```

## Status / roadmap

This is a working MVP, not a finished product:

- **Persistence** is SQLite (approvals/audit) and LangGraph's in-memory
  checkpointer — fine for one runtime instance, not for multi-instance
  deployments. A Postgres/Redis backend is the natural next step.
- **Docker/container/sandbox packaging** isn't wired up yet — `rufo deploy`
  runs the runtime as a foreground process today. Hosted deploy is planned on
  Google Cloud Run (gVisor-isolated by default), which will force a durable
  Postgres/Redis LangGraph checkpointer in place of `MemorySaver`.
- **Streaming is word-chunked after the full response completes**, not true
  per-token streaming from the model — spec-compliant SSE, but not real
  incremental generation yet. A `stream_mode="messages"` based implementation
  is the natural upgrade.
- **`rufo-connector`** (the planned OSS tunnel agent for private/internal MCP
  servers) doesn't exist yet — `rufo.yaml`'s `mcp.mode: tunnel` is schema-only
  for now.
- **CrewAI / AutoGen / OpenAI Agents SDK adapters** aren't written yet; the
  generic `guard()` decorator works with any plain callable, but a
  framework-specific adapter (mirroring how tool registration works in each)
  is worth adding once there's real usage pressure.
- **No web dashboard** — approvals/audit are CLI + REST only for now.
- **No Clerk/multi-tenant auth** — the runtime has no auth at all yet; fine
  for local/self-hosted single-tenant use, required before any hosted offering.
