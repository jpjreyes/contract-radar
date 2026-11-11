"""What a user is shown: masked text by default, the original only through the database's reveal() check (D9).

Retrieval always returns masked chunks. For each hit, `serve` asks Postgres for the original; reveal() returns it only
if the current user (app.user) has pii_access and may read that contract. Anyone else, including a connection with
no user set, keeps the masked text. The decision is made in the database, not here, so a bug in this file can't
show personal data to the wrong person.
"""

from __future__ import annotations

from dataclasses import replace

import psycopg

from contract_copilot.pii import to_original
from contract_copilot.retrieve import Hit


def serve(conn: psycopg.Connection, hits: list[Hit]) -> list[Hit]:
    if not hits:
        return hits
    originals = dict(conn.execute("SELECT id, text FROM reveal(%s)", ([h.chunk_id for h in hits],)).fetchall())
    return [replace(h, text=originals[h.chunk_id], masks=()) if h.chunk_id in originals else h for h in hits]


def contract_offsets(hit: Hit, start: int, end: int) -> tuple[int, int]:
    """Offsets of hit.text[start:end] in the original contract, whether the hit is masked or not."""
    return (hit.start + to_original(start, hit.masks, "start"), hit.start + to_original(end, hit.masks, "end"))
