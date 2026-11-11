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
  earlier run kept only totals), but no sign of damage; the retrieval eval now saves per-query ranks. Answers
  (same 50 dev questions, model sees masked text): answered when present 27/30 (was 27/30), citation on the
  lawyers' span 25/27 (was 25/27), says absent 17/20 raw (was 16/20), claims dropped 2/128 (was 3/148), median
  8.8 s, $0.00135/question: masking cost no answer quality.

## D10. Answers run as the user; the audit log is append-only and hash-chained
- **Constraints:** C2, C3, C5 (every question auditable: who, when, what they were shown).
- **One front door (`ask.py`):** `ask(user, question)` runs on the app role inside one transaction with `app.user`
  set, so row-level security (D8) and masking (D9) apply to retrieval, to what the model sees and to what comes back.
  A contract name the user can't read gets the same reply as one that doesn't exist (no existence leak).
- **Audit log (`audit.py`):** the app role has no privilege on the table (can't read, update or delete it, or run the
  verifier); it can only call `audit_append(payload)`, SECURITY DEFINER, which stamps the user from `app.user` and the
  time from the database (a client can't log as someone else; tested: a forged `app_user` in the payload is ignored).
  Each row stores sha256(previous hash | time | user | payload); `audit_verify()` returns the first row whose link or
  hash fails. Tested: editing a past row (even as admin) is caught at exactly that row.
- **What's logged:** question hash and length (not the text: a question can itself contain personal data), contract,
  retrieved and cited chunk ids, answered or not, claims kept/dropped, tokens, seconds, and **which chunks were shown
  with personal data unmasked** (empty for users without PII access). No answer text, so the log isn't a second copy
  of personal data. If the client wants question text, store it masked by the same detector.
- **Known limit:** a chain alone can't detect deletion of the newest rows (truncating the tail leaves a valid chain).
  Fix at deploy: anchor the latest hash outside the database on a schedule (e.g. S3 with Object Lock), and verify
  against the anchor.
- **Signature questions:** free-text questions about who signed route the contract's last 3 chunks into the pool, the
  same way front-matter fields route the first 3 (D6): signature blocks share no words with "who signed".
  Demo, same question, same contract: the analyst sees "/s/ [PERSON_2] … [PERSON_1]", legal_admin sees the names;
  both cite identical character offsets in the original. Not yet measured as an eval.

## D11. Field extraction: CUAD's definitions, code-side checks, per-field trust for auto-accept
- **Constraints:** C1 (every value has a verified quote), C5 (doubtful values go to a person).
- **13 fields** (CUAD categories): parties, agreement / effective / expiration date, renewal term, notice to stop
  renewal (days), governing law, termination for convenience, exclusivity, non-compete, cap on liability, uncapped
  liability, minimum commitment.
- **Pipeline (`extract.py`):** per field, a contract-scoped planned search (D6, pool 20, top 3) → union of passages
  (≤ 24, masked text: extraction never handles personal data) → ONE model call fills all 13 (value, exact quote,
  source id, high/medium/low). Code then locates each quote in its cited passage (offsets map to the original),
  and runs validators: ISO dates in range, expiration ≥ effective, notice 1–365 days, a known jurisdiction, ≥ 2
  parties, and a contradiction check (value "None; non-exclusive" but marked present).
- **Definitions = the labeling guideline.** v1 prompts used my own field descriptions; v2 uses CUAD's own category
  definitions (what the lawyers labeled against). Dev (95 contracts, 1,235 fields):

| | v1 (own wording) | v2 (CUAD definitions + contradiction check) |
|---|---|---|
| Field accuracy | 0.824 | 0.849 |
| Cap on liability F1 | 0.667 (recall 0.50) | 0.899 |
| Exclusivity F1 | 0.711 | 0.806 |
| Auto-accepted precision (high + checks) | 0.909 | 0.940 |
| Sent to review | 38% | 42% |

  Regressions in v2: minimum commitment recall 0.68 → 0.32 ("a fixed fee is not a commitment" overcorrected),
  non-compete F1 0.64 → 0.39. Not tuned further on dev (overfitting risk); both stay review-only.
- **Routing = a measured, per-field choice.** The model's "high" is not calibrated equally across fields, so only
  fields whose auto-accepted precision on dev was ≥ 95% may auto-accept (`TRUSTED`: renewal term, renewal notice,
  governing law, exclusivity, non-compete, cap on liability); dates, parties, termination for convenience,
  uncapped liability and minimum commitment always go to a person. Dev, in-sample: auto-accept precision 0.981 with
  70% of fields to review, vs. 0.940 / 42% for "any field, high + checks". The trade-off (precision promise vs.
  reviewer load) is the client's call; every review item arrives with its quote highlighted.
- **Review queue (`review.py`):** fields follow contract RLS; changes only via `review_field()` (SECURITY DEFINER:
  reviewer flag AND contract access), stamped with who/when and written to the audit log with old and new value;
  a re-extraction never overwrites a reviewed field. Reviewers: legal_admin, maria (her contracts only). Tested:
  kenji (not a reviewer) and maria on a Marketing contract are refused; the app role can't UPDATE fields.
- **Held-out test (415 contracts never used for tuning, 5,395 fields), run once with the frozen pipeline:**
  field accuracy **0.860** (dev 0.849); **auto-accepted precision 0.970** (target ≥ 0.95) with 69.5% of fields
  sent to review. Per trusted field: governing law 0.976, renewal notice 0.987, renewal term 0.98, cap on
  liability 0.978, non-compete 0.951, exclusivity 0.924 (the one trusted field under 95% on test → candidate to
  drop from TRUSTED). Weakest presence F1: minimum commitment 0.48, uncapped liability 0.65 (both review-only).
  Alternatives on test: "high, ignore checks" 0.94 / 39% review; "high or medium + checks" 0.879 / 5.8%.
- **Cost/speed (Haiku via CLI):** ~15–20 s per contract per model call, 5 in parallel; 415 contracts in ~25 min.

## D12. Portfolio questions: the model plans a filter, SQL finds the contracts
- **Why not RAG:** "which contracts auto-renew with < 60 days' notice?" needs every match out of 510; retrieval returns
  the top 8 passages. Measured on 10 of the 40 questions: plain RAG (the cited-answer pipeline over the whole
  portfolio) F1 **0.15** vs. **0.90** for filters on the same questions.
- **Design (`portfolio.py`):** the model only plans: conditions over the 13 extracted fields + contract type
  (present / absent / contains / ≤ / ≥ / before / after), or "unsupported" with a reason. Code compiles them to SQL
  with every value a bound parameter (tested with an injection string), runs it as the user (RLS), and returns each
  match with the value, quote, offsets and review status behind every condition, plus how many matches rest on
  unreviewed values. No conditions → no rows (never "everything"). Each query is audited.
- **Eval (`eval_portfolio.py`, 40 questions written with their intended filter; gold = that filter on the lawyers'
  labels, all 510 contracts, asked as legal_admin):** planner **40/40** (its plan on the gold labels gives exactly
  the gold set); end-to-end set F1 **0.84** (precision 0.84, recall 0.86); with the gold plan on the extracted
  fields also 0.84 → **all remaining error is extraction**, none is planning. Exact set match 4/40: one wrong field in
  510 breaks it, so F1 is the meaningful number (the spec's "exact ≥ 85%" target was unrealistic). Note: the 510
  include the 95 dev contracts the extraction prompts were tuned on.
- **Lever:** reviewed fields feed the same SQL, so the review queue is how a client raises this number; the UI shows
  "N matches rest on values nobody has reviewed yet" next to every answer.

## D13. API and web app: identity at the edge, every decision in Postgres
- **API (`api.py`, FastAPI):** every endpoint opens a connection as the app role and runs inside `as_user` (the
  transaction-scoped `app.user`), so the list of contracts, the text (masked or not), the fields and the review rights
  all come from row-level security, column grants and SECURITY DEFINER functions, never from Python checks. A
  contract that's missing or forbidden returns the same 404.
- **Identity for the demo:** an `X-User` header chosen with a user switcher. In a deployment the header is replaced by
  the identity provider's verified token (e.g. Cognito / OIDC → `app.user`); nothing else changes, which is the point
  of keeping the decisions in the database.
- **Web (`web/`, Next.js + Tailwind, shared design tokens with their own brand color):** Ask (whole portfolio or one
  contract), Contracts (only what RLS returns), a contract viewer (masked personal data as labelled pills; a citation
  or a key term highlights its exact characters in the original, mapped through the chunk's mask offsets), and the
  review queue (Accept / Edit / Not in contract for reviewers; read-only otherwise). The browser calls `/api`, which
  Next rewrites to the FastAPI service (one origin, no CORS).
- **Tests (`test_api.py`):** unknown user 401, each user's contract count (510 / 50 / 46), the same contract masked for
  the analyst and revealed for legal_admin, 404 for maria, review refused (403) for kenji.
