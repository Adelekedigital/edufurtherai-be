from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import uuid4

import jwt
from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.errors import problem
from app.api.schemas import ExecuteRequest, ExecuteResponse
from app.core.config import settings
from app.domain.ai_router import (
    POLICIES,
    CompletionResult,
    ProviderError,
    TerminalStatus,
    validate_candidate,
)
from app.infra.database import create_database
from app.infra.observability import LangfuseTracer
from app.infra.providers import LiteLLMProvider
from app.infra.store import MemoryStore, PostgresStore

app = FastAPI(title="Edufurther AI Router", version="0.1.0")
bearer_scheme = HTTPBearer(auto_error=False)
if settings.database_url:
    engine, sessions = create_database(settings.database_url)
    store = PostgresStore(sessions)
else:
    engine = None
    store = MemoryStore()
provider = LiteLLMProvider()
tracer = LangfuseTracer()


def ordered_models(task: str) -> list[str]:
    configured_policy = settings.routing_policy.get(task, {})
    configured_models = (
        configured_policy.models
        if hasattr(configured_policy, "models")
        else configured_policy.get("models", [])
    )
    if isinstance(configured_models, list) and configured_models:
        candidates = [model for model in configured_models if isinstance(model, str)]
    else:
        task_model = settings.task_models.get(task)
        candidates = [task_model] if isinstance(task_model, str) else []
        if not candidates:
            candidates = [settings.primary_model]
    candidates.append(settings.fallback_model)
    return list(dict.fromkeys(model for model in candidates if model))


@app.middleware("http")
async def request_context(request: Request, call_next: Any) -> JSONResponse:
    inbound = request.headers.get("X-Request-ID", "")
    request.state.request_id = (
        inbound if 1 <= len(inbound) <= 128 and inbound.isprintable() else f"req_{uuid4().hex}"
    )
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


async def authenticate(
    request: Request, product_id: str, token: str | None = None
) -> tuple[str, set[str]] | None:
    caller = settings.service_callers.get(product_id)
    if caller is None or not caller.keys:
        return None
    authorization = f"Bearer {token}" if token else request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return None
    token = authorization.removeprefix("Bearer ")
    try:
        header = jwt.get_unverified_header(token)
        kid = str(header.get("kid", ""))
        key = caller.keys.get(kid) if kid else None
        if not key:
            return None
        claims = jwt.decode(
            token,
            key,
            algorithms=[settings.service_jwt_algorithm],
            options={
                "require": ["iss", "sub", "aud", "iat", "nbf", "exp", "jti"],
                "verify_aud": False,
            },
        )
        audiences = claims["aud"] if isinstance(claims["aud"], list) else [claims["aud"]]
        if (
            str(claims["sub"]) != caller.subject
            or str(claims["iss"]) != caller.issuer
            or caller.audience not in {str(value) for value in audiences}
        ):
            return None
        scopes = set(str(claims.get("scope", "")).split())
        if caller.scopes and not caller.scopes.issubset(scopes):
            return None
        if not await store.claim_jti(
            str(claims["iss"]), str(claims["jti"]), datetime.fromtimestamp(claims["exp"], UTC)
        ):
            return None
        return str(claims["sub"]), scopes
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
async def execute(
    request: Request,
    body: ExecuteRequest,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ExecuteResponse | JSONResponse:
    identity = await authenticate(
        request,
        body.product_id,
        credentials.credentials if credentials else None,
    )
    development_bypass = (
        settings.environment == "development" and settings.allow_unauthenticated_development
    )
    if identity is None and not development_bypass:
        return problem(request, 401, "Unauthorized", "UNAUTHORIZED", "Authentication failed")
    if settings.service_jwt_required_scope and (
        identity is not None and settings.service_jwt_required_scope not in identity[1]
    ):
        return problem(request, 403, "Forbidden", "INSUFFICIENT_SCOPE", "Required scope is missing")
    policy = POLICIES.get(body.task)
    if policy is None or body.product_id not in policy.allowed_products:
        return problem(request, 403, "Forbidden", "TASK_NOT_ALLOWED", "Task is not authorized")
    if idempotency_key != body.idempotency_key:
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
    if not await store.allow_rate_limit(
        body.product_id, body.task.value, settings.rate_limit_for(body.product_id, body.task.value)
    ):
        return problem(
            request,
            429,
            "Too Many Requests",
            "RATE_LIMITED",
            "Request rate limit exceeded",
            True,
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
    trace = tracer.start(
        request_id=request_id,
        product_id=body.product_id,
        feature_id=body.feature_id,
        task=body.task.value,
        policy_version=settings.model_policy_version,
    )
    response: dict[str, Any]
    if await store.budget_exhausted(
        body.product_id, body.task.value, settings.budget_for(body.product_id, body.task.value)
    ):
        response = {
            "request_id": request_id,
            "status": TerminalStatus.BUDGET_EXHAUSTED,
            "output": None,
            "model_policy_version": settings.model_policy_version,
            "trace_reference": trace.reference,
        }
    else:
        output = None
        status = TerminalStatus.PROVIDER_UNAVAILABLE
        for attempt, model in enumerate(ordered_models(body.task.value), start=1):
            if not model:
                continue
            generation = trace.start_generation(model, attempt)
            try:
                result = await provider.complete(
                    task=body.task,
                    source_data=body.source_data,
                    model=model,
                    max_tokens=policy.max_output_tokens,
                )
                if isinstance(result, CompletionResult):
                    output = result.output
                    await store.record_usage(
                        {
                            "request_id": request_id,
                            "product_id": body.product_id,
                            "task": body.task.value,
                            "provider": model.split("/", 1)[0],
                            "model": model,
                            "input_tokens": result.input_tokens,
                            "output_tokens": result.output_tokens,
                            "estimated_cost_usd": result.estimated_cost_usd or 0,
                            "attempts": attempt,
                            "budget_usd": settings.budget_for(body.product_id, body.task.value),
                        }
                    )
                else:
                    output = result
                usage = (
                    {
                        "input_tokens": result.input_tokens,
                        "output_tokens": result.output_tokens,
                        "estimated_cost_usd": result.estimated_cost_usd,
                    }
                    if isinstance(result, CompletionResult)
                    else {}
                )
                if validate_candidate(body.task, output):
                    status = TerminalStatus.COMPLETED
                    generation.finish(status="completed", **usage)
                    break
                status = TerminalStatus.REVIEW
                generation.finish(status="review", **usage)
            except ProviderError as exc:
                status = TerminalStatus.PROVIDER_UNAVAILABLE
                generation.finish(status="error", error_category=exc.category)
        response = {
            "request_id": request_id,
            "status": status,
            "output": output if status == TerminalStatus.COMPLETED else None,
            "model_policy_version": settings.model_policy_version,
            "trace_reference": trace.reference,
        }
    trace.finish(str(response["status"]))
    tracer.flush()
    await store.put(body.product_id, body.idempotency_key, payload, response)
    return ExecuteResponse(**response)
