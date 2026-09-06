import logging
from typing import Any

from app.core.config import settings
from app.domain.ai_router import CompletionResult, ProviderError, Task

logger = logging.getLogger(__name__)


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
