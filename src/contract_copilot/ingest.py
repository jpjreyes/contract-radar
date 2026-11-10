"""Load CUAD into Postgres: contracts, clause-aware chunks, embeddings. Idempotent: a contract whose text hash is
unchanged is skipped, so re-running only processes what changed.

Run:  uv run python -m contract_copilot.ingest [--limit N]
"""

from __future__ import annotations

import argparse
import time

from contract_copilot import cuad
from contract_copilot.chunking import chunk_text
from contract_copilot.db import connect, ensure_schema
from contract_copilot.embed import embed_passages


def ingest(limit: int | None = None) -> dict[str, int]:
    conn = connect()
    ensure_schema(conn)
    contracts = cuad.load()[:limit]
    known = dict(conn.execute("SELECT name, sha FROM contracts").fetchall())
    stats = {"contracts": 0, "skipped": 0, "chunks": 0}
    t0 = time.monotonic()
    for c in contracts:
        if known.get(c.name) == c.sha:  # text unchanged: keep chunks and embeddings, refresh ownership metadata only
            conn.execute("UPDATE contracts SET contract_type = %s, region = %s, business_unit = %s WHERE name = %s",
                         (c.contract_type, c.region, c.business_unit, c.name))
            stats["skipped"] += 1
            continue
        chunks = chunk_text(c.text)
        vectors = embed_passages([f"{ch.heading}\n{ch.text}" if ch.heading else ch.text for ch in chunks])
        with conn.transaction():
            conn.execute("DELETE FROM contracts WHERE name = %s", (c.name,))
            cid = conn.execute(
                "INSERT INTO contracts (name, contract_type, region, business_unit, chars, sha) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (c.name, c.contract_type, c.region, c.business_unit, len(c.text), c.sha)).fetchone()[0]
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO chunks (contract_id, idx, start_char, end_char, heading, text, embedding) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    [(cid, ch.idx, ch.start, ch.end, ch.heading, ch.text, v) for ch, v in zip(chunks, vectors)])
        stats["contracts"] += 1
        stats["chunks"] += len(chunks)
        if stats["contracts"] % 50 == 0:
            print(f"  {stats['contracts']} contracts, {stats['chunks']} chunks, {time.monotonic() - t0:.0f}s", flush=True)
    stats["seconds"] = round(time.monotonic() - t0)
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    print(ingest(ap.parse_args().limit))


if __name__ == "__main__":
    main()
