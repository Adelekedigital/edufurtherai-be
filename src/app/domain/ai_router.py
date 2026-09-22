import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class Task(StrEnum):
    SCHOLARSHIP_EXTRACTION = "scholarship_extraction"
    MATCH_EXPLANATION = "match_explanation"
    # Agent platform tasks (edufurther-agent-be). Each workflow step is its
    # own registered task rather than one generic "run my prompt" task: the
    # prompt, the accepted output shape and the size limit stay server-side
    # policy here, exactly as they already do for the two tasks above. A
    # caller still cannot send a prompt or a schema - see ExecuteRequest's
    # extra="forbid".
    CLASSIFY_SOURCE_PAGE = "classify_source_page"
    SPLIT_LIST_CANDIDATES = "split_list_candidates"
    EXTRACT_SCHOLARSHIP_FACTS = "extract_scholarship_facts"
    COMPARE_OFFICIAL_EVIDENCE = "compare_official_evidence"
    EXTRACT_ELIGIBILITY_REQUIREMENTS = "extract_eligibility_requirements"


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
    #: Per-task, because the tasks differ by orders of magnitude: classifying
    #: a page needs a sample, splitting a list page needs the whole thing.
    #: A single global ceiling would have to be the largest, which would
    #: silently raise the limit for every small task too.
    max_source_bytes: int = 16_384


@dataclass(frozen=True)
class CompletionResult:
    output: Any
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost_usd: float | None = None


_FINDER = frozenset({"scholarship_finder"})
_AGENT = frozenset({"edufurther_agent"})


POLICIES = {
    # 32 KiB, not 16: callers cap their *content* at 16 KiB and then wrap it
    # in a dict, so the serialized payload is always a little over. The old
    # 16 KiB ceiling rejected exactly the largest well-formed requests.
    Task.SCHOLARSHIP_EXTRACTION: Policy(
        Task.SCHOLARSHIP_EXTRACTION, 1, 2_000, _FINDER, max_source_bytes=32_768
    ),
    Task.MATCH_EXPLANATION: Policy(
        Task.MATCH_EXPLANATION, 1, 800, _FINDER, max_source_bytes=32_768
    ),
    Task.CLASSIFY_SOURCE_PAGE: Policy(
        Task.CLASSIFY_SOURCE_PAGE, 1, 600, _AGENT, max_source_bytes=65_536
    ),
    # The only task that needs a whole page: a list page's tenth scholarship
    # is exactly the one a head truncation would drop.
    Task.SPLIT_LIST_CANDIDATES: Policy(
        Task.SPLIT_LIST_CANDIDATES, 1, 8_000, _AGENT, max_source_bytes=262_144
    ),
    Task.EXTRACT_SCHOLARSHIP_FACTS: Policy(
        Task.EXTRACT_SCHOLARSHIP_FACTS, 1, 2_000, _AGENT, max_source_bytes=65_536
    ),
    # Carries two documents at once - the discovery's facts and the official
    # page they are being checked against.
    Task.COMPARE_OFFICIAL_EVIDENCE: Policy(
        Task.COMPARE_OFFICIAL_EVIDENCE, 1, 2_000, _AGENT, max_source_bytes=131_072
    ),
    Task.EXTRACT_ELIGIBILITY_REQUIREMENTS: Policy(
        Task.EXTRACT_ELIGIBILITY_REQUIREMENTS, 1, 2_000, _AGENT, max_source_bytes=65_536
    ),
}


#: Returned to the caller on every response. Products persist this alongside
#: extracted facts, so a later accuracy regression can be traced to the exact
#: prompt that produced it. Bump the version whenever a prompt's text changes
#: in a way that could change its output - editing a prompt without bumping
#: makes stored provenance a lie.
PROMPT_VERSIONS = {
    Task.SCHOLARSHIP_EXTRACTION: "scholarship_extraction-v1",
    Task.MATCH_EXPLANATION: "match_explanation-v1",
    Task.CLASSIFY_SOURCE_PAGE: "classify_source_page-v1",
    Task.SPLIT_LIST_CANDIDATES: "split_list_candidates-v1",
    Task.EXTRACT_SCHOLARSHIP_FACTS: "extract_scholarship_facts-v1",
    Task.COMPARE_OFFICIAL_EVIDENCE: "compare_official_evidence-v1",
    Task.EXTRACT_ELIGIBILITY_REQUIREMENTS: "extract_eligibility_requirements-v1",
}


