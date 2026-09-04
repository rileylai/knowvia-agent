# Observability

## Scope

The current runtime provides structured process logs, persisted workflow
status and metadata, redacted operator views, readiness checks,
Prometheus-style metrics, and cost aggregation. It does not include a tracing
backend, dashboard, durable log store, or persistent time-series service.

PostgreSQL `workflow_runs` is the durable operational record used by the
runtime. It stores type, `running`/`succeeded`/`failed` status, safe failure
reason, start/finish timestamps, and operation-specific JSON metadata such as
bounded counts and known usage/cost. Telegram replay state is stored separately
in `telegram_update_ledger`; HTTP mutation replay state is stored in
`api_idempotency_records`.

The schema contains an `audit_logs` table, but no current runtime repository or
writer inserts into it. Its existence must not be interpreted as a complete or
queryable audit trail.

## Safe request and workflow signals

Each HTTP request accepts or generates `X-Workflow-ID`, stores it as the request
correlation id, includes it in request logs, and returns it in the response
header. Business workflows also receive that value as `request_workflow_id`
metadata while using their numeric `workflow_run_id` as the durable status
identity. Request logs contain only correlation id, path, method, status code,
duration, and a short event name. Workflow metadata may contain operation
names, bounded counts, provider/model and prompt versions, retrieval mode,
retry counts, complete provider usage, and known cost.

The following are never logged, persisted as general metadata, or returned by
operator surfaces:

- API keys, bearer values, Telegram bot tokens, webhook secrets, and Notion
  tokens;
- callback tokens, Redis keys, credential-bearing URLs, and raw provider
  responses;
- prompts, OCR/source text, Notion page content, page identities where not
  required by the durable index record, and vectors.

Cost remains unknown when model pricing or complete provider usage is missing.
Planner estimates are never presented as billing usage.

## Operational endpoints

| Endpoint | Behavior |
| --- | --- |
| `GET /health` | Shallow process liveness; does not probe dependencies |
| `GET /ready` | Dependency-aware readiness; returns `503` when required checks fail |
| `GET /metrics` | Fixed Prometheus text with safe numeric aggregates |
| `GET /api/ops/workflows` | Protected redacted workflow list |
| `GET /api/ops/workflows/{id}` | Protected redacted workflow detail and stale flag |
| `POST /api/ops/workflows/{id}/reconcile` | Protected terminal reconciliation for a stale running workflow; never reruns work |
| `GET /api/ops/cost` | Protected cost and budget aggregate |

`/ready` checks database connectivity, migration state, pgvector, mode-specific
provider configuration, Notion configuration, Redis, and the RQ scheduler
when required. Liveness and readiness are intentionally separate.

Telegram exposes bounded views over the same persisted data:

- `/workflow [workflow_id]` shows one redacted workflow or a bounded recent
  list, including status, safe failure reason, age/stale state, and known cost;
- `/cost` aggregates cost fields already recorded in workflow metadata and
  keeps incomplete pricing or usage as `unknown`;
- `/index-status [workflow_id]` reads the selected or latest indexing workflow,
  including safe counts, remaining work, failure reason, stale state, and known
  cost. It does not inspect RQ jobs or re-read Notion.

## Retrieval and indexing metadata

QA records `pgvector_exact_cosine` or `lexical_fallback`, plus a safe fallback
reason when applicable. Indexing records embedding provider/model/dimensions,
batch and retry counts, and complete usage/cost only when trustworthy.

An indexing failure records safe page-count or workflow status fields. It does
not expose page content, chunk text, embedding inputs, vectors, or raw
dependency errors. A stale prepared snapshot uses `STALE_PAGE_SNAPSHOT` and
fails before page replacement.

## Telegram signals

Telegram workflow metadata separates:

- `business_status` for source/proposal/review work;
- `callback_ack_status` for `answerCallbackQuery` delivery;
- `preview_delivery_status` for the post-commit preview message.

This allows a callback acknowledgement or preview delivery problem to be
recovered without repeating committed OCR, provider, source, or review work.
The update ledger remains the outer idempotency boundary.

The worker validates the canonical module-level RQ callables before consuming
the queue, uses an embedded scheduler, and logs only safe queue/worker fields.
Review Accept timeout failures keep `business_status=failed` and callback
acknowledgement status recorded independently; their safe failure reason is
`QUEUE_JOB_TIMEOUT`, not `UNKNOWN_ERROR`.

## Failure taxonomy

Common workflow and dependency reasons include:

```text
AUTHENTICATION_FAILED
AUTHORIZATION_FAILED
NOTION_AUTH_FAILED
NOTION_PAGE_NOT_FOUND
NOTION_APPEND_NOT_VERIFIED
NOTION_BLOCK_FETCH_FAILED
STALE_PAGE_SNAPSHOT
LLM_PROVIDER_ERROR
LLM_OUTPUT_INVALID
EMBEDDING_PROVIDER_NOT_CONFIGURED
EMBEDDING_PROVIDER_ERROR
VECTOR_QUERY_FAILED
VECTOR_DATA_UNAVAILABLE
TELEGRAM_QUEUE_UNAVAILABLE
QUEUE_JOB_TIMEOUT
TELEGRAM_SEND_FAILED
TELEGRAM_CALLBACK_ACK_FAILED
TELEGRAM_PREVIEW_DELIVERY_FAILED
WORKFLOW_AUDIT_UPDATE_FAILED
WRITE_POLICY_VIOLATION
```

Upload and parser failures use specific reasons such as
`PDF_PAGE_LIMIT_EXCEEDED`, `IMAGE_PIXEL_LIMIT_EXCEEDED`,
`EXTRACTED_TEXT_LIMIT_EXCEEDED`, `URL_SSRF_BLOCKED`,
`URL_RESPONSE_TOO_LARGE`, and `YOUTUBE_TRANSCRIPT_NOT_FOUND`.

## Recovery and reconciliation

The final workflow audit update is separate from business commit. If it fails,
the workflow may remain `running` until an operator confirms the business
outcome and explicitly reconciles it. Reconciliation is dry-run first and only
transitions an eligible stale workflow; it never repeats business work.

See [incident recovery](runbooks/incident-recovery.md), [backup and restore](runbooks/backup-restore.md),
and the [Telegram operator contract](11-telegram-operator-contract.md).
