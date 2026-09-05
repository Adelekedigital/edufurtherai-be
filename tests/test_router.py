from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from app.core.config import TaskRoutingPolicy
from app.domain.ai_router import CompletionResult, ProviderError
from app.infra.observability import LangfuseTracer
from app.main import app, provider, settings, store


def request(key="k1"):
    return {
        "product_id": "scholarship_finder",
        "feature_id": "extract",
        "task": "scholarship_extraction",
        "taskrelation_id": "job-1",
        "idempotency_key": key,
        "source_data": {"excerpt": "deadline"},
    }


def test_health_and_contract():
    client = TestClient(app)
    assert client.get("/health").json()["status"] == "ok"
    response = client.post(
        "/api/v1/internal/ai/execute", json=request(), headers={"Idempotency-Key": "k1"}
    )
    assert response.status_code == 200
    assert set(response.json()) == {
        "request_id",
        "status",
        "output",
        "model_policy_version",
        "trace_reference",
    }


def test_unknown_task_denied():
    client = TestClient(app)
    body = request("k2") | {"task": "arbitrary_prompt"}
    response = client.post(
        "/api/v1/internal/ai/execute", json=body, headers={"Idempotency-Key": "k2"}
    )
    assert response.status_code == 422


def test_idempotency_mismatch():
    client = TestClient(app)
    response = client.post(
        "/api/v1/internal/ai/execute", json=request("k3"), headers={"Idempotency-Key": "other"}
    )
    assert response.status_code == 400


