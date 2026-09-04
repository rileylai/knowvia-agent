# API Contract

This document covers the implemented HTTP API. Non-Telegram application and
operator routes use the configured bearer boundary; when
`API_BEARER_TOKEN` is unset, that compatibility boundary permits requests.
The Telegram webhook uses its own Telegram secret and chat-authorization
boundary. `/health`, `/ready`, and `/metrics` are public operational endpoints.

## Authentication and error envelope

When `API_BEARER_TOKEN` is set, protected callers send:

```http
Authorization: Bearer <configured-token>
```

Route and middleware failures generally use a bounded FastAPI `detail` object:

```json
{
  "detail": {
    "error_code": "LLM_OUTPUT_INVALID",
    "message": "Proposal validation failed",
    "failure_reason": "LLM_OUTPUT_INVALID",
    "workflow_run_id": 123
  }
}
```

Messages are safe summaries; raw provider, database, Notion, or parser
exceptions are not exposed.

## Liveness and readiness

### `GET /health`

Returns `200 {"status":"ok"}` without contacting dependencies.

### `GET /ready`

Returns a dependency report with `status`, `mode`, and named checks. It returns
`503` when required database, migration, pgvector, provider, Notion, Redis, or
RQ scheduler checks are unavailable.

### `GET /metrics`

Returns fixed Prometheus text for safe workflow, stale-run, cost, and scrape
failure metrics. It does not include workflow metadata or source content.

## Notion indexing

| Method and path | Request | Behavior |
| --- | --- | --- |
| `POST /api/notion/index/page` | `{ "page_id": "..." }` | Read and replace one page's derived snapshot |
| `POST /api/notion/index/incremental` | `{ "page_ids": ["..."] }` | Reconcile known manually changed pages |
| `POST /api/notion/index/full` | no body | Discover and index all accessible pages |
| `GET /api/notion/index/status` | `?workflow_run_id=<id>` optional | Read the latest or selected persisted indexing workflow |

The synchronous indexing responses are endpoint-specific:

- page index returns `workflow_run_id`, `status`, `page_id`, `page_title`,
  `notion_path`, and `indexed_block_count`;
- incremental index returns workflow/status/sync mode, processed count, and an
  `indexed_pages` list containing page identity, title/path, and block count;
- full index returns workflow/status, discovered and processed counts, and the
  same per-page list;
- status returns persisted workflow type/status, failure reason, timestamps,
  and safe metadata. It does not re-read Notion.

These HTTP indexing mutations run synchronously and return their completed
response. The queued `202` behavior described below belongs to the Telegram
webhook path. Page replacement is atomic; incremental sync preserves earlier
successful page commits when a later page fails.

## Source ingestion

| Method and path | Request |
| --- | --- |
| `POST /api/ingest/source` | JSON `{source_type, source_display_name, raw_text}` |
| `POST /api/ingest/document` | multipart file field `document`; PDF only |
| `POST /api/ingest/url` | JSON `{ "url": "https://..." }` |
| `POST /api/ingest/youtube` | JSON `{ "url": "https://youtube.com/..." }` |
| `POST /api/ingest/chat-text` | JSON `{ "chat_text": "...", "source_display_name": "..." }` |
| `POST /api/ingest/image-ocr` | multipart image files; bounded OCR batch |

Each successful response returns `workflow_run_id`, `source_document_id`,
`source_type`, `source_display_name`, and a content hash. Ingestion stores a
normalized source; it does not append to Notion.

The current limits are documented in [Workflows](02-workflows.md). URL
validation blocks local/private destinations and unsafe redirects.

## Supplement proposals and review

### `POST /api/supplement/propose`

Request:

```json
{
  "source_document_id": 12,
  "provider_name": "openai",
  "model": "gpt-4o-mini",
  "target_notion_page_id": "optional-external-page-id"
}
```

The result contains a pending `change_request_id`, duplicate status, target
page id when assigned, provider/model, and optional token counts. Provider
output is limited to title, summary, concepts, and notes; the backend derives
source, target, citations, and identity.

### `GET /api/supplement/pending`

Returns a bounded list of pending proposals. The optional `limit` query is
bounded by the route. It is read-only and does not call Notion.

### `GET /api/supplement/{change_request_id}`

Returns one reviewable proposal, its target page, source display information,
and citations. It is read-only.

### Review mutations

| Method and path | Request | Notion behavior |
| --- | --- | --- |
| `POST /api/supplement/accept` | `{change_request_id, reviewer?}` | Append to `AI Supplement Zone`, verify identity, re-index, then accept |
| `POST /api/supplement/reject` | `{change_request_id, reviewer?, reason}` | Change state only; no Notion write |
| `POST /api/supplement/edit-later` | `{change_request_id, reviewer?, reason?}` | Keep the proposal pending; no Notion write |

The HTTP API does not currently expose a Change Target mutation; Telegram's
owner-bound page picker invokes that application workflow.

Accept locks and revalidates the Change Request only in its final database
transaction, after append verification and page-snapshot preparation. That
transaction persists the prepared page and marks `accepted` together. Change
Target also uses `SELECT ... FOR UPDATE` while validating and replacing its
target identity. Reject and Edit Later re-read and validate `pending` inside
their transaction but do not currently request a row lock. A stale observed
state returns `409 INVALID_STATE_TRANSITION`. A failed or uncertain append
keeps the request pending for identity-based recovery. The final Accept lock
does not cover the earlier external Notion append, and simultaneous unkeyed
review requests are not a fully serialized contract; mutation callers should
send a stable `Idempotency-Key` and operators must reconcile uncertain append
outcomes by visible identity.

## Grounded QA

### `POST /api/qa`

Request fields:

```json
{
  "query": "What is attention?",
  "top_k": 5,
  "page_ids": ["optional-page-id"],
  "section_paths": ["optional/path/prefix"],
  "source_kinds": ["notion"],
  "provider_name": "openai",
  "model": "gpt-4o-mini"
}
```

`top_k` is bounded to 1–20. The response includes the grounded answer,
`insufficient_info`, retrieved count, backend-owned Notion citations, provider,
model, and optional token counts. Vector retrieval uses pgvector when
available; lexical fallback is explicit and safe.

## Telegram gateway

### `POST /api/telegram/webhook`

Accepts a Telegram update with an optional `update_id`, message, document/photo,
or callback query. When `TELEGRAM_WEBHOOK_SECRET` is configured, the request
must include `X-Telegram-Bot-Api-Secret-Token`. When
`TELEGRAM_ALLOWED_CHAT_IDS` is configured, the chat id must be allowlisted.

The response includes workflow/status fields and may include a bounded reply,
source document id, change request id, review state, target selection, or
citations. With Redis, a newly claimed update is queued and returns `202` with
a running/queued response; the worker later persists the terminal outcome in
the Telegram update ledger and associated workflow runs. Without Redis, the
compatibility path executes synchronously and returns the resulting status. A
repeated non-null `update_id` returns the persisted running or terminal outcome
without repeating business work.

## Mutation idempotency

`POST /api/ingest/*` and `POST /api/supplement/*` accept an optional
`Idempotency-Key` header. The key is scoped by method and path. The same key
and canonical payload replay the stored safe response; a different payload
returns `409 IDEMPOTENCY_KEY_CONFLICT`; an in-progress owner returns
`202 IDEMPOTENCY_IN_PROGRESS`.

Telegram uses its durable `update_id` ledger instead of this middleware. The
ledger and API idempotency records do not retain raw request payloads.

For Telegram command syntax, see [Telegram operator contract](11-telegram-operator-contract.md).
