"""Retrieval: keyword (Postgres full-text), vector (pgvector), hybrid (reciprocal rank fusion) and an optional
cross-encoder rerank. Every method can be scoped to one contract (single-contract questions and field extraction)
or run across the whole portfolio.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import psycopg

from contract_copilot import planner
from contract_copilot.embed import embed_query, rerank_scores
from contract_copilot.tracing import span

RRF_K = 60
STOP = {"a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have", "if", "in", "into", "is", "it", "its", "of", "on", "or", "that", "the", "their", "this", "to", "was", "were", "will", "with", "any", "all", "such", "shall", "may", "must", "other", "should", "which", "what", "who", "whom", "whose", "there", "these", "those", "been", "being", "do", "does", "part", "parts", "contract", "agreement", "highlight", "reviewed", "lawyer", "details", "related"}


@dataclass(frozen=True)
class Hit:
    chunk_id: int
    contract_id: int
    start: int
    end: int
    text: str            # masked text; serve.serve() swaps in the original for users allowed to see it
    score: float
    masks: tuple = ()    # [[masked_start, masked_end, orig_start, orig_end], ...]; empty once revealed


def _hit(row) -> Hit:
    return Hit(*row[:6], masks=tuple(tuple(m) for m in row[6]))


def _or_query(text: str) -> str:
    words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in STOP]
    return " | ".join(dict.fromkeys(words)) or "contract"


def _scope(contract_id: int | None) -> tuple[str, tuple]:
    return ("AND contract_id = %s", (contract_id,)) if contract_id is not None else ("", ())


def keyword(conn: psycopg.Connection, query: str, k: int = 50, contract_id: int | None = None) -> list[Hit]:
    where, args = _scope(contract_id)
    rows = conn.execute(
        f"""SELECT id, contract_id, start_char, end_char, text_masked, ts_rank_cd(tsv, q) AS s, mask_map
            FROM chunks, to_tsquery('english', %s) q
            WHERE tsv @@ q {where} ORDER BY s DESC LIMIT %s""", (_or_query(query), *args, k)).fetchall()
    return [_hit(r) for r in rows]


def vector(conn: psycopg.Connection, query: str, k: int = 50, contract_id: int | None = None) -> list[Hit]:
    where, args = _scope(contract_id)
    q = embed_query(query)
    rows = conn.execute(
        f"""SELECT id, contract_id, start_char, end_char, text_masked, 1 - (embedding <=> %s) AS s, mask_map
            FROM chunks WHERE true {where} ORDER BY embedding <=> %s LIMIT %s""", (q, *args, q, k)).fetchall()
    return [_hit(r) for r in rows]


def hybrid(conn: psycopg.Connection, query: str, k: int = 50, contract_id: int | None = None) -> list[Hit]:
    """Reciprocal rank fusion: robust to the two methods' scores living on different scales."""
    fused: dict[int, float] = {}
    by_id: dict[int, Hit] = {}
    for hits in (keyword(conn, query, k, contract_id), vector(conn, query, k, contract_id)):
        for rank, h in enumerate(hits):
            fused[h.chunk_id] = fused.get(h.chunk_id, 0.0) + 1.0 / (RRF_K + rank + 1)
            by_id[h.chunk_id] = h
    order = sorted(fused, key=fused.get, reverse=True)[:k]
    return [Hit(**{**by_id[i].__dict__, "score": fused[i]}) for i in order]


def rerank(query: str, hits: list[Hit], k: int) -> list[Hit]:
    with span("rerank", "RERANKER", **{"reranker.model_name": "cross-encoder/ms-marco-MiniLM-L-6-v2",
                                       "cc.candidates": len(hits), "reranker.top_k": k}):
        scores = rerank_scores(query, [h.text for h in hits])
    ranked = sorted(zip(hits, scores), key=lambda x: x[1], reverse=True)[:k]
    return [Hit(**{**h.__dict__, "score": s}) for h, s in ranked]


def first_chunks(conn: psycopg.Connection, contract_id: int, n: int) -> list[Hit]:
    rows = conn.execute("SELECT id, contract_id, start_char, end_char, text_masked, 0.0, mask_map FROM chunks "
                        "WHERE contract_id = %s ORDER BY idx LIMIT %s", (contract_id, n)).fetchall()
    return [_hit(r) for r in rows]


def last_chunks(conn: psycopg.Connection, contract_id: int, n: int) -> list[Hit]:
    rows = conn.execute("SELECT id, contract_id, start_char, end_char, text_masked, 0.0, mask_map FROM chunks "
                        "WHERE contract_id = %s ORDER BY idx DESC LIMIT %s", (contract_id, n)).fetchall()
    return [_hit(r) for r in reversed(rows)]


def planned(conn: psycopg.Connection, query: str, category: str | None, k: int, contract_id: int | None,
            pool: int) -> list[Hit]:
    """Expanded wording for recall, the original question for the reranker, and front matter routed to the
    contract's opening chunks (D6)."""
    hits = rerank(query, hybrid(conn, planner.expand(category, query), pool, contract_id), k)
    if contract_id is not None and planner.is_front_matter(category):
        front = first_chunks(conn, contract_id, planner.FRONT_CHUNKS)
        seen = {h.chunk_id for h in front}
        hits = (front + [h for h in hits if h.chunk_id not in seen])[:k]
    elif contract_id is not None and category is None and planner.is_back_matter(query):
        back = last_chunks(conn, contract_id, planner.BACK_CHUNKS)
        seen = {h.chunk_id for h in back}
        hits = (back + [h for h in hits if h.chunk_id not in seen])[:k]
    return hits


def search(conn: psycopg.Connection, query: str, method: str = "hybrid+rerank", k: int = 8,
           contract_id: int | None = None, pool: int = 50, category: str | None = None) -> list[Hit]:
    with span("retrieve", "RETRIEVER", **{"cc.method": method, "cc.k": k, "cc.contract_id": contract_id,
                                          "cc.category": category}) as s:
        hits = _search(conn, query, method, k, contract_id, pool, category)
        s.set_attribute("cc.hits", len(hits))
        s.set_attribute("cc.chunk_ids", [h.chunk_id for h in hits])
        return hits


def _search(conn: psycopg.Connection, query: str, method: str, k: int, contract_id: int | None, pool: int,
            category: str | None) -> list[Hit]:
    if method == "planned":
        return planned(conn, query, category, k, contract_id, pool)
    if method == "keyword":
        return keyword(conn, query, k, contract_id)
    if method == "vector":
        return vector(conn, query, k, contract_id)
    if method == "hybrid":
        return hybrid(conn, query, k, contract_id)
    if method == "hybrid+rerank":
        return rerank(query, hybrid(conn, query, pool, contract_id), k)
    raise ValueError(f"unknown method {method!r}")
