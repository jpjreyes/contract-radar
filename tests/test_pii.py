"""Personal-data detection and masking (C3), without the database."""

import random

import pytest

from contract_copilot import pii

BLOCK = """IN WITNESS WHEREOF, the parties have executed this Agreement.

ACME WIDGETS, INC. ("Licensee")                 NORTHWIND TRADING LLC

By: /s/ Jane Q. Doorley                          By: /s/ Marcus Ellery-Vance
Name: Jane Q. Doorley                            Name: Marcus Ellery-Vance
Title: Chief Executive Officer                   Title: General Counsel

Notices to Licensee: 1200 Harbor View Drive, Suite 410, Seattle, WA 98101, Attention: Jane Q. Doorley,
email jdoorley@acmewidgets.com, telephone (206) 555-0147. Ms. Doorley shall forward notices to the Licensee."""


@pytest.fixture(scope="module")
def spans():
    return pii.detect(BLOCK)


def found(spans, kind):
    return {s.text for s in spans if s.type == kind}


def test_detects_people_contacts_and_addresses(spans):
    assert {"Jane Q. Doorley", "Marcus Ellery-Vance", "Ms. Doorley"} <= found(spans, "PERSON")
    assert found(spans, "EMAIL") == {"jdoorley@acmewidgets.com"}
    assert found(spans, "PHONE") == {"(206) 555-0147"}
    assert any(a.startswith("1200 Harbor View Drive, Suite 410") for a in found(spans, "ADDRESS"))


def test_companies_titles_defined_terms_and_places_stay(spans):
    masked = " ".join(s.text for s in spans)
    for kept in ("ACME", "NORTHWIND", "Licensee", "Chief Executive Officer", "General Counsel", "Seattle", "WA 98101"):
        assert kept not in masked


def test_same_person_gets_the_same_placeholder(spans):
    toks = pii.tokens(BLOCK, spans)
    jane = {t for s, t in zip(spans, toks) if "Doorley" in s.text and s.text.startswith("Jane")}
    assert len(jane) == 1


def test_offset_map_round_trips_to_the_original(spans):
    toks = pii.tokens(BLOCK, spans)
    start, end = 40, len(BLOCK) - 5
    masked, mapping = pii.mask_range(BLOCK, start, end, spans, toks)
    assert "jdoorley" not in masked and "[EMAIL_1]" in masked
    original = BLOCK[start:end]
    rng = random.Random(0)
    outside = [i for i in range(len(masked) + 1) if not any(ms < i < me for ms, me, _, _ in mapping)]
    for _ in range(200):
        a, b = sorted(rng.sample(outside, 2))
        oa, ob = pii.to_original(a, mapping, "start"), pii.to_original(b, mapping, "end")
        plain = [(ms, me) for ms, me, _, _ in mapping if a <= ms and me <= b]
        if not plain:  # no placeholder inside: the text must be identical
            assert masked[a:b] == original[oa:ob]
        else:  # placeholders inside: the original span covers the masked values
            assert ob - oa >= 0 and original[oa:ob].count("@") >= masked[a:b].count("[EMAIL")


def test_section_numbers_and_prose_are_not_contacts():
    text = "See Section 16 8.4 and 8.5. The parties agree that one clause shall in no way limit another."
    assert pii.detect(text) == []
