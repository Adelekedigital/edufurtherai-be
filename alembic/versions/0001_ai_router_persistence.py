"""add AI Router persistence tables

Revision ID: 0001_ai_router_persistence
Revises:
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001_ai_router_persistence"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    jsonb = postgresql.JSONB()

    def common():
        return [
            sa.Column("id", uuid, primary_key=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        ]

    op.create_table(
        "ai_requests",
        *common(),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("product_id", sa.String(64), nullable=False),
        sa.Column("feature_id", sa.String(64), nullable=False),
        sa.Column("task", sa.String(64), nullable=False),
        sa.Column("taskrelation_id", sa.String(128), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("output", jsonb),
        sa.Column("model_policy_version", sa.String(128), nullable=False),
        sa.Column("trace_reference", sa.String(256)),
        sa.UniqueConstraint("request_id", name="uq_ai_requests_request_id"),
    )
    op.create_table(
        "ai_idempotency_keys",
        *common(),
        sa.Column("product_id", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column(
            "request_id", sa.String(128), sa.ForeignKey("ai_requests.request_id"), nullable=False
        ),
        sa.Column("response", jsonb),
        sa.UniqueConstraint("product_id", "idempotency_key", name="uq_ai_idempotency_product_key"),
    )
    op.create_table(
        "ai_usage",
        *common(),
        sa.Column(
            "request_id", sa.String(128), sa.ForeignKey("ai_requests.request_id"), nullable=False
        ),
        sa.Column("product_id", sa.String(64), nullable=False),
        sa.Column("task", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(256), nullable=False),
        sa.Column("input_tokens", sa.Integer),
        sa.Column("output_tokens", sa.Integer),
        sa.Column("estimated_cost_usd", sa.Float, nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="1"),
    )
    op.create_table(
        "ai_budget_periods",
        *common(),
        sa.Column("product_id", sa.String(64), nullable=False),
        sa.Column("task", sa.String(64), nullable=False),
        sa.Column("period_start", sa.Date, nullable=False),
        sa.Column("budget_usd", sa.Float, nullable=False),
        sa.Column("spent_usd", sa.Float, nullable=False, server_default="0"),
        sa.UniqueConstraint(
            "product_id", "task", "period_start", name="uq_ai_budget_product_task_period"
        ),
    )
    op.create_table(
        "routing_policies",
        *common(),
        sa.Column("task", sa.String(64), nullable=False),
        sa.Column("version", sa.String(128), nullable=False),
        sa.Column("models", jsonb, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("task", "version", name="uq_routing_policy_task_version"),
    )
    op.create_table(
        "service_jti_replays",
        *common(),
        sa.Column("issuer", sa.String(256), nullable=False),
        sa.Column("jti", sa.String(256), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("issuer", "jti", name="uq_service_jti_replay_issuer_jti"),
    )


def downgrade() -> None:
    for table in (
        "service_jti_replays",
        "routing_policies",
        "ai_budget_periods",
        "ai_usage",
        "ai_idempotency_keys",
        "ai_requests",
    ):
        op.drop_table(table)
