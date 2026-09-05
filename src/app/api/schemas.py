from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.ai_router import Task, TerminalStatus


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: str = Field(min_length=1, max_length=64)
    feature_id: str = Field(min_length=1, max_length=64)
    task: Task
    taskrelation_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=128)
    source_data: dict[str, Any]
    journey_id: UUID | None = None
    session_id: str | None = Field(default=None, max_length=128)
    handoff_id: UUID | None = None


class ExecuteResponse(BaseModel):
    request_id: str
    status: TerminalStatus
    output: dict[str, Any] | None
    model_policy_version: str
    trace_reference: str | None
