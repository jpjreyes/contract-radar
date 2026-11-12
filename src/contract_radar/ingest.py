"""Load CUAD into Postgres: contracts, clause-aware chunks, personal-data masking, embeddings.

Idempotent: a contract whose text hash and masking version are unchanged is skipped. If only the masking version
changed (a better detector), its chunks are re-masked in place and only chunks whose masked text changed are
re-embedded. Embeddings and the keyword index are built from the masked text, so a user without PII access can't
find a chunk by searching for a person's name (D9).

Run:  uv run python -m contract_radar.ingest [--limit N] [--workers 4]
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ProcessPoolExecutor

from psycopg.types.json import Jsonb

from contract_radar import cuad, pii
from contract_radar.chunking import Chunk, chunk_text
from contract_radar.db import connect, ensure_schema, finish_pii_migration
from contract_radar.embed import embed_passages


def masked_chunks(text: str) -> tuple[list[Chunk], list[tuple[str, str, list]], dict[str, int]]:
    """Chunks of a contract with (masked heading, masked text, offset map) each, and distinct values per type."""
    spans = pii.detect(text)
    toks = pii.tokens(text, spans)
    chunks = chunk_text(text)
    out = []
    for ch in chunks:
        body, mapping = pii.mask_range(text, ch.start, ch.end, spans, toks)
        out.append((pii.mask_value_strings(ch.heading, spans, toks), body, mapping))
    counts: dict[str, int] = {}
    for t in set(toks):
        kind = t[1:].rsplit("_", 1)[0]
        counts[kind] = counts.get(kind, 0) + 1
    return chunks, out, counts


def _embed_input(heading: str, body: str) -> str:
    return f"{heading}\n{body}" if heading else body


def _work(c: cuad.Contract) -> tuple:
    return (c, *masked_chunks(c.text))


def ingest(limit: int | None = None, workers: int = 4) -> dict[str, int]:
    conn = connect()
    ensure_schema(conn)
    contracts = cuad.load()[:limit]
    known = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT name, sha, pii_version FROM contracts").fetchall()}
    stats = {"contracts": 0, "remasked": 0, "skipped": 0, "chunks": 0, "reembedded": 0}
    todo = []
    for c in contracts:
        sha, version = known.get(c.name, (None, None))
        conn.execute("UPDATE contracts SET contract_type = %s, region = %s, business_unit = %s WHERE name = %s",
                     (c.contract_type, c.region, c.business_unit, c.name))  # ownership metadata is cheap: refresh
        if sha == c.sha and version == pii.PII_VERSION:
            stats["skipped"] += 1
        else:
            todo.append(c)
    t0 = time.monotonic()
    with ProcessPoolExecutor(workers) as ex:  # detection is the slow part (spaCy), so it runs in parallel
        for c, chunks, masked, counts in ex.map(_work, todo, chunksize=4):
            if known.get(c.name, (None,))[0] == c.sha:
                stats["reembedded"] += _remask(conn, c, chunks, masked, counts)
                stats["remasked"] += 1
            else:
                _insert(conn, c, chunks, masked, counts)
                stats["contracts"] += 1
                stats["chunks"] += len(chunks)
            done = stats["contracts"] + stats["remasked"]
            if done % 50 == 0:
                print(f"  {done}/{len(todo)} contracts, {time.monotonic() - t0:.0f}s", flush=True)
    finish_pii_migration(conn)
    stats["seconds"] = round(time.monotonic() - t0)
    return stats


def _insert(conn, c: cuad.Contract, chunks: list[Chunk], masked: list, counts: dict) -> None:
    vectors = embed_passages([_embed_input(h, b) for h, b, _ in masked])
    with conn.transaction():
        conn.execute("DELETE FROM contracts WHERE name = %s", (c.name,))
        cid = conn.execute(
            "INSERT INTO contracts (name, contract_type, region, business_unit, chars, sha, pii_version, pii_counts) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (c.name, c.contract_type, c.region, c.business_unit, len(c.text), c.sha, pii.PII_VERSION,
             Jsonb(counts))).fetchone()[0]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO chunks (contract_id, idx, start_char, end_char, heading, text, text_masked, mask_map, "
                "embedding) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [(cid, ch.idx, ch.start, ch.end, h, ch.text, b, Jsonb(m), v)
                 for ch, (h, b, m), v in zip(chunks, masked, vectors)])


def _remask(conn, c: cuad.Contract, chunks: list[Chunk], masked: list, counts: dict) -> int:
    cid = conn.execute("SELECT id FROM contracts WHERE name = %s", (c.name,)).fetchone()[0]
    stored = {r[0]: r[1:] for r in conn.execute(
        "SELECT idx, start_char, end_char, heading, text_masked FROM chunks WHERE contract_id = %s", (cid,))}
    if [(ch.idx, ch.start, ch.end) for ch in chunks] != [(i, *stored[i][:2]) for i in sorted(stored)]:
        raise RuntimeError(f"{c.name}: chunk boundaries changed; re-ingest it from scratch")
    # re-embed only chunks whose embedding input changes (never masked before: the input was the original text)
    changed = []
    for i, (ch, (h, b, _)) in enumerate(zip(chunks, masked)):
        _, _, old_heading, old_masked = stored[ch.idx]
        if (old_heading, old_masked if old_masked is not None else ch.text) != (h, b):
            changed.append(i)
    vectors = dict(zip(changed, embed_passages([_embed_input(*masked[i][:2]) for i in changed]))) if changed else {}
    with conn.transaction():
        with conn.cursor() as cur:
            cur.executemany(
                "UPDATE chunks SET heading = %s, text_masked = %s, mask_map = %s, "
                "embedding = COALESCE(%s, embedding) WHERE contract_id = %s AND idx = %s",
                [(h, b, Jsonb(m), vectors.get(i), cid, ch.idx)
                 for i, (ch, (h, b, m)) in enumerate(zip(chunks, masked))])
        conn.execute("UPDATE contracts SET pii_version = %s, pii_counts = %s WHERE id = %s",
                     (pii.PII_VERSION, Jsonb(counts), cid))
    return len(changed)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    print(json.dumps(ingest(a.limit, a.workers)))


if __name__ == "__main__":
    main()
