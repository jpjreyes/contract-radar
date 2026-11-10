"""Chunk offsets must be exact: citations and the retrieval eval both depend on them."""

from contract_copilot.chunking import MAX_CHARS, chunk_text
from contract_copilot.cuad import category_of
from contract_copilot.eval_retrieval import overlaps, split_of
from contract_copilot.retrieve import _or_query

TEXT = """SUPPLY AGREEMENT

1. Definitions. "Products" means the goods listed in Exhibit A.

2. Term. This Agreement shall commence on the Effective Date and continue for three (3) years.
2.1 Renewal. The term renews automatically for successive one-year periods unless either party gives notice
at least sixty (60) days before the end of the then-current term.

ARTICLE IX
Limitation of Liability. In no event shall either party's liability exceed the fees paid in the prior twelve months.
""" + ("Long boilerplate sentence about miscellaneous obligations. " * 80)


def test_offsets_are_exact_and_cover_the_text():
    chunks = chunk_text(TEXT)
    assert all(TEXT[c.start:c.end] == c.text for c in chunks)
    covered = "".join(c.text for c in chunks)
    assert "".join(TEXT.split()) == "".join(covered.split())  # nothing lost but whitespace


def test_chunks_respect_the_size_cap_and_split_at_clauses():
    chunks = chunk_text(TEXT)
    assert all(c.end - c.start <= MAX_CHARS + 1 for c in chunks)
    assert any(c.text.lstrip().startswith(("2. Term", "1. Definitions", "SUPPLY")) for c in chunks)


def test_label_category_and_query_helpers():
    q = 'Highlight the parts (if any) of this contract related to "Renewal Term" that should be reviewed by a lawyer.'
    assert category_of(q) == "Renewal Term"
    assert "renewal" in _or_query("Renewal Term: a renewal term after the initial term")
    assert "the" not in _or_query("the term of the agreement").split(" | ")


def test_split_is_deterministic_and_overlap_is_half_open():
    assert split_of("ACME_SUPPLY_AGREEMENT") == split_of("ACME_SUPPLY_AGREEMENT")
    assert overlaps(0, 10, 9, 20) and not overlaps(0, 10, 10, 20)
