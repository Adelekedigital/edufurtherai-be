"""Registry completeness and per-task output validation.

Adding a Task without its policy, prompt or validator fails at runtime in a
way that looks like a provider problem: a missing SYSTEM_PROMPTS entry raises
KeyError inside the provider call, which is caught and reported as
`provider_unavailable`. These tests turn that into an import-time-obvious
failure instead.
"""

import pytest

from app.domain.ai_router import (
    POLICIES,
    PROMPT_VERSIONS,
    Task,
    source_data_bytes,
    validate_candidate,
)
from app.infra.providers import SYSTEM_PROMPTS


@pytest.mark.parametrize("task", list(Task))
def test_every_task_has_a_policy_prompt_and_prompt_version(task):
    assert task in POLICIES
    assert task in SYSTEM_PROMPTS
    assert task in PROMPT_VERSIONS
    assert POLICIES[task].allowed_products
    assert POLICIES[task].max_output_tokens > 0
    assert POLICIES[task].max_source_bytes > 0


@pytest.mark.parametrize("task", list(Task))
def test_every_prompt_forbids_the_model_asserting_verification(task):
    """The automation boundary is prompt-side as well as validator-side.
    Both halves must cover every task, not just the original two."""
    assert '"verified"' in SYSTEM_PROMPTS[task]


@pytest.mark.parametrize("task", list(Task))
def test_a_verified_key_is_rejected_for_every_task(task):
    valid = {
        Task.SCHOLARSHIP_EXTRACTION: {"candidate": {}, "evidence": []},
        Task.EXTRACT_SCHOLARSHIP_FACTS: {"candidate": {}, "evidence": []},
        Task.MATCH_EXPLANATION: {"explanation": "because", "evidence": []},
        Task.CLASSIFY_SOURCE_PAGE: {"page_type": "individual", "evidence": []},
        Task.SPLIT_LIST_CANDIDATES: {"candidates": [], "evidence": []},
        Task.COMPARE_OFFICIAL_EVIDENCE: {
            "comparisons": [],
            "contradictions": [],
            "evidence": [],
        },
        Task.EXTRACT_ELIGIBILITY_REQUIREMENTS: {"rules": [], "evidence": []},
    }[task]

    assert validate_candidate(task, valid) is True
    assert validate_candidate(task, valid | {"verified": True}) is False
    assert validate_candidate(task, valid | {"verified": False}) is False


@pytest.mark.parametrize(
    "output",
    [
        {"page_type": "something_else", "evidence": []},
        {"page_type": "individual"},
        {"evidence": []},
        {"page_type": None, "evidence": []},
    ],
)
def test_classification_rejects_an_unlisted_page_type(output):
    assert validate_candidate(Task.CLASSIFY_SOURCE_PAGE, output) is False


@pytest.mark.parametrize(
    "output",
    [
        {"candidates": ["a string, not a candidate"], "evidence": []},
        {"candidates": {}, "evidence": []},
        {"candidates": [{"title": "A"}]},
    ],
)
def test_split_rejects_candidates_that_are_not_objects(output):
    assert validate_candidate(Task.SPLIT_LIST_CANDIDATES, output) is False


def test_split_accepts_an_empty_candidate_list():
    """A list page that turns out to describe no distinct awards is a real,
    reportable answer - not a malformed response to retry on another model."""
    assert validate_candidate(Task.SPLIT_LIST_CANDIDATES, {"candidates": [], "evidence": []})


def test_comparison_requires_both_comparisons_and_contradictions():
    assert not validate_candidate(
        Task.COMPARE_OFFICIAL_EVIDENCE, {"comparisons": [], "evidence": []}
    )
    assert not validate_candidate(
        Task.COMPARE_OFFICIAL_EVIDENCE, {"contradictions": [], "evidence": []}
    )


def test_eligibility_requires_rule_objects():
    assert validate_candidate(
        Task.EXTRACT_ELIGIBILITY_REQUIREMENTS,
        {"rules": [{"requirement_type": "academic_result"}], "evidence": []},
    )
    assert not validate_candidate(
        Task.EXTRACT_ELIGIBILITY_REQUIREMENTS, {"rules": ["3.0/4.0"], "evidence": []}
    )


def test_an_unregistered_task_is_denied_rather_than_falling_through():
    """Before per-task validators this function ended in a bare `return`
    expressing the match_explanation shape, so any task without its own
    branch was validated against that one instead."""

    class Unregistered(str):
        pass

    match_shape = {"explanation": "x", "evidence": []}

    assert validate_candidate(Unregistered("not_a_task"), match_shape) is False


@pytest.mark.parametrize("output", [None, [], "text", 3])
def test_a_non_object_output_is_never_valid(output):
    assert validate_candidate(Task.SCHOLARSHIP_EXTRACTION, output) is False


def test_source_data_bytes_measures_the_json_the_provider_receives():
    import json

    payload = {"raw_excerpt": "£10,000 by 1 March"}

    assert source_data_bytes(payload) == len(json.dumps(payload).encode())


def test_agent_and_finder_task_surfaces_do_not_overlap():
    finder = {t for t, p in POLICIES.items() if "scholarship_finder" in p.allowed_products}
    agent = {t for t, p in POLICIES.items() if "edufurther_agent" in p.allowed_products}

    assert finder and agent
    assert not finder & agent


@pytest.mark.parametrize("page_type", [["list"], {"a": 1}, {"list"}, 3, None])
def test_an_unhashable_page_type_is_rejected_not_raised(page_type):
    """PAGE_TYPES is a frozenset, so `x in PAGE_TYPES` hashes x. A model
    returning `"page_type": ["list"]` is an ordinary malformation - and
    unguarded it raised TypeError out of validate_candidate into the
    execute loop, which catches only ProviderError. The request 500s, and
    because the idempotency key was already claimed with no stored
    response, every later retry of that key answers 409 REQUEST_IN_PROGRESS
    forever."""
    assert (
        validate_candidate(Task.CLASSIFY_SOURCE_PAGE, {"page_type": page_type, "evidence": []})
        is False
    )


def test_a_validator_that_raises_degrades_to_invalid():
    """Defence in depth: validators are fed raw model output, so any shape
    is possible. One that still raises must mean "not the declared shape",
    never an exception escaping into the request path."""
    import app.domain.ai_router as module

    original = module._VALIDATORS[Task.CLASSIFY_SOURCE_PAGE]

    def boom(output):
        raise RuntimeError("validator bug")

    module._VALIDATORS[Task.CLASSIFY_SOURCE_PAGE] = boom
    try:
        assert validate_candidate(Task.CLASSIFY_SOURCE_PAGE, {"evidence": []}) is False
    finally:
        module._VALIDATORS[Task.CLASSIFY_SOURCE_PAGE] = original
