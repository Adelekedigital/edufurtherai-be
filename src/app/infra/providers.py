import json
import logging
import re
from typing import Any

from app.core.config import settings
from app.domain.ai_router import CompletionResult, ProviderError, Task

logger = logging.getLogger(__name__)

#: The automation boundary, stated to the model in every prompt. Kept as one
#: constant so a new task cannot quietly ship without it; the matching
#: server-side reject lives in `domain.ai_router.validate_candidate`, because
#: a prompt instruction alone is not an enforcement mechanism.
NEVER_VERIFIES = (
    'Never include a "verified" field under any circumstances; verification is '
    "decided by a separate process, never by you."
)

SYSTEM_PROMPTS = {
    Task.SCHOLARSHIP_EXTRACTION: (
        "Extract scholarship details from the source text below. Return only a JSON object of "
        'exactly this shape: {"candidate": {<extracted fields as key/value pairs>}, '
        '"evidence": [<short direct quotes from the source supporting each extracted field>]}. '
        'Never include a "verified" field under any circumstances; verification is decided by a '
        "separate process, never by you."
    ),
    Task.MATCH_EXPLANATION: (
        "Explain why the scholarship matches the given profile, using only the source text "
        "below. Address the profile's owner directly in second person ('you', 'your') - this is "
        "a personalized answer to the specific person who asked, never a third-person "
        "description of 'the applicant' or 'the candidate'. This is a suggestion, not a "
        "guarantee of eligibility - phrase eligibility in tentative terms such as 'you might be "
        "eligible' or 'you may be eligible', never as a flat assertion like 'you are eligible'. "
        "Only describe facts about the person's profile that are explicitly present in the "
        "profile data. Do not mention scholarship evidence freshness, verification recency, "
        "ranking "
        "scores or other internal metadata in the user-facing explanation. These are system "
        "checks, not profile facts supplied by the person. "
        'Return only a JSON object of exactly this shape: {"explanation": "<a concise, '
        'second-person explanation>", "evidence": [<short direct quotes from the source '
        'supporting the explanation>]}. Never include a "verified" field under any '
        "circumstances; verification is decided by a separate process, never by you."
    ),
    Task.CLASSIFY_SOURCE_PAGE: (
        "Classify the source page below by what it contains. Return only a JSON object of "
        'exactly this shape: {"page_type": "<one of: individual, list, aggregator, '
        'not_a_scholarship>", "reasons": [<short phrases explaining the classification>], '
        '"evidence": [<short direct quotes from the source supporting the classification>]}. '
        'Use "individual" when the page describes exactly one award, "list" when it describes '
        'several distinct named awards, "aggregator" when it is a directory or search listing '
        'that links out to awards described elsewhere, and "not_a_scholarship" when it '
        "describes no funding opportunity at all. Classify only what the page actually "
        "contains; do not infer awards that are merely linked or implied. " + NEVER_VERIFIES
    ),
    Task.SPLIT_LIST_CANDIDATES: (
        "The source below is a page listing several distinct scholarships. Separate it into "
        "one entry per distinct named award. Return only a JSON object of exactly this shape: "
        '{"candidates": [{"title": "<the award name as written>", "url": "<its own '
        'link if the page gives one, otherwise null>", "excerpt": "<the verbatim text from '
        'this page describing this award>", "heading": "<the section heading or list position '
        'this award appeared under, if any>"}], "evidence": [<short direct quotes locating '
        "each award on the page>]}. Copy titles and excerpts verbatim from the source; never "
        "paraphrase, summarize, complete or correct them. Do not merge two awards into one "
        "entry, do not split one award into several, and do not add awards that are only "
        "linked to rather than described. If the page turns out to describe no distinct "
        "awards, return an empty candidates list. " + NEVER_VERIFIES
    ),
    Task.EXTRACT_SCHOLARSHIP_FACTS: (
        "Extract scholarship details from the source text below. Return only a JSON object of "
        'exactly this shape: {"candidate": {<extracted fields as key/value pairs>}, '
        '"evidence": [<short direct quotes from the source supporting each extracted field>]}. '
        "Extract only what the source states. Omit a field entirely rather than guessing it, "
        "and never carry a value over from general knowledge about the provider. Quote amounts, "
        "dates and durations in the source own words and units; do not convert currencies, "
        "reformat dates, or resolve relative dates such as 'next spring'. " + NEVER_VERIFIES
    ),
    Task.COMPARE_OFFICIAL_EVIDENCE: (
        "The source below contains a set of previously extracted facts and the text of an "
        "official page for the same award. Report how they relate. Return only a JSON object "
        'of exactly this shape: {"comparisons": [{"claim_path": "<the fact being compared>", '
        '"reported_value": "<value from the extracted facts>", "official_value": "<value '
        'found on the official page, or null if absent>", "relationship": "<one of: supported, '
        'contradicted, absent>"}], "contradictions": [<short phrases naming each direct '
        'conflict>], "evidence": [<short direct quotes from the official page>]}. Report only '
        "what the official page states. A fact the official page does not mention is "
        "'absent', never 'contradicted'. You are describing how two texts relate, not deciding "
        "whether the award is genuine, current or publishable - that decision is made "
        "elsewhere, from what you report. " + NEVER_VERIFIES
    ),
    Task.EXTRACT_ELIGIBILITY_REQUIREMENTS: (
        "Extract the academic and eligibility requirements stated in the source below. Return "
        'only a JSON object of exactly this shape: {"rules": [{"requirement_type": "<what the '
        'rule constrains, e.g. academic_result, nationality, study_level, field_of_study>", '
        '"raw_value": "<the requirement exactly as the source words it>", "scale_type": "<for '
        "an academic result, one of: gpa_4_0, gpa_5_0, percentage, uk_honours, letter_grade, "
        'qualitative, not_stated, unknown; otherwise null>", "scale_max": <the maximum of that '
        'scale as a number, or null>, "measurement_basis": "<one of: cumulative, final_year, '
        'degree_classification, unknown>", "scope": {"programmes": [<programmes this rule '
        'applies to, empty if it applies to all>], "countries": [<applicant countries this '
        'rule applies to, empty if it applies to all>]}, "source_wording": "<the full '
        'sentence containing the requirement, verbatim>"}], "evidence": [<short direct quotes '
        "from the source supporting each rule>]}. Record every requirement in the scale and "
        "wording the source itself uses. Never convert between grading systems - a 3.0/4.0 is "
        "not a 75%, a 2:1 is not a GPA, and inventing an equivalence makes a real applicant "
        "result unrecoverable. Where the source is vague, such as 'a strong academic record', "
        'record it verbatim with scale_type "qualitative" rather than inventing a number. '
        'Where no requirement is stated, use "not_stated" rather than assuming a typical one. '
        + NEVER_VERIFIES
    ),
}


