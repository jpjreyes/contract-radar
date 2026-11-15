# Eval results

The runs behind every number in the main README, as written by the eval scripts (`src/contract_radar/eval_*.py`).
Extracted values, contract text and personal-data strings are left out; metrics, per-question outcomes and settings
are kept. How each run was produced and what changed between runs: [DECISIONS.md](../../DECISIONS.md).

| File | README claim | Decision |
|---|---|---|
| `retrieval-test.json` | clause in the top 8 for 89.1% (5,506 clauses, every method) | D3–D5 |
| `permissions.json` | 0 permission leaks in 2,129 returned chunks | D8 |
| `pii-exposure.json` | 0 personal-data values in 4,841 served texts; app role can't select the original | D9, D19 |
| `pii-detector-v2-vs-v3.json` | detector recall / precision on dev, test, test2 and fresh test3 | D19 |
| `pii-answers.json` | 0 personal-data values in analyst answers that ask for exactly that data | D20 |
| `extraction-test.json` | field accuracy 0.879, auto-accepted 97.9% correct (415 contracts) | D11, D18, D20 |
| `extraction-test-heldout-half.json` | per-field F1 on the 217 contracts the small model never saw | D18, D20 |
| `small-model-cascade.json` | small model vs LLM vs cascade, LLM calls skipped | D17 |
| `portfolio.json` | planner 40/40, set F1 0.865, plain RAG 0.15 | D12, D20 |
| `parity-api.json` | API parity 5/5: deployed app vs the final eval, 10.7 s median on the API | D21 |
| `answers-dev.json` | 28/30 answered, 26/28 on the lawyers' span, 17/20 raw / 19/20 adjudicated; `absent_consistency`: the same result in 4 runs | D7, D20 |

Adjudications of disagreements with CUAD's labels: [`../adjudication.json`](../adjudication.json) (answers) and
[`../pii_adjudication.json`](../pii_adjudication.json) (personal-data labels). Model: Claude Haiku via the Claude CLI
on a Claude plan in an isolated configuration; costs are API-equivalent.
