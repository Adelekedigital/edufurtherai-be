import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infra.models import AIBudgetPeriod, AIIdempotencyKey, AIRequest, AIUsage


@dataclass
class IdempotencyRecord:
    digest: str
    response: dict[str, Any] | None


class MemoryStore:
    def __init__(self) -> None:
        self.records: dict[tuple[str, str], IdempotencyRecord] = {}
        self.spent_usd = 0.0
        self.replayed_jtis: set[tuple[str, str]] = set()
        self.usage: list[dict[str, Any]] = []

    @staticmethod
    def digest(payload: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

    async def get(self, product: str, key: str) -> IdempotencyRecord | None:
        return self.records.get((product, key))

    async def put(
        self, product: str, key: str, payload: dict[str, Any], response: dict[str, Any]
    ) -> None:
        self.records[(product, key)] = IdempotencyRecord(self.digest(payload), response)

    async def claim_jti(self, issuer: str, jti: str, expires_at: datetime) -> bool:
        del expires_at
        identity = (issuer, jti)
        if identity in self.replayed_jtis:
            return False
        self.replayed_jtis.add(identity)
        return True

    async def record_usage(self, usage: dict[str, Any]) -> None:
        self.usage.append(usage)
        self.spent_usd += float(usage.get("estimated_cost_usd") or 0)

    async def budget_exhausted(self, product: str, task: str, limit: float) -> bool:
        spent = sum(
            float(item.get("estimated_cost_usd") or 0)
            for item in self.usage
            if item.get("product_id") == product and item.get("task") == task
        )
        return spent >= limit


class PostgresStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    @staticmethod
    def digest(payload: dict[str, Any]) -> str:
        return MemoryStore.digest(payload)

    async def get(self, product: str, key: str) -> IdempotencyRecord | None:
        async with self.sessions() as session:
            row = await session.scalar(
                select(AIIdempotencyKey).where(
                    AIIdempotencyKey.product_id == product,
                    AIIdempotencyKey.idempotency_key == key,
                )
            )
            return IdempotencyRecord(row.request_digest, row.response) if row else None

    async def claim(
        self, product: str, key: str, payload: dict[str, Any], request_id: str, policy: str
    ) -> IdempotencyRecord | None:
        digest = self.digest(payload)
        try:
            async with self.sessions() as session:
                async with session.begin():
                    existing = await session.scalar(
                        select(AIIdempotencyKey)
                        .where(
                            AIIdempotencyKey.product_id == product,
                            AIIdempotencyKey.idempotency_key == key,
                        )
                        .with_for_update()
                    )
                    if existing:
                        return IdempotencyRecord(existing.request_digest, existing.response)
                    session.add(
                        AIRequest(
                            request_id=request_id,
                            product_id=product,
                            feature_id=payload["feature_id"],
                            task=payload["task"],
                            taskrelation_id=payload["taskrelation_id"],
                            request_digest=digest,
                            status="pending",
                            model_policy_version=policy,
                        )
                    )
                    await session.flush()
                    session.add(
                        AIIdempotencyKey(
                            product_id=product,
                            idempotency_key=key,
                            request_digest=digest,
                            request_id=request_id,
                        )
                    )
        except IntegrityError:
            # A concurrent claimant won the unique-key race; read its outcome.
            existing = await self.get(product, key)
            if existing:
                return existing
            raise
        return None

    async def put(
        self, product: str, key: str, payload: dict[str, Any], response: dict[str, Any]
    ) -> None:
        async with self.sessions() as session:
            async with session.begin():
                idem = await session.scalar(
                    select(AIIdempotencyKey)
                    .where(
                        AIIdempotencyKey.product_id == product,
                        AIIdempotencyKey.idempotency_key == key,
                    )
                    .with_for_update()
                )
                if idem:
                    idem.response = response
                    request = await session.scalar(
                        select(AIRequest)
                        .where(AIRequest.request_id == idem.request_id)
                        .with_for_update()
                    )
                    if request:
                        request.status = str(response["status"])
                        request.output = response.get("output")
                        request.trace_reference = response.get("trace_reference")

    async def check_connection(self) -> None:
        async with self.sessions() as session:
            await session.execute(text("SELECT 1"))

    async def claim_jti(self, issuer: str, jti: str, expires_at: datetime) -> bool:
        from app.infra.models import ServiceJTIReplay

        async with self.sessions() as session:
            async with session.begin():
                result = await session.execute(
                    insert(ServiceJTIReplay)
                    .values(issuer=issuer, jti=jti, expires_at=expires_at)
                    .on_conflict_do_nothing(constraint="uq_service_jti_replay_issuer_jti")
                )
                return result.rowcount == 1

    async def record_usage(self, usage: dict[str, Any]) -> None:
        async with self.sessions() as session:
            async with session.begin():
                budget = float(usage.get("budget_usd", 0))
                cost = float(usage.get("estimated_cost_usd") or 0)
                session.add(
                    AIUsage(**{key: value for key, value in usage.items() if key != "budget_usd"})
                )
                await session.flush()
                period = date.today()
                await session.execute(
                    insert(AIBudgetPeriod)
                    .values(
                        product_id=usage["product_id"],
                        task=usage["task"],
                        period_start=period,
                        budget_usd=budget,
                        spent_usd=cost,
                    )
                    .on_conflict_do_update(
                        index_elements=["product_id", "task", "period_start"],
                        set_={
                            "spent_usd": AIBudgetPeriod.spent_usd + cost,
                            "updated_at": func.now(),
                        },
                    )
                )

    async def budget_exhausted(self, product: str, task: str, limit: float) -> bool:
        start = datetime.combine(date.today(), datetime.min.time(), tzinfo=UTC)
        async with self.sessions() as session:
            spent = await session.scalar(
                select(func.coalesce(func.sum(AIUsage.estimated_cost_usd), 0)).where(
                    AIUsage.product_id == product,
                    AIUsage.task == task,
                    AIUsage.created_at >= start,
                )
            )
            return float(spent or 0) >= limit
