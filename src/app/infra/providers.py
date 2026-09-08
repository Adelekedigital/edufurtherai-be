import json
import logging
from typing import Any

from app.core.config import settings
from app.domain.ai_router import CompletionResult, ProviderError, Task

logger = logging.getLogger(__name__)

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
        "Return only a JSON object of exactly this shape: {\"explanation\": \"<a concise, "
        'second-person explanation>", "evidence": [<short direct quotes from the source '
        'supporting the explanation>]}. Never include a "verified" field under any '
        "circumstances; verification is decided by a separate process, never by you."
    ),
}


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
            return CompletionResult(
                output=json.loads(result.choices[0].message.content),
                input_tokens=getattr(usage, "prompt_tokens", None),
                output_tokens=getattr(usage, "completion_tokens", None),
                estimated_cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
            )
        except Exception as exc:
            name = type(exc).__name__.lower()
            retryable = any(x in name for x in ("timeout", "ratelimit", "serviceunavailable"))
            logger.warning("provider call failed model=%s error=%s", model, exc, exc_info=True)
            raise ProviderError(
                "provider_transient" if retryable else "provider_error", retryable
            ) from exc
