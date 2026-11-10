"""Postgres schema: contracts and their chunks, with keyword (full-text) and vector indexes in one database.

Permissions live on the rows themselves (region, business_unit) so row-level security can filter retrieval inside
the search query, before top-k (decision D4).
"""

from __future__ import annotations

import os

import psycopg
from dotenv import load_dotenv
from pgvector.psycopg import register_vector

EMBED_DIM = 384

SCHEMA = f"""
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS contracts (
    id            serial PRIMARY KEY,
    name          text UNIQUE NOT NULL,      -- CUAD title (file stem)
    contract_type text NOT NULL,
    region        text NOT NULL,
    business_unit text NOT NULL,
    chars         integer NOT NULL,
    sha           text NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id          serial PRIMARY KEY,
    contract_id integer NOT NULL REFERENCES contracts ON DELETE CASCADE,
    idx         integer NOT NULL,
    start_char  integer NOT NULL,            -- offsets into the contract text (CUAD answer offsets use the same text)
    end_char    integer NOT NULL,
    heading     text NOT NULL DEFAULT '',
    text        text NOT NULL,
    embedding   vector({EMBED_DIM}),
    tsv         tsvector GENERATED ALWAYS AS (to_tsvector('english', heading || ' ' || text)) STORED,
    UNIQUE (contract_id, idx)
);

CREATE INDEX IF NOT EXISTS chunks_tsv ON chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS chunks_contract ON chunks (contract_id);
"""


def connect(url: str | None = None) -> psycopg.Connection:
    load_dotenv()
    url = url or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("Set DATABASE_URL in .env (see .env.example).")
    conn = psycopg.connect(url, autocommit=True)
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    register_vector(conn)
    return conn


def ensure_schema(conn: psycopg.Connection) -> None:
    conn.execute(SCHEMA)