def test_retryable_provider_error_uses_all_ordered_models(monkeypatch):
    calls = []

    async def complete(*, task, source_data, model, max_tokens):
        calls.append(model)
        if model == "openai/primary":
            raise ProviderError("provider_transient", retryable=True)
        return {"candidate": {}, "evidence": []}

    monkeypatch.setattr(provider, "complete", complete)
    monkeypatch.setattr(
        settings,
        "routing_policy",
        {"scholarship_extraction": {"models": ["openai/primary", "anthropic/secondary"]}},
    )
    response = TestClient(app).post(
        "/api/v1/internal/ai/execute",
        json=request("fallback-ordered"),
        headers={"Idempotency-Key": "fallback-ordered"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert calls == ["openai/primary", "anthropic/secondary"]


def test_permanent_provider_error_does_not_fallback(monkeypatch):
    calls = []

    async def complete(*, task, source_data, model, max_tokens):
        calls.append(model)
        raise ProviderError("provider_error", retryable=False)

    monkeypatch.setattr(provider, "complete", complete)
    monkeypatch.setattr(settings, "routing_policy", {})
    monkeypatch.setattr(settings, "primary_model", "openai/primary")
    monkeypatch.setattr(settings, "fallback_model", "anthropic/fallback")
    response = TestClient(app).post(
        "/api/v1/internal/ai/execute",
        json=request("fallback-permanent"),
        headers={"Idempotency-Key": "fallback-permanent"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "provider_unavailable"
    assert calls == ["openai/primary"]


def test_retryable_errors_exhaust_ordered_models(monkeypatch):
    calls = []

    async def complete(*, task, source_data, model, max_tokens):
        calls.append(model)
        raise ProviderError("provider_transient", retryable=True)

    monkeypatch.setattr(provider, "complete", complete)
    monkeypatch.setattr(
        settings,
        "routing_policy",
        {"scholarship_extraction": {"models": ["one/model", "two/model"]}},
    )
    monkeypatch.setattr(settings, "fallback_model", "three/model")
    response = TestClient(app).post(
        "/api/v1/internal/ai/execute",
        json=request("fallback-exhausted"),
        headers={"Idempotency-Key": "fallback-exhausted"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "provider_unavailable"
    assert calls == ["one/model", "two/model", "three/model"]


def test_routing_policy_rejects_invalid_model_ids():
    with pytest.raises(ValueError):
        TaskRoutingPolicy(models=["openai/model", "not-provider-qualified"])


def test_routing_policy_rejects_duplicate_models():
    with pytest.raises(ValueError):
        TaskRoutingPolicy(models=["openai/model", "openai/model"])


def test_langfuse_tracer_sends_metadata_without_payloads():
    class Span:
        def __init__(self):
            self.metadata = None
            self.ended = False

        def update(self, *, metadata):
            self.metadata = metadata

        def end(self):
            self.ended = True

    class Client:
        def __init__(self):
            self.span = Span()
            self.kwargs = None
            self.flushed = False

        def create_trace_id(self, *, seed):
            return "trace-id"

        def start_span(self, **kwargs):
            self.kwargs = kwargs
            return self.span

        def get_trace_url(self, *, trace_id):
            return "https://langfuse.example/trace/trace-id"

        def flush(self):
            self.flushed = True

    client = Client()
    tracer = LangfuseTracer(client)
    trace = tracer.start(
        request_id="req-1",
        product_id="scholarship_finder",
        feature_id="extract",
        task="scholarship_extraction",
        policy_version="v1",
    )
    trace.finish("completed")
    tracer.flush()
    assert trace.reference == "https://langfuse.example/trace/trace-id"
    assert client.kwargs["metadata"] == {
        "request_id": "req-1",
        "product_id": "scholarship_finder",
        "feature_id": "extract",
        "task": "scholarship_extraction",
        "model_policy_version": "v1",
    }
    assert "source_data" not in client.kwargs
    assert "output" not in client.kwargs
    assert client.span.metadata == {"status": "completed"}
    assert client.span.ended
    assert client.flushed


def test_langfuse_failure_does_not_raise():
    class BrokenClient:
        def create_trace_id(self, *, seed):
            raise RuntimeError("telemetry unavailable")

    trace = LangfuseTracer(BrokenClient()).start(
        request_id="req-1",
        product_id="scholarship_finder",
        feature_id="extract",
        task="scholarship_extraction",
        policy_version="v1",
    )
    trace.finish("completed")


def test_provider_usage_is_recorded(monkeypatch):
    async def complete(*, task, source_data, model, max_tokens):
        return CompletionResult(
            output={"candidate": {}, "evidence": []},
            input_tokens=12,
            output_tokens=7,
            estimated_cost_usd=0.004,
        )

    monkeypatch.setattr(provider, "complete", complete)
    monkeypatch.setattr(settings, "routing_policy", {})
    monkeypatch.setattr(settings, "primary_model", "openai/usage-test")
    monkeypatch.setattr(settings, "fallback_model", "")
    response = TestClient(app).post(
        "/api/v1/internal/ai/execute",
        json=request("usage-recorded"),
        headers={"Idempotency-Key": "usage-recorded"},
    )
    assert response.status_code == 200
    usage = store.usage[-1]
    assert usage["input_tokens"] == 12
    assert usage["output_tokens"] == 7
    assert usage["estimated_cost_usd"] == 0.004


def test_product_task_budget_blocks_new_request(monkeypatch):
    calls = []

    async def complete(*, task, source_data, model, max_tokens):
        calls.append(model)
        return CompletionResult(output={"candidate": {}, "evidence": []}, estimated_cost_usd=0.004)

    store.usage.clear()
    monkeypatch.setattr(provider, "complete", complete)
    monkeypatch.setattr(settings, "routing_policy", {})
    monkeypatch.setattr(settings, "primary_model", "openai/budget-test")
    monkeypatch.setattr(settings, "fallback_model", "")
    monkeypatch.setattr(
        settings,
        "product_task_budgets",
        {"scholarship_finder:scholarship_extraction": 0.003},
    )
    client = TestClient(app)
    first = client.post(
        "/api/v1/internal/ai/execute",
        json=request("budget-first"),
        headers={"Idempotency-Key": "budget-first"},
    )
    second = client.post(
        "/api/v1/internal/ai/execute",
        json=request("budget-second"),
        headers={"Idempotency-Key": "budget-second"},
    )
    assert first.json()["status"] == "completed"
    assert second.json()["status"] == "budget_exhausted"
    assert calls == ["openai/budget-test"]


def test_product_task_rate_limit_returns_problem(monkeypatch):
    calls = []

    async def complete(*, task, source_data, model, max_tokens):
        calls.append(model)
        return {"candidate": {}, "evidence": []}

    store.request_times.clear()
    monkeypatch.setattr(provider, "complete", complete)
    monkeypatch.setattr(settings, "routing_policy", {})
    monkeypatch.setattr(settings, "primary_model", "openai/rate-test")
    monkeypatch.setattr(settings, "fallback_model", "")
    monkeypatch.setattr(
        settings,
        "product_task_rate_limits",
        {"scholarship_finder:scholarship_extraction": 1},
    )
    client = TestClient(app)
    first = client.post(
        "/api/v1/internal/ai/execute",
        json=request("rate-first"),
        headers={"Idempotency-Key": "rate-first"},
    )
    second = client.post(
        "/api/v1/internal/ai/execute",
        json=request("rate-second"),
        headers={"Idempotency-Key": "rate-second"},
    )
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["code"] == "RATE_LIMITED"
    assert calls == ["openai/rate-test"]


def test_jwt_key_registry_scope_and_replay(monkeypatch):
    async def complete(*, task, source_data, model, max_tokens):
        return {"candidate": {}, "evidence": []}

    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "iss": "edufurther-ai-router",
            "sub": "scholarship-finder-worker",
            "aud": "edufurther-ai-router",
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(minutes=1),
            "jti": "jwt-registry-test",
            "scope": "ai:execute",
        },
        "rotated-secret-with-at-least-32-bytes",
        algorithm="HS256",
        headers={"kid": "rotated"},
    )
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "service_jwt_public_key", "")
    monkeypatch.setattr(
        settings, "service_jwt_keys", {"rotated": "rotated-secret-with-at-least-32-bytes"}
    )
    monkeypatch.setattr(settings, "service_jwt_algorithm", "HS256")
    monkeypatch.setattr(settings, "service_jwt_required_scope", "ai:execute")
    monkeypatch.setattr(settings, "routing_policy", {})
    monkeypatch.setattr(settings, "primary_model", "openai/auth-test")
    monkeypatch.setattr(settings, "fallback_model", "")
    monkeypatch.setattr(provider, "complete", complete)
    store.replayed_jtis.clear()
    client = TestClient(app)
    first = client.post(
        "/api/v1/internal/ai/execute",
        json=request("jwt-registry-key"),
        headers={"Idempotency-Key": "jwt-registry-key", "Authorization": f"Bearer {token}"},
    )
    replay = client.post(
        "/api/v1/internal/ai/execute",
        json=request("jwt-registry-replay"),
        headers={"Idempotency-Key": "jwt-registry-replay", "Authorization": f"Bearer {token}"},
    )
    assert first.status_code == 200
    assert replay.status_code == 401
