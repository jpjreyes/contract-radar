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
- **Held-out test split (run once, 5,506 labeled clauses, 415 contracts never used for tuning):**

| Method | hit@1 | hit@5 | hit@8 | span recall@8 | MRR |
|---|---|---|---|---|---|
| Keyword | 0.273 | 0.586 | 0.686 | 0.547 | 0.402 |
| Vector | 0.325 | 0.618 | 0.717 | 0.573 | 0.446 |
| Hybrid | 0.332 | 0.655 | 0.746 | 0.597 | 0.466 |
| Hybrid + reranker | 0.465 | 0.769 | 0.839 | 0.698 | 0.592 |
| **Planned** | **0.585** | **0.843** | **0.891** | **0.787** | **0.694** |

  Test matches dev within 1–2 points at every k: the planning generalizes rather than overfitting the dev contracts.
  Reranking costs ~0.4 s per question on the Mac's GPU.
- **Honesty:** expansions are written from contract language, not fitted to examples; categories with 1–3 dev
  examples (Most Favored Nation, Price Restrictions) are reported, not tuned. The held-out test split is run once.

## D7. Answers: verified citations, an explicit "is it there?" field, and adjudicated disagreements
- **Constraint:** C1, C5.
- **Design:** the model must quote the words behind every claim; code locates each quote in the cited passage (exact,
  whitespace-insensitive, or fuzzy ≥ 90 aligned on the raw text) and drops claims it can't verify. A separate
  `clause_present` field separates "No, there isn't one" from a real answer: the first eval counted any reply as a
  failed refusal, which mixed up a correct "no" with an over-call.
- **Evidence (dev split, Haiku via the Claude CLI):**

| Measure | Result |
|---|---|
| Answered when the lawyers labeled the clause | 27/30 |
| A citation lands on the lawyers' span | 25/27 |
| Says the clause is absent when the label says absent | 16/20 raw · **18/20 after adjudication** |
| Claims dropped for unverifiable quotes | 3/148 (first run), 0/35 (second) |
| Median latency · cost | 10–13 s · $0.0012–0.0013 per question |

- **Adjudication** (`evals/adjudication.json`): of the 4 disagreements on "absent" clauses, 2 were label misses in
  CUAD (Pareteum renewal notice; Middlebrook termination for convenience), 1 was run-to-run inconsistency, 1 a real
  over-call (non-exclusive licenses read as exclusivity). Both numbers are reported; labels are never edited.
- **Open:** latency is above C4 (8 s) on the CLI backend (process start-up per call + long answers): re-measure on the
  API and cap answer length; run pass^k on the absent set for the inconsistency.

## D8. Row-level security in practice: a non-superuser app role, deny by default
- **Constraint:** C2.
- **Finding while building:** the local Postgres user is a superuser, and superusers bypass row-level security even with
  `FORCE ROW LEVEL SECURITY`. So the app connects as `cc_app` (login, not superuser, no BYPASSRLS); ingestion and
  setup use the admin role. Tests assert the app role can't bypass RLS.
