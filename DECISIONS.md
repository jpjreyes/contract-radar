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
  API and cap answer length; run pass^k on the absent set for the inconsistency. (Both done in D20: 7.5 s median;
  pass^3 17/20 raw, identical across runs.)

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
- **After the test run (Nov 10):** exclusivity removed from TRUSTED instead of tuning it on the test set
  (the fix would be judged on the data that's supposed to stay unseen). And a deterministic **5% spot check**: one in
  20 auto-accepted values goes to a reviewer anyway, so precision keeps being measured in production (reviewers'
  verdicts on that sample are the live precision number). Test with both: auto-accepted precision **0.976**, 74.1%
  of fields to review (spot checks included).
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

## D14. Tracing: OpenTelemetry spans per step, no contract text in traces
- **Why:** latency (C4) and cost need a per-stage breakdown to fix; an aggregate "9 s" doesn't say whether to cut the
  reranker pool, the prompt or the model.
- **Instrumentation (`tracing.py`):** OpenTelemetry SDK, one trace per request: `ask` → `retrieve` (method, k,
  chunk ids) → `rerank` → `reveal` (chunks returned unmasked) → `llm` (GenAI conventions: model, input/output
  tokens; plus cost in USD and model seconds) → `verify_citations` (kept/dropped) → `audit` (audit id); `portfolio`
  → `plan` → `llm` → `sql` (rows). FastAPI requests are spans too. Every backend is wrapped (`TracedBackend`), so
  extraction and evals are traced the same way. OpenInference span kinds (LLM / RETRIEVER / RERANKER / CHAIN /
  TOOL) let LLM-aware viewers lay traces out; export is plain OTLP, so the backend is a deploy choice.
- **Privacy rule:** spans carry ids, counts, tokens, cost and timing, never the question, contract text or model
  output (a trace store would otherwise be an unmasked copy of personal data, outside RLS). `CC_TRACE_CONTENT=1`
  adds them for local debugging only. Tested: a question's words never appear in any span attribute.
- **Viewer:** Arize Phoenix locally (OSS, LLM-aware). Off unless `CC_TRACE_ENDPOINT` is set.
- **Tracing as a reusable module:** the same spans work for any service with a model call.

## D15. "Act this quarter": deadlines computed from the extracted terms, never guessed
- **Why:** a question box reads as a chatbot; the client's real job is "what do we have to act on, by when?".
  The app now opens on that view; Ask moves to a tab.
- **The computation:** CUAD's contracts date from 1999–2020, but auto-renewing ones are still alive. For each,
  the end date is rolled forward term by term (respecting limited renewals: "one additional 3-year period",
  "up to 2 renewal terms") until it passes the as-of date; the deadline is that term's end minus the notice period.
  `parse_renewal` reads the extracted value into automatic / evergreen / not automatic / unclear (15 unit tests on
  real values). Unclear terms, a missing expiration or a missing notice period go to "Need a person", never guessed.
- **Every row shows its evidence:** the values it was computed from, each with its review status and a link to the
  clause. Expiration dates are review-only fields (D11), so every deadline visibly rests on an unreviewed date until
  someone reviews it: that is the honest state, and it tells the reviewer where to start.
- **On the data, as of 2026-11-11:** legal_admin sees 18 notice deadlines in the next 90 days (the first one 1 day
  out), 18 renewals whose window already closed, 189 contracts with uncapped liability, 59 needing a person; maria
  sees only her 3 deadlines (RLS applies to the view).

## D16. Azure deploy, sign-in, and limits for a public demo
- **Cloud (decided, ROADMAP cloud map):** Azure, because the buyer (legal) runs on Microsoft 365 and enterprise
  sign-in is the gap this project closes.
- **Shape (infra/azure, Terraform, `terraform validate` in CI):** Container Apps for the API and the web (scale to
  zero), PostgreSQL Flexible **with no public access** (VNet + private DNS), Key Vault read by a **managed identity**
  (no secrets in code or env files), Container Registry pulled by the same identity, Application Insights for the
  OpenTelemetry spans, a blob container with an immutability policy for the audit-chain anchor (D10's known limit),
  and a resource-group budget alert.
- **Sign-in:** Entra ID. The API verifies the bearer token (signature via the tenant's keys, issuer, audience,
  expiry); the person is mapped to an app role **inside Postgres** by their own id or by **directory security
  group** (`identity_map` + `resolve_identity`, SECURITY DEFINER; the app can't read or edit the mapping). Terraform
  creates one group per role. Access is then managed as group membership in the company directory and enforced by
  row-level security. The demo user switcher stays for local use (`CC_AUTH=demo`). 7 tests with real signed tokens:
  person mapping, group mapping, wrong audience, wrong issuer, expired, unmapped, header spoofing.
- **Public-demo limits:** a monthly model-spend cap checked before every model call (402 when used up; the radar,
  contracts and review queue need no model and keep working), a per-user hourly rate (429), and **replay mode**: the
  example questions' answers are recorded once and served without calling the model; anything else gets 409.
- **Found while preparing the load:** on a managed Postgres the admin is not a superuser, so FORCE ROW LEVEL
  SECURITY also applies to the owner of the SECURITY DEFINER functions; `reveal()` and `review_field()` would have
  silently seen no rows (legal_admin shown masked text, reviews not saved). Fixed with explicit owner policies (the
  functions check permissions themselves); the cloud load ends with a smoke test of exactly this.
- **Data into a private database:** `cloud_load export` writes the processed tables as CSV (COPY), the archive goes
  to private blob storage (Entra auth, no keys), and a one-off Container Apps job inside the VNet downloads it with
  its managed identity, loads the data **before** switching RLS on, then runs the setup and the smoke test. Tested
  end to end on a scratch local database (510 contracts, 22,706 chunks, 6,630 fields; smoke test passed).
- **Images:** API 1.14 GB (CPU-only PyTorch on Linux; embedding model and reranker baked in; PII detection moved to
  an `ingest` dependency group the image skips); built and smoke-tested in CI, built for Azure with `az acr build`
  (nothing heavy builds on the laptop).

## D17. M7: a small local model for clause presence, and a cascade with the LLM
- **Question:** today the LLM decides, per contract, whether each of the 13 terms is there (~$0.0015 and ~15 s per
  contract, and the contract text leaves the server). Can a small model running on our own hardware do part of that?
- **Model:** bge-small (33M parameters, the same encoder the search uses), fine-tuned as a 13-label classifier on
  every chunk's **masked** text (D9: no personal data in training); contract presence = the highest chunk probability
  ≥ a per-field threshold. 2 epochs, max 256 tokens, positives weighted up (they're rare). Trains in ~17 min on a
  laptop GPU; scores a contract in **0.44 s** locally, $0.
- **Splits (contract level, fixed by hash):** train = half of the retrieval "test" contracts; thresholds and the
  confidence band = the 95 dev contracts; **evaluation = the other half of test** (217 contracts), never seen by the
  model or the thresholds. The LLM's numbers are recomputed on the same contracts, so the comparison is head to head.
- **Two label sources, same model and data:** `gold` = chunks overlapping the lawyers' spans (what a client with
  labeled data has); `llm` = chunks holding the quote our LLM extraction cited (no human labels at all: true
  distillation).
- **Cascade:** the small model decides when it's confident (probability inside a band chosen on dev so those
  decisions are ≥ 95% right), otherwise the LLM's answer is used.

| Mean presence F1 over 13 fields (eval half) | small model alone | LLM alone | cascade | LLM calls avoided |
|---|---|---|---|---|
| trained on lawyer labels | 0.779 | 0.843 | **0.858** | **31%** |
| trained on lawyer labels, second run (same setup; this is the saved model) | 0.765 | 0.843 | 0.850 | 32% |
| trained on the LLM's own answers (no human labels) | 0.723 | 0.843 | 0.846 | 26% |

- **Where the small model wins:** minimum commitment (**0.736 vs the LLM's 0.531**), exclusivity (0.761 vs 0.747),
  parties and agreement dates (≥ 0.95, on par). It decides governing law alone for 91% of contracts (0.94 vs 0.98).
- **Where it loses badly:** renewal term, notice to stop renewal, termination for convenience (0.59–0.69 vs
  0.87–0.92): these need reading conditions ("renews unless notice…"), which a chunk classifier doesn't do.
- **What it's used for:** (1) the cascade is the cost/privacy story: a third of the per-contract model calls can be
  skipped with no loss in F1 (+0.015); (2) the lawyer-label model finds clauses the search misses, so it now feeds
  extraction's sources for the weakest fields (D18). Gold beats LLM labels by 0.056 on the small model alone; with
  no labeled data, distillation from the LLM still gets a cascade at the LLM's level.
- **Run-to-run variance:** two identical lawyer-label runs differ by 0.014 (small model) and 0.008 (cascade), so
  differences below ~0.01 are noise; the cascade beats the LLM in both runs.
- **Known limits:** two runs for lawyer labels, one for LLM labels; the eval half is 217 contracts,
  so fields with < 60 positives move a few points between runs; presence only (values and quotes stay with the LLM).
- Run: `uv run python -m contract_copilot.distill --labels gold|llm --epochs 2` (progress every 50 steps); results in
  `runs/distill-*.json`; the trained model is saved to `data/models/m7-<labels>/` (not in git).

## D18. Weak extraction fields: clearer definitions, the small model as a second retriever, and as a judge
- **Diagnosis first (dev, 95 contracts):** for the weakest fields, was the lawyers' clause even among the passages
  the model read? Minimum commitment 13/25, non-compete 7/14, termination for convenience 25/29, uncapped liability
  22/22. Reading 6 passages per field instead of 3 adds 1 clause. So two different problems: **search misses** for
  minimum commitment and non-compete; **judgment** for uncapped liability (16 false alarms, mostly "no cap is stated,
  so the indemnity is uncapped": the lawyers only label it when the contract says so).
- **Changes (each tried on dev only; test touched once at the end):**
  1. Definitions: uncapped liability only when the contract says so (unlimited, or carved out of a cap); the absence
     of a cap or an indemnity without a maximum is not. Minimum commitment includes minimum purchase, volume,
     capacity and spend obligations, as the lawyers labeled it. (A termination-for-convenience rewording lost F1 on
     dev and was reverted.)
  2. **The M7 small model as a second retriever:** its 2 highest-scoring chunks for minimum commitment and
     non-compete are added to the passages (tried for termination for convenience too: lost precision, removed).
  3. **The small model decides presence for minimum commitment**, the one field where it beat the LLM on dev
     (0.69 vs 0.50) and on the held-out half in both D17 runs; the LLM still reads values everywhere else. A
     contract it flags gets the clause highlighted and goes to a person (the field never auto-accepts).

| Dev (95 contracts), presence F1 | before | definitions | + small model retrieves | + small model decides (shipped) |
|---|---|---|---|---|
| minimum commitment | 0.457 | 0.571 | 0.500 | **0.691** |
| non-compete | 0.385 | 0.480 | 0.645 | 0.563 |
| uncapped liability | 0.690 | 0.840 | 0.846 | 0.840 |
| termination for convenience | 0.754 | 0.697 | 0.687 | 0.746 |
| field accuracy (13 fields) | 0.849 | 0.857 | 0.866 | 0.861 |
| auto-accepted correct | — (trust rules came later) | 97.0% | 97.7% | **99.1%** |

- **Test, run once (the reported number).** Headline on the 217 test contracts the small model never saw in any
  form (its training used the other half's lawyer labels); all 415 below for continuity with D11:

| Test | before (D11) | after (D18) |
|---|---|---|
| held-out half (217): field accuracy | 0.866 | **0.878** |
| held-out half: minimum commitment F1 | 0.531 | **0.712** |
| held-out half: uncapped liability F1 | 0.692 | **0.773** |
| held-out half: non-compete F1 | 0.680 | **0.752** |
| held-out half: exclusivity F1 | 0.747 | **0.832** |
| held-out half: auto-accepted correct / sent to a person | 98.2% / 74.4% | **98.3% / 73.4%** |
| all 415: field accuracy | 0.860 | 0.875 |
| all 415: auto-accepted correct / sent to a person | 97.6% / 74.1% | 97.8% / 72.5% |

- **Knock-on effect, portfolio questions (D12, re-run):** set F1 **0.84 → 0.865** (planner still 40/40; the oracle
  plan scores the same, so the gain is all extraction). Act this quarter (legal_admin): uncapped liability 189 → 153
  contracts (fewer false alarms), 57 sent to a person; notice deadlines unchanged at 18.
- **Noise:** dev runs of the same configuration move single fields by up to ±0.1 (non-compete has 14 positives), so
  only the held-out test numbers are reported; trusted fields stayed ≥ 95% precise on dev in the shipped setup.
- **Cost:** the small model adds ~0.5 s per contract on a laptop GPU and $0; the LLM prompt grows by ≤ 4 passages.
- **Still weak:** uncapped liability precision 0.67 (the model over-reads carve-outs), minimum commitment 0.71,
  expiration dates (value right 87%): all stay review-only.

## D19. Personal-data detector v3, measured on a fresh sample
- **Why:** D9's misses on test2 were "Surname, Given" lists (inventor tables), non-US and suite-first addresses,
  repeated labels ("By: By: Jimmy Wayne Anderson"), nicknames ("BaoQing (Angel) Song") and initials blocks
  ("Initials: CL MD"). test2 had been inspected, so a **fresh test3** (120 chunks, 60 likely-PII + 60 random,
  183 items, labeled by Sonnet before any v3 code was written) is the reported number.
- **v3 rules:** surname-first names only inside a list (a ';' / '&' / column gap before and after) and never when a
  zip/postcode or a firm suffix follows ("Morgan, Lewis & Bockius LLP", "Seattle, Washington 98104"); nicknames
  inside names; label words can't be swallowed into a name; two signature columns run together are cut to the first
  name; initials after "Initials:"; addresses with a leading suite, compound directions ("Drive Northwest"), "Box",
  street-then-number (Germany, Netherlands, Nordics), and UK building/road names only when a UK postcode follows.
- **Tried and dropped: a corpus-wide name registry** (names found in one contract masked in all others). Zero recall
  gain on dev, test and test2, precision 0.955 → 0.892 on test: it spread junk ("Governing Law", "Signature Page",
  "Chief Operating") that the per-contract checks let through. Removed.

| Detector, recall / precision | v2 (D9) | v3 |
|---|---|---|
| dev (tuned on) | 0.956 / 0.994 | 0.967 / 0.994 |
| test (inspected) | 0.926 / 0.965 | 0.960 / 0.970 |
| test2 (inspected for v3) | 0.831 / 0.936 | 0.965 / 0.951 |
| **test3 (fresh, reported)** | **0.902 / 0.944** | **0.907 / 0.944** |

- **Reading it honestly:** v3 fixes the patterns it was written for (test2 +0.13) and costs nothing elsewhere, but on
  fresh data it adds one address. The fresh sample also shows v2 was better than test2 suggested (0.90, not 0.83):
  single samples of ~120 chunks swing by several points. Remaining misses on test3: international addresses (Hong
  Kong, Osaka, Lima, UK building names), names in unlabeled table columns, "van der" particles; false positives:
  company names used alone ("Miltenyi", "Corio's"), signature formulas ("THUS DONE AND SIGNED").
- **Enforcement is what protects users, not detector recall:** re-masked all 510 contracts with v3 (222 chunks
  changed and were re-embedded), then re-ran both enforcement evals: **0 personal-data leaks in 4,841 served texts**
  and **0 permission leaks in 2,129 returned chunks**. A missed name is still only visible to users who can read that
  contract. The exposure probe also lists masked values that appear unmasked elsewhere: mostly detector false
  positives that cost readability, not privacy ("Governing Law" or "ARTICLE XIII" masked as a person in one contract).

## D20. Final evals on the Claude plan, isolated configuration (2026-11-11)
- **How the numbers were produced:** Claude Haiku via the Claude CLI on a Claude plan, in an isolated configuration
  (no user plugins, settings or instructions; one sentence is added to every prompt by an account-managed hook that can't be
  turned off); the runner refuses an API key and stops at 80% of the 5-hour or weekly limit. Costs are
  API-equivalent. Re-run in order: extraction of all 415 test contracts, extraction eval, answers, personal data in
  answers (portfolio questions were re-run first: planner 40/40, set F1 0.865, unchanged). The whole set used 8 points
  of the 5-hour window and 1 point of the weekly limit.

| Measure | before (D11/D18, D7/D9) | final |
|---|---|---|
| Extraction, all 415 test: field accuracy | 0.875 | **0.879** |
| Extraction, all 415: auto-accepted correct / sent to a person | 97.8% / 72.5% | **97.9% / 72.5%** |
| Extraction, held-out half (217): field accuracy | 0.878 | **0.887** |
| held-out half: minimum commitment / uncapped liability / non-compete F1 | 0.712 / 0.773 / 0.752 | 0.712 / **0.797** / 0.727 |
| held-out half: auto-accepted correct / sent to a person | 98.3% / 73.4% | 98.2% / 73.0% |
| Answers (dev, 50): answered when the clause exists | 27/30 | **28/30** |
| Answers: citation on the lawyers' span | 25/27 | **26/28** |
| Answers: says absent when the label says absent | 17/20 raw · 18/20 adjudicated | **17/20 raw · 19/20 adjudicated** |
| Answers: claims dropped for unverifiable quotes | 2/128 | 2/129 |
| Answers: median latency · cost per question | 8.8 s · $0.00135 | **7.5 s · $0.00116** |
| Personal data in answers (analyst, 10 contracts, asked for exactly that data) | 0 values | **0 values** (2–13 placeholders per answer; legal_admin sees 2–14 real values) |

- **Adjudication:** the 3 raw "absent" disagreements are the same contracts already reviewed in
  `evals/adjudication.json`: 2 CUAD label misses (Pareteum renewal notice, Middlebrook termination for convenience)
  and 1 real over-call (Ingevity: non-exclusive licenses read as exclusivity). Marshall, the inconsistent case in D7,
  came back absent this time.
- **Consistency (pass^k, closes D7's open item):** the 20 "absent" questions run 3 more times: **17/20 in every
  run, the same 3 contracts each time** (pass^3 = 17/20 raw, 19/20 adjudicated; Marshall absent 3/3). The
  remaining errors are systematic, not random: 2 label misses and the Ingevity over-call. Cost: 3 points of the
  5-hour window, 0 of the week.
- **Noise:** the two answer misses when the clause exists are both expiration dates; non-compete moved −0.025 on the
  held-out half (54 positives) with no code change, inside the run-to-run swing noted in D18.
- **Knock-on, Act this quarter (legal_admin, as of 2026-11-11):** notice deadlines 18 (unchanged), uncapped
  liability 153 → 151, sent to a person 57 → 62. Replay answers for the public demo re-recorded.
- **Latency:** median 7.5 s is now under the 8 s target (C4) even on the CLI backend; the API number is measured on
  the deployed version.
