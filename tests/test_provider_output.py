"""Turning a model reply into JSON, and failing legibly when it will not.

Every failure here used to surface as `provider_unavailable`, which points
at the provider when the provider answered perfectly well. A whole
investigation went looking for an outage that had not happened.
"""

import pytest

from app.domain.ai_router import ProviderError, Task
from app.infra.providers import _parse_output

TASK = Task.CLASSIFY_SOURCE_PAGE


def parse(content, finish_reason="stop"):
    return _parse_output(content, finish_reason, "test/model", TASK)


def test_plain_json_is_returned():
    assert parse('{"page_type": "list"}') == {"page_type": "list"}


@pytest.mark.parametrize(
    "content",
    [
        '```json\n{"page_type": "list"}\n```',
        '```\n{"page_type": "list"}\n```',
        'Here is the result:\n{"page_type": "list"}',
        '{"page_type": "list"}\n\nHope that helps.',
    ],
)
def test_a_reply_wrapped_in_prose_or_a_fence_is_still_read(content):
    """`response_format=json_object` is requested, but not every model on
    every route honours it. Discarding an otherwise correct answer over a
    markdown fence is the expensive choice."""
    assert parse(content) == {"page_type": "list"}


def test_a_truncated_reply_is_named_as_such():
    """The one worth naming loudest: a reply cut off at the token ceiling
    is unrecoverable, and the retry truncates in the same place. It is a
    configuration problem wearing a transport problem's clothes."""
    with pytest.raises(ProviderError) as caught:
        parse('{"candidates": [{"title": "unterminated', finish_reason="length")

    assert caught.value.category == "provider_output_truncated"
    assert caught.value.retryable is False


def test_an_empty_reply_is_distinguished_from_a_malformed_one():
    with pytest.raises(ProviderError) as caught:
        parse(None)

    assert caught.value.category == "provider_output_empty"


def test_output_that_is_not_json_at_all_is_reported_as_invalid():
    with pytest.raises(ProviderError) as caught:
        parse("I cannot help with that request.")

    assert caught.value.category == "provider_output_invalid"


def test_none_of_these_are_retryable():
    """Retrying a model that returned prose returns prose again, at the
    same cost. The caller should see the real reason instead."""
    for content, reason in [
        ('{"a": "cut', "length"),
        (None, "stop"),
        ("not json", "stop"),
    ]:
        with pytest.raises(ProviderError) as caught:
            parse(content, finish_reason=reason)
        assert caught.value.retryable is False
