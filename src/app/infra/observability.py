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
