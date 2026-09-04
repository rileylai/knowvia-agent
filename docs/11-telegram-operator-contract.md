# Telegram Operator Contract

The Telegram webhook is a transport boundary. It validates the envelope and
trust policy, claims update idempotency, and delegates a typed intent. It does
not call Notion, PostgreSQL, Redis, an LLM, or provider/tool adapters directly.

## Commands

| Command | Syntax | Class | Confirmation or side effect |
| --- | --- | --- | --- |
| `/sync` | `/sync` | Selected-page derived-index mutation | Live hierarchy selection and final `sync_confirm`; no Notion write |
| `/index-full` | `/index-full` | Full derived-index mutation | Duration/cost warning and opaque `index_full_confirm`; no Notion write |
| `/index-status` | `/index-status [workflow_id]` | Read-only | Reads persisted indexing status; never re-reads Notion |
| `/cost` | `/cost [today\|7d\|month\|workflow <workflow_id>]` | Read-only | Shows known cost, unknown cost, and budget state |
| `/pending` | `/pending` | Read-only review inbox | Lists bounded proposals; actions use explicit callbacks |
| `/workflow` | `/workflow [workflow_id]` | Read-only | Shows at most five recent summaries or one redacted detail |
| `/status` | `/status` | Read-only readiness | Shows liveness separately from dependency readiness |
| `/stats` | `/stats` | Read-only aggregates | Shows page/block/chunk/vector/proposal counts and safe timestamps |

`/start` remains an alias for help. Existing `/pages`, `/ingest`, `/ask`,
`/accept`, `/reject`, and `/retry-proposal` commands retain their ingestion,
QA, and review behavior. `/ask` accepts repeated `--page <id>` and
`--section <path>` scopes; the operator UI does not require users to type a
Notion UUID for page selection.

`/retry-proposal` applies only to an upload session whose proposal failed after
its `SourceDocument` and safe target were persisted. It atomically claims the
session and regenerates from that source id; it does not download media again,
rerun OCR or PDF extraction, or create another source record.

## Authorization

Authorization is deterministic and happens before operator work:

1. `X-Telegram-Bot-Api-Secret-Token` must match when
   `TELEGRAM_WEBHOOK_SECRET` is configured.
2. The chat id must be in `TELEGRAM_ALLOWED_CHAT_IDS` when that allowlist is
   configured.
3. Callback ownership must match the exact `(chat_id, user_id)` pair that
   created the mapping.

Rejected authorization does not create a workflow, enqueue a job, acknowledge a
callback, or send a reply.

## Callback contract

Telegram carries only `ll:<opaque_token>`. Redis or the in-memory compatibility
store maps it to an allowlisted, TTL-bound server-side record.

| Callback kind | Actions | Responsibility |
| --- | --- | --- |
| `picker` | `open_page`, `select_target`, `back`, `root` | Hierarchy navigation and target selection |
| `review` | `accept`, `reject`, `change_target` | Existing proposal review workflow |
| `operator` | `sync_toggle`, `sync_confirm`, `sync_cancel`, `index_full_confirm`, `index_full_cancel`, `pending_view` | Operator selection, confirmation, and read-only proposal view |

Mappings may contain page/workflow/proposal ids, selection state, owner, expiry,
and claim state server-side. These values are not encoded in callback data or
logs. One-shot confirmations and mutation callbacks are claimed atomically.
Duplicate or expired callbacks fail safely without repeating work.

Valid callbacks are acknowledged before long OCR, provider, indexing, or review
work. `TELEGRAM_CALLBACK_ACK_FAILED` describes acknowledgement delivery only.
Business status and preview delivery status are tracked independently.

## Command behavior

### `/sync`

The service discovers up to 100 accessible pages, renders hierarchy paths, and
allows up to 10 selected pages in a session with a 10-minute TTL. The final
confirmation calls page-scoped incremental indexing. Each page uses page-level
replacement; earlier successful pages remain committed if another page fails.

### `/index-full` and `/index-status`

