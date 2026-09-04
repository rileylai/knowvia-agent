# Evaluation Contract

Evaluation separates deterministic acceptance from opt-in dependency checks.
The default suite must not contact a real Notion workspace, send Telegram
messages, consume provider quota, or mutate a shared database.

The parser and note completeness benchmark is governed by
[ADR-0009](decisions/0009-parser-note-completeness-benchmark-contract.md).
Its diagnostic manifests, runner/lane/renderer path, C01 Generation/End-to-end
Q14 slice, and 13 draft review packets are implemented. Formal fixture
evidence, reviewed gold, Parser metric contracts, calibration, formal
provenance/store schemas, and baseline authority remain pending. See the
[operational roadmap](../dev_state/parser-note-completeness/03-roadmap.md) for
the exact evidence boundary.

## Verification levels

| Level | Current mechanism | Acceptance meaning |
| --- | --- | --- |
| Deterministic unit and integration | `pytest`, in-memory adapters, fixtures, SQLite, and isolated repository tests | Required; failures block acceptance |
| Deterministic evaluation harnesses | Retrieval, citation, vector retrieval, write safety, manual sync, and prompt-injection programs | Required for the affected capability |
| Adapter fixture matrix | Real parser libraries or injected transports for PDF, URL, OCR, and other boundaries | Required where the adapter changes; no network by default |
| Opt-in live dependency smoke | Explicit environment guards and dedicated Notion, PostgreSQL/pgvector, provider, or Telegram resources | Evidence for integration readiness; a skip is not a pass |
| Guarded live end-to-end | Read-only Notion canary or explicitly approved append canary against a sandbox page | Manual release evidence; never an implicit test action |

No automated test proves production process supervision, TLS termination,
backup scheduling, long-running real-workspace capacity, or ongoing provider
compatibility. Those remain deployment or release checks rather than claimed
test coverage.

## Golden question set

`tests/evals/golden_questions.yaml` is a schema-validated synthetic fixture.
Each case defines a query, bounded page/section scope, expected exact Notion
paths, required terms, forbidden pending/rejected text, and whether retrieval,
citation, and production-RAG exclusion checks apply. Cases cover manual notes
and accepted content under `AI Supplement Zone`.

The deterministic retrieval harness builds an isolated synthetic database. It
does not read a private workspace. Source-document decoys containing pending
and rejected text verify that the production repository admits only eligible
Notion chunks before ranking.

Live Notion verification uses a separately configured resource:

- the read/index/QA canary requires `LEARNLOOP_RUN_NOTION_READ_CANARY=1`,
  `NOTION_TOKEN`, and `LEARNLOOP_NOTION_CANARY_PAGE_ID`; its transport blocks
  every Notion write;
- the append canary requires the live CLI option, explicit approval option,
  token, and dedicated canary page id; its transport permits only block reads
  and append-shaped child writes and rejects other mutation shapes;
- live adapter checks require `--live` or
  `LEARNLOOP_RUN_ADAPTER_SMOKE_LIVE=1`; Telegram sending additionally requires
  `LEARNLOOP_SMOKE_ALLOW_TELEGRAM_SEND=1` and a dedicated chat;
- live vector smoke requires `LEARNLOOP_RUN_LIVE_VECTOR_SMOKE=1`, provider
  credentials, and an isolated PostgreSQL/pgvector target.

Never place a token, private page id, Telegram chat id, or complete database URL
in this documentation or a committed fixture.

## Metrics and acceptance criteria

| Contract | Current calculation | Pass condition |
| --- | --- | --- |
| Retrieval hit rate | Fraction of enabled golden questions whose retrieved top-k contains at least one expected exact path | Reported as `hit_count / total`; the harness has no configured release threshold, so regressions require review |
| Exact page/path match | Exact string membership after applying each case's page and section scope | Every case asserted by its deterministic test must match the expected path |
| Citation accuracy | Fraction of enabled cases whose deduplicated citation paths contain an expected exact path | Default harness threshold is `1.0`; configurable CLI values must be declared in the verification record |
| Insufficient information | QA returns `insufficient_info=true`, no fabricated answer, and no citations when the safe retrieval scope has no support | Deterministic scenario must pass; there is no aggregate score threshold |
| Production eligibility | Repository filtering excludes source-document, pending, rejected, known synthetic PostgreSQL pages, and out-of-scope page/section candidates before ranking | All exclusion scenarios pass |
| Duplicate append prevention | Two writer calls with the same change-request identity result in one append and an idempotent replay | All write-safety checks pass |
| Page replacement atomicity | Failure before or during replacement leaves the prior complete page snapshot; successful replacement commits page, blocks, chunks, and vectors together | Transaction and failure-injection tests pass; no numeric score is defined |

The write-safety harness also requires original blocks to remain unchanged,
append targets to remain under `AI Supplement Zone`, and policy violations to
perform no write. Prompt-injection checks require source and retrieval text to
remain data rather than authority over targets, tools, citations, or review
state.

## Reproducible commands

```bash
uv sync --dev
uv run --no-env-file --frozen pytest -q
uv run --no-env-file --frozen python scripts/preflight.py --profile test --json
uv run --no-env-file --frozen python tests/evals/retrieval_eval.py
uv run --no-env-file --frozen python tests/evals/citation_accuracy_eval.py
uv run --no-env-file --frozen python tests/evals/vector_retrieval_eval.py
uv run --no-env-file --frozen python tests/evals/write_safety_eval.py
uv run --no-env-file --frozen python tests/evals/manual_sync_eval.py
uv run --no-env-file --frozen python tests/evals/prompt_injection_eval.py
uv run --no-env-file --frozen python tests/evals/adapter_smoke_matrix.py --json
```

Guarded release and recovery inspections are separate and redacted:

```bash
uv run --no-env-file --frozen python scripts/postgres_restore_drill.py --json
uv run --no-env-file --frozen python scripts/notion_db_recovery_drill.py --json
uv run --no-env-file --frozen python scripts/release_gate.py --json
```

An unavailable dependency, omitted opt-in guard, or skipped live operation is
inconclusive, not successful evidence.

## Step 99 hybrid retrieval decision

Step 99 completed on 2026-08-12 with the formal decision
`maintain_vector_primary`; see
[ADR-0008](decisions/0008-retain-vector-primary-after-hybrid-evaluation.md).
The selected weighted-RRF candidate improved reciprocal-rank sum by
`2.666666666666`, below the preregistered `2.700` requirement. The threshold is
evaluated at frozen precision and is not rounded into a pass.

Keyword-only reported the highest aggregate metrics but was preregistered only
as a comparison baseline, not a production replacement candidate. Its current
ASCII token-set scorer has limited multilingual generalization, and this
offline evaluation provides no production-scale latency, resource, or traffic
evidence. The result supports retaining lexical fallback, not switching to
keyword-primary retrieval.

Step 99 citation tables use *invalid-to-qrel path* to mean a retrieved path not
present in the annotated qrels for that query. The term does not mean a path is
structurally invalid, unsafe, or fabricated. Independent and golden citation
conformance each retained zero invalid citations. Production remains exact
body-only, query-embedding-first, repository-owned exact filtered cosine, with
lexical fallback only when the vector query fails or eligible vectors are
unavailable. Step 100 remains deferred.
