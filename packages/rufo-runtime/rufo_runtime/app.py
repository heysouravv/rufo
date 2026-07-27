from __future__ import annotations

import hmac
import json
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from langgraph.types import Command
from pydantic import BaseModel
from rufo_core.deploy_config import DeployConfig, EndpointConfig, load_deploy_config
from rufo_core.limiter import RateLimiter
from rufo_core.loader import load_policy
from rufo_core.models import Limit
from rufo_core import PolicyEngine
from rufo_sdk.errors import ApprovalRejected, ToolCallDenied

from rufo_runtime.agent_loader import load_agent
from rufo_runtime.settings import Settings
from rufo_runtime.store import Store

_agent_app = FastAPI(title="rufo-runtime")

_settings = Settings.from_env()
_policy_spec = load_policy(_settings.policy_path)
_engine = PolicyEngine(_policy_spec)
_agent = load_agent(_settings.agent_module, _settings.agent_entrypoint)
_store = Store(_settings.db_path)


def _require_admin_token(authorization: str | None = Header(default=None)) -> None:
    """Approvals/audit/resume have no auth of their own by default -- a real
    Cloud Run deploy proved an anonymous caller could read the full approval
    queue and self-approve a pending request via the public URL, completely
    defeating the human-in-the-loop point of `require_approval`. When the
    control plane deploys an agent it generates a random per-deployment
    RUFO_ADMIN_TOKEN and proxies these calls itself with it attached --
    local dev (no token configured) stays fully open, matching today's
    zero-friction `rufo deploy` loop.
    """
    if not _settings.admin_token:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing Bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(token, _settings.admin_token):
        raise HTTPException(status_code=401, detail="invalid admin token")


# Split so a hosted deploy can gate approval/audit management behind
# RUFO_ADMIN_TOKEN while the customer-facing surface (invoke, chat
# completions) stays open -- see _require_admin_token's docstring.
public_router = APIRouter()
management_router = APIRouter(dependencies=[Depends(_require_admin_token)])

if _settings.deploy_config_path:
    _deploy_config = load_deploy_config(_settings.deploy_config_path)
else:
    _deploy_config = DeployConfig(
        endpoint=EndpointConfig(model_name=Path(_settings.agent_module).stem)
    )

# HTTP-layer cap on /invoke and /v1/chat/completions -- see RateLimitConfig's
# docstring for why this exists independent of policy.yaml's own tool-level
# limits. In-memory/per-instance, same documented limitation as RateLimiter's
# use inside the policy engine itself.
_http_rate_limiter = RateLimiter()
_http_rate_limit = Limit(
    scope="http", max_calls=_deploy_config.rate_limit.max_calls, per_seconds=_deploy_config.rate_limit.per_seconds
)


def _enforce_rate_limit() -> None:
    if _deploy_config.rate_limit.max_calls <= 0:
        return
    if _http_rate_limiter.hit("http", _http_rate_limit):
        raise HTTPException(
            status_code=429,
            detail=(
                f"rate limit exceeded: {_http_rate_limit.max_calls} calls / "
                f"{_http_rate_limit.per_seconds}s -- configure a higher rate_limit in rufo.yaml if needed"
            ),
        )


class InvokeRequest(BaseModel):
    input: dict[str, Any]
    thread_id: str | None = None


class ResumeRequest(BaseModel):
    thread_id: str
    approval_id: str
    approved: bool
    reason: str | None = None


class DecideRequest(BaseModel):
    approved: bool
    reason: str | None = None


class CreateApprovalRequest(BaseModel):
    tool_name: str
    args: dict[str, Any]
    reason: str
    run_id: str


