"""Registry completeness and per-task output validation.

Adding a Task without its policy, prompt or validator fails at runtime in a
way that looks like a provider problem: a missing SYSTEM_PROMPTS entry raises
KeyError inside the provider call, which is caught and reported as
`provider_unavailable`. These tests turn that into an import-time-obvious
failure instead.
"""

import pytest

from app.domain.ai_router import (
    MAX_BIO_CHARS,
    POLICIES,
    PROMPT_VERSIONS,
    Task,
    normalize_output,
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
        Task.BIO_DRAFT: {"text": "I am studying engineering in Lagos."},
        Task.BIO_IMPROVE: {"text": "I am studying engineering in Lagos."},
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


def test_product_task_surfaces_do_not_overlap():
    surfaces = [
        {t for t, p in POLICIES.items() if product in p.allowed_products}
        for product in ("scholarship_finder", "edufurther_agent", "edufurther_platform")
    ]

    assert all(surfaces)
    for i, a in enumerate(surfaces):
        for b in surfaces[i + 1 :]:
            assert not a & b


def test_platform_is_authorized_for_exactly_the_bio_tasks():
    platform = {t for t, p in POLICIES.items() if "edufurther_platform" in p.allowed_products}

    assert platform == {Task.BIO_DRAFT, Task.BIO_IMPROVE}


@pytest.mark.parametrize("task", [Task.BIO_DRAFT, Task.BIO_IMPROVE])
def test_prompt_versions_match_the_platform_pattern(task):
    """The platform validates stored versions against this pattern with the
    task name as prefix; a version it rejects is a bio it cannot save."""
    import re

    version = PROMPT_VERSIONS[task]
    assert re.fullmatch(r"[a-z][a-z0-9_]*-v[0-9]+", version)
    assert version.startswith(f"{task.value}-v")


@pytest.mark.parametrize("task", [Task.BIO_DRAFT, Task.BIO_IMPROVE])
def test_bio_accepts_plain_prose_up_to_the_cap(task):
    assert validate_candidate(task, {"text": "a" * MAX_BIO_CHARS})
    assert validate_candidate(task, {"text": "I moved from Accra.\n\nNow I mentor - gladly."})
    # Non-ASCII counts per character, not per byte.
    assert validate_candidate(task, {"text": "é" * MAX_BIO_CHARS})
    # Ordinary prose that only looks like markup.
    assert validate_candidate(task, {"text": "I love physics <3 and think maths > everything."})
    assert validate_candidate(task, {"text": "My GPA went from <3.0 to >3.8."})
    assert validate_candidate(task, {"text": "Hi.\n2019. I moved to Leeds."})
    # A plain hyphen and the en dash are not em dashes.
    assert validate_candidate(task, {"text": "Leeds - then London, 2019\u20132021."})


@pytest.mark.parametrize(
    "output",
    [
        {"text": "a" * (MAX_BIO_CHARS + 1)},
        {"text": ""},
        {"text": "   \n "},
        {"text": None},
        {"text": ["a list"]},
        {},
        {"bio": "wrong key"},
        {"text": "fine", "notes": "extra key"},
        {"text": "I <b>love</b> physics."},
        {"text": "I **love** physics."},
        {"text": "I love `physics`."},
        {"text": "# About me\nI love physics."},
        {"text": "I offer:\n- essay review\n- visa advice"},
        {"text": "I offer:\n1. essay review"},
        {"text": "I moved to Leeds \u2015 and stayed."},
        {"text": "I moved to Leeds \u2e3a and stayed."},
        {"text": "Always on time \u231a and up early \u23f0."},
        {"text": "I *really* enjoy mentoring."},
        {"text": "See [my blog](https://example.com)."},
        {"text": "> A quote I live by."},
        {"text": "I write <img src=x onerror=alert(1) and stop"},
        {"text": "I moved to Leeds \u2014 and stayed."},
        {"text": "Happy to help \U0001f600"},
        {"text": "Ask me anything \u2764\ufe0f"},
        {"text": "Five stars \u2b50"},
        {"text": "Thumbs up \U0001f44d\U0001f3fd"},
    ],
)
@pytest.mark.parametrize("task", [Task.BIO_DRAFT, Task.BIO_IMPROVE])
def test_bio_rejects_anything_but_one_plain_text_field(task, output):
    assert validate_candidate(task, output) is False


def test_bio_improve_admits_a_full_length_non_ascii_text():
    """1,500 characters of the person's own text must fit even when every
    one becomes an escape in json.dumps - an emoji is a twelve-byte
    surrogate pair on the wire."""
    payload = {"role": "mentee", "text": "\U0001f600" * MAX_BIO_CHARS}

    assert source_data_bytes(payload) <= POLICIES[Task.BIO_IMPROVE].max_source_bytes


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


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("I moved to Leeds \u2014 and stayed.", "I moved to Leeds, and stayed."),
        ("I moved to Leeds\u2014and stayed.", "I moved to Leeds, and stayed."),
        ("Leeds \u2015 then London \u2e3a happily.", "Leeds, then London, happily."),
        ("Happy to help \U0001f600", "Happy to help"),
        ("Happy to help \U0001f600.", "Happy to help."),
        ("Ask me anything \u2764\ufe0f!", "Ask me anything!"),
        ("Thumbs up \U0001f44d\U0001f3fd and go", "Thumbs up and go"),
        ("Family \U0001f468\u200d\U0001f469\u200d\U0001f467 first", "Family first"),
        ("\U0001f393 Graduate student", "Graduate student"),
        ("I stayed \u2014.", "I stayed."),
        ("  Plain text, untouched - with a hyphen.  ", "Plain text, untouched - with a hyphen."),
        ("Line one.\n\nLine two \u2014 continued.", "Line one.\n\nLine two, continued."),
    ],
)
@pytest.mark.parametrize("task", [Task.BIO_DRAFT, Task.BIO_IMPROVE])
def test_bio_em_dashes_and_emoji_are_repaired_not_rejected(task, raw, clean):
    output = normalize_output(task, {"text": raw})

    assert output == {"text": clean}
    assert validate_candidate(task, output)


def test_a_zero_width_joiner_outside_an_emoji_is_kept():
    """Indic scripts use the joiner in ordinary text; only one binding an
    emoji sequence together is removed."""
    text = "\u0915\u094d\u200d\u0937"

    assert normalize_output(Task.BIO_IMPROVE, {"text": text}) == {"text": text}


def test_markup_is_not_repaired():
    output = normalize_output(Task.BIO_DRAFT, {"text": "I **love** physics."})

    assert validate_candidate(Task.BIO_DRAFT, output) is False


@pytest.mark.parametrize("output", [None, [], {"text": None}, {"bio": "x"}])
def test_normalizer_passes_odd_shapes_through(output):
    assert normalize_output(Task.BIO_DRAFT, output) == output


def test_other_tasks_are_never_normalized():
    output = {"explanation": "Yes \u2014 maybe \U0001f600", "evidence": []}

    assert normalize_output(Task.MATCH_EXPLANATION, output) is output