PAGE_TYPES = frozenset({"individual", "list", "aggregator", "not_a_scholarship"})


class ProviderError(Exception):
    def __init__(self, category: str, retryable: bool = False) -> None:
        self.category, self.retryable = category, retryable


class Provider(Protocol):
    async def complete(
        self, *, task: Task, source_data: dict[str, Any], model: str, max_tokens: int
    ) -> Any: ...


def source_data_bytes(source_data: dict[str, Any]) -> int:
    """Size of the payload the provider will actually receive.

    Deliberately mirrors `providers.complete`, which sends
    `json.dumps(source_data)` as the user message - including its default
    `ensure_ascii=True`, so a non-ASCII character is measured at the six
    bytes it really costs on the wire. `str(source_data)` was neither the
    serialized form nor a stable proxy for its length.
    """
    return len(json.dumps(source_data).encode())


def _has_evidence(output: dict[str, Any]) -> bool:
    return isinstance(output.get("evidence"), list)


def _is_dict_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, dict) for item in value)


def _validate_extraction(output: dict[str, Any]) -> bool:
    return isinstance(output.get("candidate"), dict) and _has_evidence(output)


def _validate_match_explanation(output: dict[str, Any]) -> bool:
    return isinstance(output.get("explanation"), str) and _has_evidence(output)


def _validate_classification(output: dict[str, Any]) -> bool:
    # isinstance-guarded before the membership test. PAGE_TYPES is a
    # frozenset, so `x in PAGE_TYPES` hashes x - and a model returning
    # `"page_type": ["list"]` is an ordinary malformation, not an exotic
    # one. Unguarded it raised TypeError out of validate_candidate, which
    # the execute loop does not catch.
    page_type = output.get("page_type")
    return isinstance(page_type, str) and page_type in PAGE_TYPES and _has_evidence(output)


def _validate_split(output: dict[str, Any]) -> bool:
    return _is_dict_list(output.get("candidates")) and _has_evidence(output)


def _validate_comparison(output: dict[str, Any]) -> bool:
    return (
        _is_dict_list(output.get("comparisons"))
        and isinstance(output.get("contradictions"), list)
        and _has_evidence(output)
    )


def _validate_eligibility(output: dict[str, Any]) -> bool:
    return _is_dict_list(output.get("rules")) and _has_evidence(output)


_VALIDATORS: dict[Task, Callable[[dict[str, Any]], bool]] = {
    Task.SCHOLARSHIP_EXTRACTION: _validate_extraction,
    Task.MATCH_EXPLANATION: _validate_match_explanation,
    Task.CLASSIFY_SOURCE_PAGE: _validate_classification,
    Task.SPLIT_LIST_CANDIDATES: _validate_split,
    Task.EXTRACT_SCHOLARSHIP_FACTS: _validate_extraction,
    Task.COMPARE_OFFICIAL_EVIDENCE: _validate_comparison,
    Task.EXTRACT_ELIGIBILITY_REQUIREMENTS: _validate_eligibility,
}


def validate_candidate(task: Task, output: Any) -> bool:
    """Reject anything that is not the task's declared shape.

    A `verified` key is rejected for every task, always: verification is a
    product decision made against real evidence, never something a model
    gets to assert about its own output.

    Default-deny on an unregistered task. A task with no validator must not
    fall through to some other task's check and be marked `completed`.
    """
    if not isinstance(output, dict) or output.get("verified") is not None:
        return False
    validator = _VALIDATORS.get(task)
    if validator is None:
        return False
    try:
        return validator(output)
    except Exception:  # pragma: no cover - defence in depth
        # A validator is fed raw model output, so it must treat any shape as
        # possible. If one still raises, the answer is "this is not the
        # declared shape", not a 500 - and crucially not an exception
        # escaping into the execute loop, where it would leave the
        # idempotency key claimed with no stored response and every retry
        # of that key answering 409 REQUEST_IN_PROGRESS forever.
        logger.warning("validator raised for task=%s", task, exc_info=True)
        return False
