"""Citation checks in code: a claim survives only with a quote that really is in the cited passage."""

from contract_copilot.answer import _norm, locate

TEXT = ("2.1 Renewal. The term renews automatically for successive one-year periods unless either party gives\n"
        "notice at least sixty (60) days before the end of the then-current term.")


def test_exact_and_whitespace_insensitive_quotes_are_located():
    s, e = locate("renews automatically for successive one-year periods", TEXT)
    assert TEXT[s:e] == "renews automatically for successive one-year periods"
    s, e = locate("unless either party gives notice at least sixty (60) days", TEXT)
    assert _norm(TEXT[s:e]) == "unless either party gives notice at least sixty (60) days"


def test_near_verbatim_quote_is_accepted_but_invented_text_is_not():
    assert locate("renews automaticaly for successive one-year periods", TEXT) is not None  # one typo
    assert locate("the agreement may be terminated for convenience on 30 days notice", TEXT) is None
    assert locate("the", TEXT) is None  # too short to prove anything


class FakeBackend:
    def __init__(self, data):
        self.data = data

    def complete(self, system, prompt, schema, model="haiku", images=None):
        from contract_copilot.llm import LLMResult
        return LLMResult(self.data, 10, 5, 0.1, model)