def _config_for(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def _handle_agent_result(thread_id: str, result: Any) -> dict:
    if isinstance(result, dict) and "__interrupt__" in result and result["__interrupt__"]:
        interrupt_obj = result["__interrupt__"][0]
        payload = interrupt_obj.value if hasattr(interrupt_obj, "value") else interrupt_obj
        payload = payload if isinstance(payload, dict) else {"reason": str(payload)}
        approval_id = _store.create_approval(
            run_id=thread_id,
            tool_name=payload.get("tool_name", "unknown"),
            args=payload.get("args", {}),
            reason=payload.get("reason", "approval required"),
        )
        return {
            "status": "paused",
            "thread_id": thread_id,
            "approval_id": approval_id,
            "tool_name": payload.get("tool_name"),
            "reason": payload.get("reason"),
        }

    _store.log_event(thread_id, "run_completed", None, {"output": _safe_jsonable(result)})
    return {"status": "completed", "thread_id": thread_id, "output": _safe_jsonable(result)}


def _safe_jsonable(value: Any) -> Any:
    from langchain_core.messages import BaseMessage

    if isinstance(value, BaseMessage):
        return {"type": value.type, "content": value.content}
    if isinstance(value, dict):
        return {k: _safe_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_jsonable(v) for v in value]

    try:
        import json

        json.dumps(value)
        return value
    except TypeError:
        return str(value)


@public_router.post("/invoke", dependencies=[Depends(_enforce_rate_limit)])
def invoke(req: InvokeRequest) -> dict:
    thread_id = req.thread_id or str(uuid.uuid4())
    _store.log_event(thread_id, "run_started", None, {"input": _safe_jsonable(req.input)})
    try:
        result = _agent.invoke(req.input, config=_config_for(thread_id))
    except Exception as exc:  # noqa: BLE001 - surface any agent/tool error to the caller
        _store.log_event(thread_id, "run_error", None, {"error": str(exc)})
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _handle_agent_result(thread_id, result)


@management_router.post("/resume")
def resume(req: ResumeRequest) -> dict:
    approval = _store.decide_approval(req.approval_id, req.approved, req.reason)
    if approval is None:
        raise HTTPException(status_code=404, detail="approval not found")

    resume_payload = {"approved": req.approved, "reason": req.reason}
    try:
        result = _agent.invoke(Command(resume=resume_payload), config=_config_for(req.thread_id))
    except Exception as exc:  # noqa: BLE001
        _store.log_event(req.thread_id, "run_error", None, {"error": str(exc)})
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _handle_agent_result(req.thread_id, result)


@management_router.post("/approvals")
def create_approval(req: CreateApprovalRequest) -> dict:
    approval_id = _store.create_approval(req.run_id, req.tool_name, req.args, req.reason)
    return {"id": approval_id}


@management_router.get("/approvals")
def list_approvals(status: str | None = None) -> list[dict]:
    return _store.list_approvals(status)


@management_router.get("/approvals/{approval_id}")
def get_approval(approval_id: str) -> dict:
    approval = _store.get_approval(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="approval not found")
    return approval


@management_router.post("/approvals/{approval_id}/decide")
def decide_approval(approval_id: str, req: DecideRequest) -> dict:
    approval = _store.decide_approval(approval_id, req.approved, req.reason)
    if approval is None:
        raise HTTPException(status_code=404, detail="approval not found")
    return approval


@management_router.get("/audit")
def list_audit(run_id: str | None = None, limit: int = 200) -> list[dict]:
    return _store.list_audit(run_id, limit)


@public_router.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# OpenAI-compatible endpoint surface (/v1/chat/completions, /v1/models).
#
# Every deploy gets this alongside /invoke+/resume. Tool calls are always
# executed server-side by the agent -- never surfaced as `tool_calls` in the
# response -- otherwise a caller could execute a tool Rufo never evaluated
# and policy enforcement would be bypassed entirely.
#
# HITL does not fit a synchronous request/response call: if a require_approval
# decision fires mid-run here, we fail loudly with a clear error rather than
# blocking indefinitely or silently downgrading to deny/allow.
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    stream: bool = False
    user: str | None = None


def _openai_error(status_code: int, message: str, error_type: str, code: str | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"message": message, "type": error_type, "code": code}},
    )


def _extract_usage(message: Any) -> dict:
    meta = getattr(message, "response_metadata", None) or {}
    usage = meta.get("token_usage") or meta.get("usage") or {}
    return {
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
    }


def _last_ai_message(result: Any):
    messages = result.get("messages", []) if isinstance(result, dict) else []
    for msg in reversed(messages):
        if getattr(msg, "type", None) == "ai" and getattr(msg, "content", None):
            return msg
    return None


