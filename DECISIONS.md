# Decisions

Each entry: the constraint it serves, the options, the choice and why, and what would change it. Results that confirm
or overturn a decision are added to the entry.

## D1. Vector store: Postgres + pgvector (2026-11-10)
- **Constraint:** C2 (permissions inside retrieval), C4.
- **Options:** A. pgvector in Postgres · B. a dedicated vector database (e.g. Qdrant).
- **Choice:** A. Text, vectors, keyword search and permissions live in one database, so row-level security can filter
  forbidden contracts inside the search query itself. A separate vector store would need the permissions mirrored
  and kept in sync: a second system and a leak risk.
- **Would change if:** the corpus grows past what one Postgres instance serves within C4.

## D2. Embeddings: a local open model (2026-11-10)
- **Constraint:** C3 (contract text stays on our machines), C4.
- **Options:** A. local (`BAAI/bge-small-en-v1.5`, 384 dims) · B. API embeddings (Voyage, OpenAI).
- **Choice:** A. Free, private, runs on the Mac's GPU; the full corpus embeds in minutes. API embeddings can be a bit
  better on legal text but send contract text to a vendor.
- **Would change if:** the retrieval eval shows the local model is the bottleneck.

## D3. Reranker: a local cross-encoder, kept only if it wins (2026-11-10)
- **Constraint:** C1 (the right clause at the top), C4.
- **Options:** A. local cross-encoder · B. none · C. Claude as the reranker (one extra model call per question).
- **Choice:** A, measured against B in the retrieval eval; it stays only if it improves hit@k enough for its latency.

## D4. Permissions: Postgres row-level security + an application test suite (2026-11-10)
- **Constraint:** C2.
- **Options:** A. row-level security, applied before top-k · B. application-level filters only.
- **Choice:** A. The database refuses to return forbidden rows to the user's role even if the app has a bug, and the
  filter runs inside retrieval (not "top-k, then drop forbidden"), so a user with few contracts still gets good recall.
  An adversarial test suite proves 0 leaks.

## D5. Source text: CUAD's JSON contexts, not the TXT files
- **Constraint:** C5 (evals must be exact).
- **Choice:** the lawyers' labels carry character offsets into the JSON text, so ingesting that exact text lets the
  retrieval eval check overlap with labeled spans directly, with no fuzzy matching. PDF page numbers are mapped onto
  citations later, for display.

## D6. Query planning: expansions in contract language + front-matter routing
- **Constraint:** C1 (the right clause must be among what the model sees).
- **Evidence (dev split, 1,196 labeled clauses, 95 contracts):**

| Method | hit@1 | hit@5 | hit@8 | span recall@8 | MRR |
|---|---|---|---|---|---|
| Keyword (Postgres full-text) | 0.287 | 0.576 | 0.661 | 0.533 | 0.404 |
| Vector (bge-small) | 0.364 | 0.625 | 0.706 | 0.587 | 0.471 |
| Hybrid (RRF) | 0.357 | 0.648 | 0.746 | 0.621 | 0.478 |
| Hybrid + reranker | 0.477 | 0.756 | 0.826 | 0.698 | 0.594 |
| **Planned** (expansions + front matter + reranker) | **0.604** | **0.850** | **0.895** | **0.809** | **0.709** |

- **Reading:** hybrid beats either method alone; the reranker adds +12 points at hit@1 for ~0.4 s per question (D3
  confirmed: it stays). The weakest types were front-matter fields (Parties 0.34 hit@5: they live in the opening
  chunks) and clauses whose lawyer definitions don't use contract wording (Minimum Commitment 0.48). Routing the
  opening chunks for front-matter fields and adding contract phrasing per clause type closed most of the gap.
- **Honesty:** expansions are written from contract language, not fitted to examples; categories with 1–3 dev
  examples (Most Favored Nation, Price Restrictions) are reported, not tuned. The held-out test split is run once.