- **Design:** `app.user` is set per transaction (`set_config(..., true)`), so it can't leak into the next request on a
  pooled connection. Policies call `can_read(region, business_unit)` (SECURITY DEFINER over `app_users`, which the
  app role can't read or change). No user, unknown user or no grant → no rows.
- **Evidence (`eval_permissions`):** for 3 restricted users, 25 forbidden contracts each, a 300-character passage copied
  verbatim from the forbidden contract was searched with keyword, vector, hybrid and planned retrieval, globally and
  scoped to the forbidden id: **0 leaks in 2,122 returned chunks.** Control: the same attack on the user's own
  contracts still finds them (10/10, 10/10, 7/10; the misses are boilerplate passages shared by many contracts).
- **Visibility per demo user:** legal_admin 510 contracts · maria (Procurement, North America) 50 · kenji (Marketing,
  EMEA + LATAM) 46 · analyst (Legal, all regions) 311 · no user / unknown user 0.

## D9. Personal data: mask at ingest, lock the original column, reveal only through the database
- **Constraint:** C3 (personal data masked for roles without access to it).
- **Policy (what counts):** names of individual people (signatories, notice recipients, employees, attorneys, defined
  short names like "Maimon"), emails, phone/fax numbers, street-level addresses. Company and law-firm names, job
  titles, defined terms ("Licensee") and city/state/country stay visible: they decide jurisdiction and party
  questions and identify no one.
- **Detection (`pii.py`):** Microsoft Presidio + spaCy `en_core_web_lg` for names, emails and phones, plus rules for
  what contracts do: signature and notice fields ("Name:", "/s/", "By:", "Attention:"), street addresses, and
  filters for what a general NER model gets wrong on contracts (company names, departments, defined terms, words the
  contract also uses in lowercase). Every other occurrence of a found name in the contract is masked too, including
  the bare surname when it's the person's defined alias ("Nantz") but not when it's a company's ("Rogers" for Rogers
  Cable Inc.).
- **Masking:** typed placeholders, stable per contract (`[PERSON_1]` is the same person throughout), so answers stay
  readable ("[PERSON_1] signed for the Licensee"). Each chunk stores a masked→original offset map, so citations on
  masked text still point at the exact characters of the original contract (eval offsets unchanged).
- **Mask at ingest, not at serve time:** the keyword index and the embeddings are built from masked text, so a user
  without PII access can't find a chunk by searching for a person's name (a serve-time mask would still rank it).
  Trade-off: a user WITH access can't search by name either; acceptable for clause questions, revisit if the client
  needs "every contract John Smith signed".
- **Enforcement in Postgres, not Python:** the app role has column-level SELECT on every chunk column except `text`
  (a query naming it fails with a privilege error). The only way back is `reveal(ids)`, SECURITY DEFINER, which
  returns originals only when the current user has `pii_access` AND may read that contract. `serve()` swaps in what
  reveal returns; a connection with no user keeps the masked text. Masking is versioned (`pii_version`): a better
  detector re-masks in place and re-embeds only chunks whose masked text changed.
- **Detector quality (`eval_pii detector`, labels by Sonnet on a stratified sample, half likely-PII chunks, half
  random, adjudicated in `evals/pii_adjudication.json`; a label item counts as protected only if ≥ 90% of its
  letters/digits are masked):**

| Labeled set | Chunks | Items | Recall | Precision |
|---|---|---|---|---|
| dev (tuned on) | 121 | 180 | 0.956 | 0.994 |
| test, first run (v1) | 119 | 272 | 0.871 | 0.895 |
| test, after fixing errors seen on it | 119 | 272 | 0.926 | 0.965 |
| **test2, fresh, never inspected before scoring (v2)** | **120** | **254** | **0.831** | **0.936** |

  Test2 is the honest number (test stopped being held out once its errors drove fixes). Emails and phones: 0.95–1.0
  everywhere. The misses are mostly name lists in "Surname, Given" order (one patent table = 25 of test2's 44 misses),
  non-US addresses without a street suffix, and bare celebrity surnames. Reported, not hidden: residual exposure
  risk ≈ 1 − recall on items the detector doesn't know about.
- **Enforcement (`eval_pii exposure`, the number that must be 0):** for 3 users without PII access, personal data in
  contracts they CAN read was searched with keyword, vector, hybrid and planned retrieval, globally and scoped, and
  the chunk was fetched by id: **0 enforcement leaks in 3,912 served texts** (every served text equals the stored
  masked text); the app role cannot select the original column; control: legal_admin gets originals 23/23. Reported
  apart: the probed string sometimes also appears unmasked elsewhere (mostly detector false positives like
  "Miltenyi", masked once as a surname but visible in "Miltenyi Product"; two real names found unmasked in other
  contracts where the detector missed them → v3 idea: a corpus-wide registry of found names).
- **Answers (`eval_pii answers`):** asking for exactly the personal data ("who signed, where do notices go, emails,
  phones") on 10 Legal contracts: the analyst got placeholders in every answer (2–15 per contract) and 0 real values;
  legal_admin got the real values (2–16 per contract). The one hit in v1 was the word "Email" masked by a detector
  bug ("Mario Cibelli Email" read as a name) → v2 cuts field labels from model-found names.
- **Corpus after masking (v2):** 8,022 values masked in 2,293 of 22,706 chunks (1,959 people, 1,312 addresses,
  717 phone numbers, 294 emails); detection over 510 contracts takes ~6 min on 4 processes.
- **Regression:** row-level security leak test after masking: still 0 leaks in 2,090 returned chunks. Retrieval:
  22.6% of test clauses sit in a chunk that masking changed (front matter and signature blocks); planned hit@8 on
  the first 800 test clauses after masking is 0.911 (full test set before masking: 0.891). Not a same-set A/B (the
  earlier run kept only totals), but no sign of damage; per-query results are worth saving next time.
