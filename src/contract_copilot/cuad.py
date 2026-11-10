"""CUAD v1 loader: contract texts, contract types, and the lawyers' labels.

The contract text comes from CUAD_v1.json, not the TXT files, because the labels' character offsets point into that
exact text (D5): retrieval evals can then check whether a retrieved chunk covers a labeled span without fuzzy matching.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CUAD_DIR = ROOT / "data" / "raw" / "CUAD_v1"

# Synthetic ownership for the permission model (documented in CLIENT_BRIEF.md): which team owns a contract type.
BUSINESS_UNIT = {
    "Procurement": {"Supply", "Distributor", "Manufacturing", "Outsourcing", "Transportation", "Reseller", "Agency",
                    "Franchise"},
    "Marketing": {"Marketing", "Promotion", "Sponsorship", "Endorsement", "Co-Branding", "Affiliate"},
}
REGIONS = ("North America", "EMEA", "LATAM")


@dataclass
class Span:
    start: int
    end: int
    text: str


@dataclass
class Label:
    category: str          # e.g. "Renewal Term"
    question: str
    spans: list[Span] = field(default_factory=list)


@dataclass
class Contract:
    name: str
    text: str
    contract_type: str
    labels: list[Label]

    @property
    def business_unit(self) -> str:
        return next((bu for bu, types in BUSINESS_UNIT.items() if self.contract_type in types), "Legal")

    @property
    def region(self) -> str:
        return REGIONS[int(hashlib.sha256(self.name.encode()).hexdigest(), 16) % len(REGIONS)]

    @property
    def sha(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()[:16]


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def contract_types() -> dict[str, str]:
    """Normalized file stem -> contract type, from the folder layout full_contract_pdf/Part_*/<Type>/<file>.pdf."""
    types = {}
    for pdf in (CUAD_DIR / "full_contract_pdf").glob("Part_*/*/*"):
        if pdf.suffix.lower() == ".pdf":
            types[_norm(pdf.stem)] = pdf.parent.name.replace("_", " ").strip()
    return types


def category_of(question: str) -> str:
    m = re.search(r'related to "([^"]+)"', question)
    return m.group(1) if m else question[:40]


def load() -> list[Contract]:
    data = json.loads((CUAD_DIR / "CUAD_v1.json").read_text())["data"]
    types = contract_types()
    out = []
    for doc in data:
        para = doc["paragraphs"][0]
        labels = []
        for qa in para["qas"]:
            spans = [Span(a["answer_start"], a["answer_start"] + len(a["text"]), a["text"]) for a in qa["answers"]]
            labels.append(Label(category_of(qa["question"]), qa["question"], spans))
        out.append(Contract(doc["title"], para["context"], types.get(_norm(doc["title"]), "Unknown"), labels))
    return out
