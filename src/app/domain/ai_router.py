from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class Task(StrEnum):
    SCHOLARSHIP_EXTRACTION = "scholarship_extraction"
    MATCH_EXPLANATION = "match_explanation"


class TerminalStatus(StrEnum):
    COMPLETED = "completed"
    REVIEW = "review"
    BUDGET_EXHAUSTED = "budget_exhausted"
    PROVIDER_UNAVAILABLE = "provider_unavailable"


@dataclass(frozen=True)
class Policy:
    task: Task
    schema_version: int
    max_output_tokens: int
    allowed_products: frozenset[str]


@dataclass(frozen=True)
class CompletionResult:
    output: Any
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost_usd: float | None = None


POLICIES = {
    Task.SCHOLARSHIP_EXTRACTION: Policy(
        Task.SCHOLARSHIP_EXTRACTION, 1, 2_000, frozenset({"scholarship_finder"})
    ),
    Task.MATCH_EXPLANATION: Policy(
        Task.MATCH_EXPLANATION, 1, 800, frozenset({"scholarship_finder"})
    ),
}


class ProviderError(Exception):
    def __init__(self, category: str, retryable: bool = False) -> None:
        self.category, self.retryable = category, retryable


class Provider(Protocol):
    async def complete(
        self, *, task: Task, source_data: dict[str, Any], model: str, max_tokens: int
    ) -> Any: ...


def validate_candidate(task: Task, output: Any) -> bool:
    if not isinstance(output, dict) or output.get("verified") is not None:
        return False
    if task == Task.SCHOLARSHIP_EXTRACTION:
        return isinstance(output.get("candidate"), dict) and isinstance(
            output.get("evidence"), list
        )
    return isinstance(output.get("explanation"), str) and isinstance(output.get("evidence"), list)
