"""Personal-data detection and masking (C3), without the database."""

import random

import pytest

from contract_radar import pii

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


V3 = """Inventors     Chen, Suzanne; & Kim, Jae-Hyeong; &  Lu, Chungji (Jay)        2007-0266286
Marketing Contact: Name: BaoQing (Angel) Song
If to OntoChem: OntoChem GmbH Blücherstr. 24, D-06120 Halle (Saale) Germany
IMG Center, Suite 100, 1360 East 9th Street, Cleveland, Ohio 44114
Attn: Alistair Hancock Attn: Andrew Kirby  Rubicon House  Guildford Road  West End, Surrey GU24 9PW
By: By: Jimmy Wayne Anderson Timothy Cabrera Its: Chairman
Confidential Page 5 of 7 Initials: CL MD Version 3
Governed by the laws of the State of Delaware, Inc. and Section 5, Article 3; the Company shall pay."""


def test_v3_lists_nicknames_initials_and_repeated_labels():
    people = found(pii.detect(V3), "PERSON")
    assert {"Chen, Suzanne", "Kim, Jae-Hyeong", "Lu, Chungji (Jay)", "BaoQing (Angel) Song", "Alistair Hancock",
            "Andrew Kirby", "Jimmy Wayne Anderson", "CL MD"} <= people
    assert not any(w in " ".join(people) for w in ("Delaware", "Section", "Article", "Attn"))


def test_v3_addresses_european_leading_unit_and_uk_postcode():
    addresses = found(pii.detect(V3), "ADDRESS")
    assert "Blücherstr. 24" in addresses
    assert "Suite 100, 1360 East 9th Street" in addresses
    assert {"Rubicon House", "Guildford Road"} <= addresses
    assert not any("Halle" in a or "Surrey" in a for a in addresses)  # city and county stay visible



def test_v3_firms_and_places_are_not_surname_lists():
    text = ("copies to:   Morgan, Lewis & Bockius LLP 1111 Pennsylvania Avenue\n"
            "Required Signature:           Seattle, Washington  98104\n")
    assert not found(pii.detect(text), "PERSON") & {"Morgan, Lewis", "Seattle, Washington"}
