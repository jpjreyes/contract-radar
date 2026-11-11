"""Tracing (OpenTelemetry): one trace per request, one span per step, so latency and cost can be read per stage.

    ask ─┬─ retrieve (planned)  ─┬─ keyword ─ vector ─ rerank
         ├─ reveal               (how many chunks came back unmasked)
         ├─ llm                  (model, tokens, cost, seconds)
         ├─ verify_citations     (claims kept / dropped)
         └─ audit

Spans follow the OpenTelemetry GenAI conventions for model calls (gen_ai.*) and set OpenInference span kinds
(LLM / RETRIEVER / RERANKER / CHAIN / TOOL) so LLM-aware viewers such as Arize Phoenix lay them out properly; any
OTLP backend can receive them. Tracing is off unless an endpoint is configured, and spans carry ids, counts, tokens,
cost and timing but no contract text or questions (a trace store must not become an unmasked copy of the data).
CC_TRACE_CONTENT=1 adds the question and the model's raw output, for local debugging only.

Local viewer:  uvx --from arize-phoenix phoenix serve      (UI on http://localhost:6006)
Then:          CC_TRACE_ENDPOINT=http://localhost:6006/v1/traces uv run uvicorn contract_copilot.api:app --port 8071
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import trace

_configured = False
tracer = trace.get_tracer("contract_copilot")

# USD per million tokens (input, output), list prices; used for the cost attribute on every model span.
PRICES = {"haiku": (0.10, 0.50)}


def setup(service: str = "contract-copilot") -> bool:
    """Install an OTLP exporter if CC_TRACE_ENDPOINT (or the standard OTEL variable) is set. Idempotent."""
    global _configured
    endpoint = os.environ.get("CC_TRACE_ENDPOINT") or os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    if _configured or not endpoint:
        return _configured
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    provider = TracerProvider(resource=Resource.create({"service.name": service}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    _configured = True
    return True


def content_allowed() -> bool:
    return os.environ.get("CC_TRACE_CONTENT") == "1"


@contextmanager
def span(name: str, kind: str = "CHAIN", **attrs) -> Iterator[trace.Span]:
    """A span with an OpenInference kind and plain attributes (None values are dropped)."""
    with tracer.start_as_current_span(name) as s:
        s.set_attribute("openinference.span.kind", kind)
        for k, v in attrs.items():
            if v is not None:
                s.set_attribute(k, v)
        yield s


def cost_usd(model: str, tokens_in: int, tokens_out: int) -> float | None:
    for key, (p_in, p_out) in PRICES.items():
        if key in model.lower():
            return round((tokens_in * p_in + tokens_out * p_out) / 1e6, 6)
    return None


class TracedBackend:
    """Wraps any model backend: every call becomes an LLM span with model, tokens, cost and latency."""

    def __init__(self, inner, backend_name: str):
        self.inner = inner
        self.backend_name = backend_name

    def complete(self, system: str, prompt: str, schema: dict, model: str | None = None, images=None):
        kwargs = {"images": images} if images else {}
        if model:
            kwargs["model"] = model
        with span("llm", "LLM", **{"gen_ai.system": "anthropic", "gen_ai.operation.name": "chat",
                                   "cc.backend": self.backend_name, "cc.prompt_chars": len(prompt)}) as s:
            res = self.inner.complete(system, prompt, schema, **kwargs)
            s.set_attribute("gen_ai.request.model", res.model)
            s.set_attribute("gen_ai.usage.input_tokens", res.input_tokens)
            s.set_attribute("gen_ai.usage.output_tokens", res.output_tokens)
            s.set_attribute("cc.model_seconds", round(res.latency_s, 2))
            cost = cost_usd(res.model, res.input_tokens, res.output_tokens)
            if cost is not None:
                s.set_attribute("cc.cost_usd", cost)
            if content_allowed():
                s.set_attribute("output.value", str(res.data)[:4000])
            return res
