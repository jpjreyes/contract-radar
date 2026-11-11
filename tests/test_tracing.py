"""Tracing: the span tree of one question, cost on the model span, and no question text in the trace by default."""

import os

import pytest
from dotenv import load_dotenv

load_dotenv()
pytestmark = pytest.mark.skipif(not os.environ.get("APP_DATABASE_URL"), reason="needs the cc_app role")


@pytest.fixture(scope="module")
def exporter():
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    exp = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exp))
    trace.set_tracer_provider(provider)
    return exp


class FakeModel:
    """Quotes the first source verbatim, so one claim verifies; no model call, no cost."""

    def complete(self, system, prompt, schema, model=None, images=None):
        from contract_copilot.llm import LLMResult
        first = prompt.split("[S1]", 1)[1].split("\n", 2)[2][:60].strip()
        return LLMResult({"answerable": True, "clause_present": True,
                          "claims": [{"text": "x", "sources": [{"id": "S1", "quote": first}]}]}, 1200, 80, 0.5, "haiku")


def test_one_question_produces_the_expected_span_tree_without_content(exporter):
    from contract_copilot.ask import ask
    from contract_copilot.tracing import TracedBackend
    exporter.clear()
    secret_question = "Who signed for Kiromic and what is the termination notice period?"
    a, audit_id = ask("analyst", secret_question, backend=TracedBackend(FakeModel(), "fake"))
    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert {"ask", "retrieve", "rerank", "reveal", "llm", "verify_citations", "audit"} <= set(spans)
    root = spans["ask"]
    for name in ("retrieve", "llm", "verify_citations", "audit"):
        assert spans[name].context.trace_id == root.context.trace_id  # one trace per request
    llm = spans["llm"].attributes
    assert llm["gen_ai.usage.input_tokens"] == 1200 and llm["cc.cost_usd"] == pytest.approx(0.00016)
    assert spans["verify_citations"].attributes["cc.claims_kept"] == 1
    assert root.attributes["enduser.id"] == "analyst" and root.attributes["cc.audit_id"] == audit_id
    everything = " ".join(str(v) for s in exporter.get_finished_spans() for v in s.attributes.values())
    assert "Kiromic" not in everything and "termination" not in everything
