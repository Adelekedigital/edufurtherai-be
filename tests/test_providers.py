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
