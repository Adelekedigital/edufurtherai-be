from fastapi.testclient import TestClient

from app.main import app


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
