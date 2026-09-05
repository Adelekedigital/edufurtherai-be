from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import settings


@dataclass
class TraceHandle:
    reference: str | None = None
    span: Any = None

    def finish(self, status: str) -> None:
        if self.span is None:
            return
        try:
            self.span.update(metadata={"status": status})
            self.span.end()
        except Exception:
            return

    def start_generation(self, model: str, attempt: int) -> GenerationHandle:
        if self.span is None:
            return GenerationHandle()
        try:
            generation = self.span.start_observation(
                name="model-call",
                as_type="generation",
                model=model,
                metadata={"attempt": attempt},
            )
            return GenerationHandle(generation)
        except Exception:
            return GenerationHandle()


@dataclass
class GenerationHandle:
    generation: Any = None

    def finish(
        self,
        *,
        status: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        estimated_cost_usd: float | None = None,
        error_category: str | None = None,
    ) -> None:
        if self.generation is None:
            return
        try:
            metadata = {"status": status}
            if error_category:
                metadata["error_category"] = error_category
            self.generation.update(
                metadata=metadata,
                **(
                    {
                        "usage_details": {
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                        }
                    }
                    if input_tokens is not None or output_tokens is not None
                    else {}
                ),
                **(
                    {"cost_details": {"total": estimated_cost_usd}}
                    if estimated_cost_usd is not None
                    else {}
                ),
            )
            self.generation.end()
        except Exception:
            return


class LangfuseTracer:
    def __init__(self, client: Any = None) -> None:
        self.client = client
        if self.client is not None or not settings.langfuse_enabled:
            return
        if not settings.langfuse_public_key or not settings.langfuse_secret_key:
            return
        try:
            from langfuse import Langfuse

            self.client = Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_host,
                environment=settings.environment,
            )
        except Exception:
            self.client = None

    def start(
        self,
        *,
        request_id: str,
        product_id: str,
        feature_id: str,
        task: str,
        policy_version: str,
    ) -> TraceHandle:
        if self.client is None:
            return TraceHandle()
        try:
            trace_id = self.client.create_trace_id(seed=request_id)
            span = self.client.start_span(
                trace_context={"trace_id": trace_id},
                name="ai.execute",
                metadata={
                    "request_id": request_id,
                    "product_id": product_id,
                    "feature_id": feature_id,
                    "task": task,
                    "model_policy_version": policy_version,
                },
                version=policy_version,
            )
            reference = self.client.get_trace_url(trace_id=trace_id)
            return TraceHandle(reference=reference, span=span)
        except Exception:
            return TraceHandle()

    def flush(self) -> None:
        if self.client is None:
            return
        try:
            self.client.flush()
        except Exception:
            return
