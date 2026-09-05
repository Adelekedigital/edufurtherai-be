from typing import Any

from app.core.config import settings
from app.domain.ai_router import ProviderError, Task


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
                    {"role": "system", "content": "Return only the approved candidate JSON."},
                    {"role": "user", "content": str(source_data)},
                ],
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
                timeout=45,
                **({"api_key": api_key} if api_key else {}),
            )
            import json

            return json.loads(result.choices[0].message.content)
        except Exception as exc:
            name = type(exc).__name__.lower()
            retryable = any(x in name for x in ("timeout", "ratelimit", "serviceunavailable"))
            raise ProviderError(
                "provider_transient" if retryable else "provider_error", retryable
            ) from exc
