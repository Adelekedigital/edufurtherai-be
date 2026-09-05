from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def uuid_default() -> UUID:
    return uuid4()


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )


class AIRequest(Timestamped, Base):
    __tablename__ = "ai_requests"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid_default)
    request_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    product_id: Mapped[str] = mapped_column(String(64), nullable=False)
    feature_id: Mapped[str] = mapped_column(String(64), nullable=False)
    task: Mapped[str] = mapped_column(String(64), nullable=False)
    taskrelation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    model_policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_reference: Mapped[str | None] = mapped_column(String(256))


class AIIdempotencyKey(Timestamped, Base):
    __tablename__ = "ai_idempotency_keys"
    __table_args__ = (
        UniqueConstraint("product_id", "idempotency_key", name="uq_ai_idempotency_product_key"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid_default)
    product_id: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("ai_requests.request_id"), nullable=False
    )
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class AIUsage(Timestamped, Base):
    __tablename__ = "ai_usage"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid_default)
    request_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("ai_requests.request_id"), nullable=False
    )
    product_id: Mapped[str] = mapped_column(String(64), nullable=False)
    task: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(256), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class AIBudgetPeriod(Timestamped, Base):
    __tablename__ = "ai_budget_periods"
    __table_args__ = (
        UniqueConstraint(
            "product_id", "task", "period_start", name="uq_ai_budget_product_task_period"
        ),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid_default)
    product_id: Mapped[str] = mapped_column(String(64), nullable=False)
    task: Mapped[str] = mapped_column(String(64), nullable=False)
    period_start: Mapped[datetime] = mapped_column(Date, nullable=False)
    budget_usd: Mapped[float] = mapped_column(Float, nullable=False)
    spent_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0)


class RoutingPolicy(Timestamped, Base):
    __tablename__ = "routing_policies"
    __table_args__ = (UniqueConstraint("task", "version", name="uq_routing_policy_task_version"),)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid_default)
    task: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(128), nullable=False)
    models: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    active: Mapped[bool] = mapped_column(nullable=False, default=False)


class ServiceJTIReplay(Timestamped, Base):
    __tablename__ = "service_jti_replays"
    __table_args__ = (UniqueConstraint("issuer", "jti", name="uq_service_jti_replay_issuer_jti"),)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid_default)
    issuer: Mapped[str] = mapped_column(String(256), nullable=False)
    jti: Mapped[str] = mapped_column(String(256), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