def _stream_chat_completion(chunk_id: str, content: str, model: str):
    created = int(time.time())
    words = content.split(" ")
    for i, word in enumerate(words):
        piece = word if i == 0 else " " + word
        chunk = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(chunk)}\n\n"
    final_chunk = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(final_chunk)}\n\n"
    yield "data: [DONE]\n\n"


@public_router.get("/v1/models")
def list_models() -> dict:
    return {
        "object": "list",
        "data": [
            {
                "id": _deploy_config.endpoint.model_name,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "rufo",
            }
        ],
    }


@public_router.post("/v1/chat/completions", dependencies=[Depends(_enforce_rate_limit)])
def chat_completions(req: ChatCompletionRequest):
    if not _deploy_config.endpoint.enabled:
        return _openai_error(404, "endpoint mode is disabled for this agent", "not_found")

    thread_id = req.user or f"endpoint-{uuid.uuid4()}"
    input_messages = {"messages": [{"role": m.role, "content": m.content} for m in req.messages]}

    _store.log_event(thread_id, "run_started", None, {"input": _safe_jsonable(input_messages), "surface": "endpoint"})

    try:
        result = _agent.invoke(input_messages, config=_config_for(thread_id))
    except ToolCallDenied as exc:
        _store.log_event(thread_id, "run_denied", exc.tool_name, {"reason": exc.reason})
        return _openai_error(400, f"tool '{exc.tool_name}' denied by policy: {exc.reason}", "rufo_policy_denied", "denied")
    except ApprovalRejected as exc:
        _store.log_event(thread_id, "run_rejected", exc.tool_name, {"reason": exc.reason})
        return _openai_error(400, f"tool '{exc.tool_name}' rejected: {exc.reason}", "rufo_policy_rejected", "rejected")
    except Exception as exc:  # noqa: BLE001
        _store.log_event(thread_id, "run_error", None, {"error": str(exc)})
        return _openai_error(500, str(exc), "internal_error")

    if isinstance(result, dict) and result.get("__interrupt__"):
        interrupt_obj = result["__interrupt__"][0]
        payload = interrupt_obj.value if hasattr(interrupt_obj, "value") else interrupt_obj
        payload = payload if isinstance(payload, dict) else {}
        _store.log_event(thread_id, "endpoint_approval_blocked", payload.get("tool_name"), payload)
        return _openai_error(
            409,
            f"tool '{payload.get('tool_name')}' requires human approval, which the synchronous "
            "/v1/chat/completions endpoint cannot grant. Use this agent's /invoke + /resume "
            "workflow surface for approval-gated tools.",
            "rufo_approval_required",
            "approval_required",
        )

    final_message = _last_ai_message(result)
    content = final_message.content if final_message else ""
    _store.log_event(thread_id, "run_completed", None, {"output": content, "surface": "endpoint"})

    chunk_id = f"chatcmpl-{thread_id}"
    if req.stream and _deploy_config.endpoint.stream:
        return StreamingResponse(
            _stream_chat_completion(chunk_id, content, req.model), media_type="text/event-stream"
        )

    return {
        "id": chunk_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": req.model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": _extract_usage(final_message),
    }


_agent_app.include_router(public_router)
_agent_app.include_router(management_router)

# Deployed agents behind the shared Load Balancer (rufo.eldridgemorgan.com/agents/<org>/<agent>)
# need every route reachable at that prefix -- the LB forwards the full,
# unmodified path rather than rewriting it, so the app has to strip the
# prefix itself via a standard Starlette sub-app mount. Local dev and any
# deploy without RUFO_PUBLIC_PATH_PREFIX set keep serving everything
# (including approvals/audit) at root unchanged.
#
# management_router is included here too, not left off the public path --
# it's protected by _require_admin_token instead, so the control plane can
# reach it at the same branded URL it already has on file for this
# deployment (no separate internal path/backend needed).
if _settings.public_path_prefix:
    public_app = FastAPI(title="rufo-runtime")
    public_app.include_router(public_router)
    public_app.include_router(management_router)
    app = FastAPI(title="rufo-runtime")
    app.mount(_settings.public_path_prefix, public_app)
else:
    app = _agent_app
