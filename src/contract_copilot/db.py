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
    sha           text NOT NULL,
    pii_version   integer NOT NULL DEFAULT 0,   -- which detector masked this contract (pii.PII_VERSION)
    pii_counts    jsonb NOT NULL DEFAULT '{{}}'  -- distinct masked values per type, e.g. {{"PERSON": 4}}
);

CREATE TABLE IF NOT EXISTS chunks (
    id          serial PRIMARY KEY,
    contract_id integer NOT NULL REFERENCES contracts ON DELETE CASCADE,
    idx         integer NOT NULL,
    start_char  integer NOT NULL,            -- offsets into the contract text (CUAD answer offsets use the same text)
    end_char    integer NOT NULL,
    heading     text NOT NULL DEFAULT '',
    text        text NOT NULL,               -- original; the app role has no privilege on this column (D9)
    text_masked text NOT NULL,               -- personal data replaced by [PERSON_1]-style placeholders
    mask_map    jsonb NOT NULL DEFAULT '[]', -- [[masked_start, masked_end, orig_start, orig_end], ...]
    embedding   vector({EMBED_DIM}),         -- computed from the masked text, like the keyword index
    tsv         tsvector GENERATED ALWAYS AS (to_tsvector('english', heading || ' ' || text_masked)) STORED,
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


MIGRATE_PII = """
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS pii_version integer NOT NULL DEFAULT 0;
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS pii_counts jsonb NOT NULL DEFAULT '{}';
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS text_masked text;
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS mask_map jsonb NOT NULL DEFAULT '[]';
"""


def ensure_schema(conn: psycopg.Connection) -> None:
    exists = conn.execute("SELECT to_regclass('chunks') IS NOT NULL").fetchone()[0]
    if exists:
        conn.execute(MIGRATE_PII)  # databases created before masking (D9): columns first, index swap after fill
    conn.execute(SCHEMA)


def finish_pii_migration(conn: psycopg.Connection) -> None:
    """Once every chunk has masked text: make it required and rebuild the keyword index on it."""
    if conn.execute("SELECT count(*) FROM chunks WHERE text_masked IS NULL").fetchone()[0]:
        raise RuntimeError("some chunks have no masked text yet")
    expr = conn.execute("SELECT pg_get_expr(d.adbin, d.adrelid) FROM pg_attrdef d JOIN pg_attribute a "
                        "ON a.attrelid = d.adrelid AND a.attnum = d.adnum "
                        "WHERE d.adrelid = 'chunks'::regclass AND a.attname = 'tsv'").fetchone()[0]
    with conn.transaction():
        conn.execute("ALTER TABLE chunks ALTER COLUMN text_masked SET NOT NULL")
        if "text_masked" not in expr:
            conn.execute("ALTER TABLE chunks DROP COLUMN tsv")
            conn.execute("ALTER TABLE chunks ADD COLUMN tsv tsvector GENERATED ALWAYS AS "
                         "(to_tsvector('english', heading || ' ' || text_masked)) STORED")
            conn.execute("CREATE INDEX chunks_tsv ON chunks USING gin (tsv)")
