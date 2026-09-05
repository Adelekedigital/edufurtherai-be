import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.infra.models import AIIdempotencyKey, AIRequest, ServiceJTIReplay
from app.infra.store import PostgresStore

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL is required")


def test_postgres_idempotency_and_replay_are_durable() -> None:
    asyncio.run(_exercise_store())


async def _exercise_store() -> None:
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = PostgresStore(sessions)
    payload = {
        "feature_id": "extract",
        "task": "scholarship_extraction",
        "taskrelation_id": "integration-job",
    }
    request_id = "req_integration_store"

    async with sessions() as session:
        await session.execute(delete(AIIdempotencyKey))
        await session.execute(delete(AIRequest))
        await session.execute(delete(ServiceJTIReplay))
        await session.commit()

    assert (
        await store.claim("scholarship_finder", "integration-key", payload, request_id, "v1")
        is None
    )
    claimed = await store.get("scholarship_finder", "integration-key")
    assert claimed is not None
    assert claimed.response is None

    response = {
        "request_id": request_id,
        "status": "completed",
        "output": {"candidate": {}, "evidence": []},
        "model_policy_version": "v1",
        "trace_reference": None,
    }
    await store.put("scholarship_finder", "integration-key", payload, response)
    stored = await store.get("scholarship_finder", "integration-key")
    assert stored is not None and stored.response == response

    assert await store.claim_jti(
        "issuer", "integration-jti", datetime.now(UTC) + timedelta(minutes=5)
    )
    assert not await store.claim_jti(
        "issuer", "integration-jti", datetime.now(UTC) + timedelta(minutes=5)
    )
    await engine.dispose()
