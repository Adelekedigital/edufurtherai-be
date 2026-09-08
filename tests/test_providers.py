import asyncio
import sys
from types import SimpleNamespace

import pytest

from app.domain.ai_router import ProviderError, Task
from app.infra.providers import LiteLLMProvider


class RateLimitError(Exception):
    pass


class ServiceUnavailableError(Exception):
    pass


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (TimeoutError("timed out"), True),
        (RateLimitError("rate limited"), True),
        (ServiceUnavailableError("unavailable"), True),
        (ValueError("invalid response"), False),
    ],
)
def test_litellm_provider_classifies_transport_failures(monkeypatch, error, retryable):
    async def acompletion(**kwargs):
        raise error

    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(acompletion=acompletion))
    with pytest.raises(ProviderError) as caught:
        asyncio.run(
            LiteLLMProvider().complete(
                task=Task.SCHOLARSHIP_EXTRACTION,
                source_data={"excerpt": "bounded"},
                model="openai/test",
                max_tokens=100,
            )
        )
    assert caught.value.retryable is retryable

def test_match_explanation_prompt_does_not_attribute_internal_evidence_to_profile(monkeypatch):
    captured = {}

    async def acompletion(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"explanation":"ok","evidence":[]}'))],
            usage=None,
            _hidden_params={},
        )

    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(acompletion=acompletion))
    asyncio.run(
        LiteLLMProvider().complete(
            task=Task.MATCH_EXPLANATION,
            source_data={
                "profile": {"program_level": "masters", "country": "Ghana"},
                "scholarship": {"last_verified_at": "2026-08-01T00:00:00Z"},
            },
            model="openai/test",
            max_tokens=100,
        )
    )

    prompt = captured["messages"][0]["content"]
    assert "Only describe facts about the person's profile" in prompt
    assert "Do not mention scholarship evidence freshness, verification recency" in prompt
