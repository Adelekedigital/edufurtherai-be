import pytest
from fastapi.testclient import TestClient

from app.core.config import TaskRoutingPolicy
from app.domain.ai_router import ProviderError
from app.main import app, provider, settings


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