_JSON_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def _parse_output(content: str | None, finish_reason: str | None, model: str, task: Task) -> Any:
    """Turn a model reply into JSON, or fail with a reason worth reading.

    Every failure here used to surface as `provider_unavailable`, which
    points at the provider when the provider answered perfectly well - a
    whole investigation was spent looking for an outage that had not
    happened. The three real causes are distinguished instead.

    Truncation is the one worth naming loudest: a reply cut off at the
    token ceiling is unrecoverable, and retrying it produces the same cut
    in the same place. It is a configuration problem wearing a transport
    problem's clothes.
    """
    if finish_reason == "length":
        logger.warning(
            "provider output truncated model=%s task=%s - raise max_output_tokens",
            model,
            task.value,
        )
        raise ProviderError("provider_output_truncated", retryable=False)
    if not content:
        logger.warning("provider returned no content model=%s task=%s", model, task.value)
        raise ProviderError("provider_output_empty", retryable=False)

    # `response_format=json_object` is requested, but not every model on
    # every route honours it - some still wrap the object in a markdown
    # fence or a sentence of preamble. Stripping that is cheaper than
    # discarding an otherwise correct answer.
    cleaned = _JSON_FENCE.sub("", content.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                pass
        logger.warning(
            "provider output was not JSON model=%s task=%s head=%r",
            model,
            task.value,
            cleaned[:120],
        )
        raise ProviderError("provider_output_invalid", retryable=False) from None


class LiteLLMProvider:
    """Thin adapter; keys stay in LiteLLM/provider environment, never in caller input."""

    async def complete(
        self, *, task: Task, source_data: dict[str, Any], model: str, max_tokens: int
    ) -> Any:
        if not model:
            raise ProviderError("provider_unavailable", retryable=False)
        try:
            from litellm import acompletion

            provider_name = model.split("/", 1)[0]
            api_key = settings.provider_keys.get(provider_name)
            result = await acompletion(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPTS[task]},
                    {"role": "user", "content": json.dumps(source_data)},
                ],
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
                timeout=45,
                **({"api_key": api_key} if api_key else {}),
            )

            usage = getattr(result, "usage", None)
            hidden_params = getattr(result, "_hidden_params", {}) or {}
            cost = hidden_params.get("response_cost")
            choice = result.choices[0]
            return CompletionResult(
                output=_parse_output(
                    choice.message.content,
                    getattr(choice, "finish_reason", None),
                    model,
                    task,
                ),
                input_tokens=getattr(usage, "prompt_tokens", None),
                output_tokens=getattr(usage, "completion_tokens", None),
                estimated_cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
            )
        except ProviderError:
            # Already categorised - a truncated or unparseable reply is not
            # the same failure as the provider being unreachable, and
            # re-wrapping it here would erase that.
            raise
        except Exception as exc:
            name = type(exc).__name__.lower()
            retryable = any(x in name for x in ("timeout", "ratelimit", "serviceunavailable"))
            logger.warning("provider call failed model=%s error=%s", model, exc, exc_info=True)
            raise ProviderError(
                "provider_transient" if retryable else "provider_error", retryable
            ) from exc
