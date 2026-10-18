import json
import logging
import re
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
    # EduFurther platform writing assist. Two tasks rather than one per role:
    # the role travels in source_data and the prompt branches on it, so a
    # mentee and a mentor bio share one policy, one validator and one cap.
    BIO_DRAFT = "bio_draft"
    BIO_IMPROVE = "bio_improve"


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
_PLATFORM = frozenset({"edufurther_platform"})

#: The platform's about_me column limit. Counted in code points, as
#: Postgres char_length and Python len both count them.
MAX_BIO_CHARS = 1_500


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
        # 24k, not 8k: a roundup describing fifty awards produced a reply
        # cut off mid-string at the old ceiling, which is unrecoverable -
        # the retry truncates in the same place.
        Task.SPLIT_LIST_CANDIDATES,
        1,
        24_000,
        _AGENT,
        max_source_bytes=262_144,
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
    # 1,500 characters is ~400 tokens of English, but several times that in
    # scripts that tokenize poorly, or when a model writes non-ASCII as \u
    # escapes inside the JSON. A bio cut off at the ceiling is unrecoverable
    # on retry, and the ceiling caps spend rather than setting it, so it is
    # sized for the worst case.
    Task.BIO_DRAFT: Policy(Task.BIO_DRAFT, 1, 4_000, _PLATFORM, max_source_bytes=8_192),
    # The person's own 1,500-character text, measured as json.dumps sends
    # it: ensure_ascii writes a non-ASCII character as a six-byte escape and
    # an emoji as a twelve-byte surrogate pair, so the worst case is ~18 KB.
    Task.BIO_IMPROVE: Policy(Task.BIO_IMPROVE, 1, 4_000, _PLATFORM, max_source_bytes=20_480),
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
    Task.BIO_DRAFT: "bio_draft-v1",
    Task.BIO_IMPROVE: "bio_improve-v1",
}


PAGE_TYPES = frozenset({"individual", "list", "aggregator", "not_a_scholarship"})

#: Markup the profile page would render literally: anything opening an HTML
#: tag (closed or not), asterisks, bold underscores, code, markdown links,
#: headings, blockquotes and list bullets. A bio is plain prose, so "<3" and
#: "maths > physics" pass, and only a one- or two-digit number opening a line
#: counts as a list item, so "2019. I moved" is not one.
_MARKUP = re.compile(
    r"</?[A-Za-z!]|\*|__|`|\[[^\]\n]*\]\(|^\s*(?:#{1,6}\s|[-+]\s|>|\d{1,2}[.)]\s)", re.MULTILINE
)

#: Owner rule for profile writing: no em dashes and no emoji, ever. Models
#: reach for both habitually, so the router repairs a bio rather than
#: rejecting it (`normalize_output`), and the validator re-checks afterwards.
#: The dash set is the em dash and its look-alikes (horizontal bar, two- and
#: three-em dashes, small em dash); a plain hyphen and the en dash stay
#: allowed.
_EM_DASHES = "\u2014\u2015\u2e3a\u2e3b\ufe58"
#: The pictographic blocks, the scattered symbols that render as emoji by
#: default, the variation selector that turns any other symbol into its emoji
#: form, and the combining keycap.
_EMOJI = (
    "\u203c\u2049\u20e3\u231a\u231b\u2328\u23cf\u23e9-\u23fa\u24c2\u25aa-\u25fe"
    "\u2600-\u27bf\u2934\u2935\u2b00-\u2bff\u3030\u303d\u3297\u3299\ufe0f"
    "\U0001f000-\U0001faff"
)
_FORBIDDEN_IN_BIO = re.compile(f"[{_EM_DASHES}{_EMOJI}]")
_DASH_RUN = re.compile(f"[ \\t]*[{_EM_DASHES}]+[ \\t]*")
#: An emoji and everything that binds to it: a zero-width joiner only inside
#: such a run, never on its own, because Indic scripts use it in plain text.
_EMOJI_RUN = re.compile(f"[{_EMOJI}](?:[{_EMOJI}]|\u200d)*")


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


def _validate_bio(output: dict[str, Any]) -> bool:
    # Exactly one key: the platform writes `text` straight into about_me, so
    # anything else the model adds would be silently dropped at best.
    text = output.get("text")
    return (
        set(output) == {"text"}
        and isinstance(text, str)
        and bool(text.strip())
        and len(text) <= MAX_BIO_CHARS
        and _MARKUP.search(text) is None
        and _FORBIDDEN_IN_BIO.search(text) is None
    )


def _clean_bio_text(text: str) -> str:
    """Replace each em dash with a comma and drop emoji, then tidy what
    that leaves, so "I moved <em dash> and stayed <emoji>." reads "I moved,
    and stayed." Markup is not repaired here; it is still rejected."""
    text = _DASH_RUN.sub(", ", text)
    text = _EMOJI_RUN.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+([,.;:!?])", r"\1", text)
    text = re.sub(r",[ \t]*([,.;:!?])", r"\1", text)
    text = re.sub(r"(?m)^[ \t,]+|[ \t,]+$", "", text)
    return text.strip()


def _normalize_bio(output: dict[str, Any]) -> dict[str, Any]:
    text = output.get("text")
    if not isinstance(text, str):
        return output
    return {**output, "text": _clean_bio_text(text)}


_NORMALIZERS: dict[Task, Callable[[dict[str, Any]], dict[str, Any]]] = {
    Task.BIO_DRAFT: _normalize_bio,
    Task.BIO_IMPROVE: _normalize_bio,
}


def normalize_output(task: Task, output: Any) -> Any:
    """Repair what a task's rules allow repairing, before validation.

    Only tasks with a registered normalizer are touched, and a normalizer
    never raises into the execute loop: on any surprise the output passes
    through unchanged and the validator decides.
    """
    normalizer = _NORMALIZERS.get(task)
    if normalizer is None or not isinstance(output, dict):
        return output
    try:
        return normalizer(output)
    except Exception:  # pragma: no cover - defence in depth
        logger.warning("normalizer raised for task=%s", task, exc_info=True)
        return output


_VALIDATORS: dict[Task, Callable[[dict[str, Any]], bool]] = {
    Task.SCHOLARSHIP_EXTRACTION: _validate_extraction,
    Task.MATCH_EXPLANATION: _validate_match_explanation,
    Task.CLASSIFY_SOURCE_PAGE: _validate_classification,
    Task.SPLIT_LIST_CANDIDATES: _validate_split,
    Task.EXTRACT_SCHOLARSHIP_FACTS: _validate_extraction,
    Task.COMPARE_OFFICIAL_EVIDENCE: _validate_comparison,
    Task.EXTRACT_ELIGIBILITY_REQUIREMENTS: _validate_eligibility,
    Task.BIO_DRAFT: _validate_bio,
    Task.BIO_IMPROVE: _validate_bio,
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