`/index-full` requires an owner-bound, unexpired confirmation. With Redis it
creates a durable indexing workflow and queues the dedicated full-index job;
the response points to `/index-status <workflow_id>`. The dedicated job has a
separate timeout and no automatic RQ retry. Without Redis, the synchronous
compatibility path remains available, but the ready `local` profile reports its
queue dependency as unavailable.

`/index-status` reads persisted workflow state, counts, remaining work, safe
failure reason, stale state, and known cost. It never starts or re-runs an
index.

### `/cost` and `/workflow`

`/cost` supports today, rolling 7-day, calendar-month, and single-workflow
scopes. It separates recorded LLM/proposal/QA and embedding/indexing costs
where metadata supports them. Missing pricing remains `unknown`.

`/workflow` returns bounded, redacted status. It is not a rerun, reconciliation,
or SQL control surface.

### `/pending`

The inbox reads at most eight pending proposals and shows bounded title,
summary, source display name, and target path. View is read-only. Accept,
Reject, and Change target are explicit callbacks. Only Accept can append to
`AI Supplement Zone`, verify durable identity, and re-index. Pending and
rejected proposals remain outside production RAG.

Accept and Change Target do not share one generic locking path. Change Target
locks the Change Request in its transaction and revalidates `pending` before
changing the target. Accept performs append verification and snapshot
preparation first, then locks and revalidates the Change Request in the final
transaction that stores the page replacement and `accepted` status. Reject
re-reads and validates state in its update transaction but does not currently
request that row lock. Duplicate Telegram mutation callbacks are additionally
blocked by their one-shot server-side claim.

### Screenshot batches and PDFs

Telegram media groups are collected by owner and `media_group_id`, deduplicated
by file identity, and sorted by `message_id`. A versioned delayed settle claim
prevents an older scheduled job from finalizing a batch after another image
arrives. OCR evidence is persisted as one screenshot source and proposal
content must pass deterministic grounding validation; eligible title, summary,
or body repair is bounded and revalidated.

PDF uploads use text-layer extraction only. Scanned or image-only PDFs are not
automatically passed to screenshot OCR.

### `/status` and `/stats`

`/status` returns fixed check states for database, migration, pgvector,
provider, Notion, Redis, and the RQ scheduler. A not-ready dependency does not
make the read command itself unsafe.

`/stats` returns repository-backed aggregate counts and UTC timestamps for the
latest successful full index and manual incremental sync. It does not return
page ids, paths, titles, text, vectors, or proposal JSON.

## Queue and idempotency

When `REDIS_URL` is configured, the webhook claims the durable update ledger
and enqueues one serializable envelope on the `telegram` queue. The worker
uses the canonical module-level job import path and an embedded scheduler.
Ordinary jobs use `TELEGRAM_JOB_TIMEOUT_SECONDS`; review Accept jobs use
`TELEGRAM_REVIEW_JOB_TIMEOUT_SECONDS`; full indexing uses
`TELEGRAM_INDEXING_JOB_TIMEOUT_SECONDS`. The generic webhook worker remains
the review execution path; the enqueue boundary selects the review bound from
the typed callback mapping or `/accept` command. Reject, Change Target, and
read-only callbacks remain ordinary jobs.

RQ timeout exceptions are translated to the neutral `QUEUE_JOB_TIMEOUT`
failure reason. Accept recovery still treats the visible
`change-request-<id>` identity as the reconciliation boundary: a timeout does
not authorize a blind retry or a second Notion append.

The embedded RQ scheduler is required for delayed media-group settle jobs and
retry intervals. The initial full-index job is enqueued immediately; the
scheduler does not turn it into a scheduled job.

Duplicate non-null `update_id` deliveries replay the stored running or terminal
result. A worker crash may be retried according to the queue policy, but a
completed ledger or callback claim prevents duplicate business work. Without
Redis, local/test operation uses the synchronous compatibility path.

## Safe output

Operator messages may include operation, status, workflow reference, bounded
counts, display paths, deterministic failure reasons, and known or `unknown`
cost. They must not include callback tokens, Redis keys, raw Notion blocks,
source/OCR text, prompts, embeddings, provider exceptions, secrets, or private
metadata.
