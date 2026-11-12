"""Gold labels for the PII detector: a stratified sample of chunks, labeled by a stronger model, then adjudicated.

Sample: half "likely PII" chunks (signature blocks, notice clauses, anything with @ or a phone pattern), half random,
so the recall number covers where PII actually lives and the precision number covers ordinary clause text too.
The split into dev (used to tune the detector) and test (reported) is by a hash of the chunk id, fixed forever.

Labeling policy (the same text the detector is held to; see DECISIONS D9):
  PERSON   names of individual people: signatories, notice recipients, employees, attorneys, initials-only names.
           NOT company or law-firm names, job titles, defined terms ("Licensee"), products, places.
  EMAIL    any email address.
  PHONE    phone and fax numbers.
  ADDRESS  street-level address lines: number + street (+ suite/floor), or a P.O. box. City, state, zip and country
           stay visible (they decide jurisdiction questions and don't identify anyone alone).
  Redaction markers like [***] are not PII.

Run:  uv run python -m contract_radar.pii_gold --likely 120 --random 120 --workers 4
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from contract_radar.db import connect
from contract_radar.llm import get_backend

GOLD = Path(__file__).resolve().parents[2] / "evals" / "pii_gold.json"
TYPES = ("PERSON", "EMAIL", "PHONE", "ADDRESS")
LIKELY = re.compile(r"Name:|/s/|Att(?:ention|n)\.?:|By:|@|\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}|Telephone|Fax", re.IGNORECASE)

SYSTEM = """You label personal data in contract text for a privacy evaluation. Return every occurrence of:
PERSON: names of individual people (signatories, notice recipients, employees, attorneys, names in initials form
like "J. Smith"). Do NOT label company, bank or law-firm names (even if they contain a surname), job titles, defined
terms such as "Licensee" or "Executive", product names, or places.
EMAIL: email addresses.
PHONE: telephone and fax numbers.
ADDRESS: street-level address lines only: building number + street name (+ suite, floor, unit), or a P.O. Box.
Do NOT include the city, state, zip code or country.
Redaction markers like [***] are not personal data. Copy each item's text exactly as it appears, character for
character, without surrounding punctuation. List repeated items once per occurrence. If there is none, return an
empty list."""

SCHEMA = {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
    "type": {"type": "string", "enum": list(TYPES)}, "text": {"type": "string"}}, "required": ["type", "text"]}}},
    "required": ["items"]}


def split(chunk_id: int) -> str:
    return "test" if int(hashlib.sha1(f"pii-{chunk_id}".encode()).hexdigest(), 16) % 2 else "dev"


def sample(n_likely: int, n_random: int, seed: int = 7, exclude: set[int] = frozenset(),
           force_split: str | None = None) -> list[dict]:
    rows = [r for r in connect().execute("SELECT id, contract_id, text FROM chunks ORDER BY id").fetchall()
            if r[0] not in exclude]
    rng = random.Random(seed)
    likely = [r for r in rows if LIKELY.search(r[2])]
    picked = {r[0]: ("likely", r) for r in rng.sample(likely, n_likely)}
    rest = [r for r in rows if r[0] not in picked]
    picked.update({r[0]: ("random", r) for r in rng.sample(rest, n_random)})
    return [{"chunk_id": r[0], "contract_id": r[1], "stratum": s, "split": force_split or split(r[0]), "text": r[2]}
            for s, r in picked.values()]


def spans(text: str, items: list[dict]) -> tuple[list[dict], list[dict]]:
    """Exact character spans for the labeled strings (every occurrence); strings not found are returned separately."""
    found, missing, seen = [], [], set()
    for it in items:
        t = it["text"].strip()
        if not t or (it["type"], t) in seen:
            continue
        seen.add((it["type"], t))
        pattern = r"\s+".join(re.escape(w) for w in t.split())
        hits = list(re.finditer(pattern, text))
        if not hits:
            missing.append(it)
        for m in hits:
            found.append({"type": it["type"], "start": m.start(), "end": m.end(), "text": m.group()})
    return sorted(found, key=lambda s: s["start"]), missing


def label(rec: dict, model: str) -> dict:
    try:
        res = get_backend().complete(SYSTEM, rec["text"], SCHEMA, model=model)
    except Exception as e:  # noqa: BLE001 - one failed call shouldn't sink the batch; rerun picks it up
        print(f"  chunk {rec['chunk_id']}: {e}", flush=True)
        return {}
    found, missing = spans(rec["text"], res.data.get("items", []))
    return {**rec, "gold": found, "unlocated": missing, "labeler": model}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--likely", type=int, default=120)
    ap.add_argument("--random", type=int, default=120)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--fresh-split", help="label NEW chunks into this split (e.g. test2, after test was inspected)")
    a = ap.parse_args()
    done = {r["chunk_id"]: r for r in json.loads(GOLD.read_text())} if GOLD.exists() else {}
    if a.fresh_split:
        earlier = {i for i, r in done.items() if r["split"] != a.fresh_split}
        seed = int(hashlib.sha1(a.fresh_split.encode()).hexdigest(), 16) % 10_000
        todo = [r for r in sample(a.likely, a.random, seed=seed, exclude=earlier, force_split=a.fresh_split)
                if r["chunk_id"] not in done]
    else:
        todo = [r for r in sample(a.likely, a.random) if r["chunk_id"] not in done]
    print(f"{len(done)} already labeled, {len(todo)} to label", flush=True)
    with ThreadPoolExecutor(a.workers) as ex:
        for i, r in enumerate(ex.map(lambda r: label(r, a.model), todo), 1):
            if r:
                done[r["chunk_id"]] = r
            if i % 20 == 0 or i == len(todo):
                GOLD.write_text(json.dumps(sorted(done.values(), key=lambda x: x["chunk_id"]), indent=1))
                print(f"  {i}/{len(todo)}", flush=True)


if __name__ == "__main__":
    main()
