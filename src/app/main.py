from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import jwt
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.errors import problem
from app.api.schemas import ExecuteRequest, ExecuteResponse
from app.core.config import settings
from app.domain.ai_router import POLICIES, ProviderError, TerminalStatus, validate_candidate
from app.infra.database import create_database
from app.infra.providers import LiteLLMProvider
from app.infra.store import MemoryStore, PostgresStore

app = FastAPI(title="Edufurther AI Router", version="0.1.0")
if settings.database_url:
    engine, sessions = create_database(settings.database_url)
    store = PostgresStore(sessions)
else:
    engine = None
    store = MemoryStore()
provider = LiteLLMProvider()


@app.middleware("http")
async def request_context(request: Request, call_next: Any) -> JSONResponse:
    inbound = request.headers.get("X-Request-ID", "")
    request.state.request_id = (
        inbound if 1 <= len(inbound) <= 128 and inbound.isprintable() else f"req_{uuid4().hex}"
    )
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


async def authenticate(request: Request) -> tuple[str, set[str]] | None:
    if not settings.service_jwt_public_key:
        return None
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    if not token:
        return None
    try:
        claims = jwt.decode(
            token,
            settings.service_jwt_public_key,
            algorithms=[settings.service_jwt_algorithm],
            issuer=settings.service_issuer,
            audience=settings.service_audience,
            options={"require": ["iss", "sub", "aud", "iat", "nbf", "exp", "jti"]},
        )
        if not await store.claim_jti(
            str(claims["iss"]), str(claims["jti"]), datetime.fromtimestamp(claims["exp"], UTC)
        ):
            return None
        return str(claims["sub"]), set(str(claims.get("scope", "")).split())
    except jwt.PyJWTError:
        return None


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.service_name, "version": app.version}


@app.get("/ready", response_model=None)
async def ready(request: Request) -> dict[str, str] | JSONResponse:
    if isinstance(store, PostgresStore):
        try:
            await store.check_connection()
        except Exception:
            return problem(
                request,
                503,
                "Service Unavailable",
                "DEPENDENCY_NOT_READY",
                "Required dependency is unavailable",
                True,
            )
    return {"status": "ready", "service": settings.service_name}


@app.post("/api/v1/internal/ai/execute", response_model=ExecuteResponse)
async def execute(request: Request, body: ExecuteRequest) -> ExecuteResponse | JSONResponse:
    identity = await authenticate(request)
    if settings.environment != "development" and identity is None:
        return problem(request, 401, "Unauthorized", "UNAUTHORIZED", "Authentication failed")
    policy = POLICIES.get(body.task)
    if policy is None or body.product_id not in policy.allowed_products:
        return problem(request, 403, "Forbidden", "TASK_NOT_ALLOWED", "Task is not authorized")
    if request.headers.get("Idempotency-Key") != body.idempotency_key:
        return problem(
            request,
            400,
            "Bad Request",
            "IDEMPOTENCY_KEY_MISMATCH",
            "Idempotency-Key must match the request body",
        )
    raw = str(body.source_data).encode()
    if len(raw) > settings.max_source_bytes:
        return problem(
            request,
            422,
            "Unprocessable Content",
            "SOURCE_DATA_TOO_LARGE",
            "Source data exceeds the bounded excerpt limit",
        )
    payload = body.model_dump(mode="json")
    existing = await store.get(body.product_id, body.idempotency_key)
    if existing:
        if existing.digest != store.digest(payload):
            return problem(
                request,
                409,
                "Conflict",
                "IDEMPOTENCY_CONFLICT",
                "Key was used with a different request",
            )
        if existing.response is not None:
            return ExecuteResponse(**existing.response)
        return problem(
            request, 409, "Conflict", "REQUEST_IN_PROGRESS", "Request is already in progress"
        )
    request_id = request.state.request_id
    if isinstance(store, PostgresStore):
        existing = await store.claim(
            body.product_id,
            body.idempotency_key,
            payload,
            request_id,
            settings.model_policy_version,
        )
        if existing:
            if existing.digest != store.digest(payload):
                return problem(
                    request,
                    409,
                    "Conflict",
                    "IDEMPOTENCY_CONFLICT",
                    "Key was used with a different request",
                )
            if existing.response is not None:
                return ExecuteResponse(**existing.response)
            return problem(
                request, 409, "Conflict", "REQUEST_IN_PROGRESS", "Request is already in progress"
            )
    response: dict[str, Any]
    spent_usd = getattr(store, "spent_usd", 0.0)
    if spent_usd >= settings.daily_budget_usd:
        response = {
            "request_id": request_id,
            "status": TerminalStatus.BUDGET_EXHAUSTED,
            "output": None,
            "model_policy_version": settings.model_policy_version,
            "trace_reference": None,
        }
    else:
        output = None
        status = TerminalStatus.PROVIDER_UNAVAILABLE
        configured_policy = settings.routing_policy.get(body.task.value, {})
        configured_models = configured_policy.get("models", [])
        configured = (
            configured_models[0]
            if isinstance(configured_models, list) and configured_models
            else settings.task_models.get(body.task.value)
        )
        models = (
            (configured, settings.fallback_model)
            if configured
            else (settings.primary_model, settings.fallback_model)
        )
        for model in models:
            if not model:
                continue
            try:
                output = await provider.complete(
                    task=body.task,
                    source_data=body.source_data,
                    model=model,
                    max_tokens=policy.max_output_tokens,
                )
                if validate_candidate(body.task, output):
                    status = TerminalStatus.COMPLETED
                    break
                status = TerminalStatus.REVIEW
            except ProviderError as exc:
                status = TerminalStatus.PROVIDER_UNAVAILABLE
                if not exc.retryable:
                    break
        response = {
            "request_id": request_id,
            "status": status,
            "output": output if status == TerminalStatus.COMPLETED else None,
            "model_policy_version": settings.model_policy_version,
            "trace_reference": None,
        }
    await store.put(body.product_id, body.idempotency_key, payload, response)
    return ExecuteResponse(**response)
