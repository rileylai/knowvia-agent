# Architecture

## System boundaries

LearnLoop uses explicit application boundaries so policy is testable and
external integrations can be replaced without changing workflow code.

```text
FastAPI route
  -> orchestrator
      -> deterministic service / provider router / tool registry
          -> repository or external adapter
```

The route owns request validation, authentication hooks, dependency wiring, and
response mapping. Orchestrators own workflow sequencing. Services own
permission checks, validation, idempotency, cost handling, readiness, and
redaction. Repositories own database queries and transactions.

## Components

| Component | Responsibility |
| --- | --- |
| `src/app` | FastAPI routes, Pydantic schemas, dependency wiring, and request trust boundaries |
| `src/orchestrators` | Indexing, ingestion, QA, review, and Telegram workflow coordination |
| `src/services` | Deterministic business rules, limits, prompt loading, readiness, cost, and recovery helpers |
| `src/providers` | LLM and embedding interfaces, routing, response models, and OpenAI adapters |
| `src/tools` | Notion, Telegram, PDF, URL, YouTube, and OCR adapters behind tool contracts |
| `src/repositories` | PostgreSQL reads/writes for pages, blocks, chunks, proposals, workflows, and idempotency |
| `src/rag` | Block paths, chunking, embeddings input, retrieval, and citation metadata |
| `src/queue` | Backend-neutral `QueueClient` and Redis/RQ implementation |
| `src/worker` | Module-level RQ callables for queued Telegram work |
| `alembic` | Explicit PostgreSQL schema migrations |

## Integration boundaries

### Notion

`NotionReaderTool` reads pages, blocks, hierarchy, and current content. The
live backend is selected with `NOTION_BACKEND=live` and requires
`NOTION_TOKEN`. `NOTION_BACKEND=mock` is the default and reads the configured
mock data directory or bundled fixtures. `NotionWriterTool` exposes only the
append operation needed for `AI Supplement Zone`; it has no update, delete, or
move operation.

### LLM and embeddings

The provider router selects an `LLMProvider` for proposal and QA generation.
The current defaults are `openai` and `gpt-4o-mini`. The embedding adapter
uses OpenAI `text-embedding-3-small` with explicit 1536 dimensions for
indexing and query embeddings.

Index-time embedding execution is owned by `EmbeddingBatchService`. It plans
stable contiguous batches, runs sequentially, validates response ordering and
dimensions, retries only retryable failures, and returns no partial result.
The page indexer opens its replacement transaction only after the complete
prepared snapshot is valid. See [ADR-0006](decisions/0006-bounded-embedding-execution-contract.md).

### PostgreSQL and pgvector

PostgreSQL stores durable workflow state and the derived Notion snapshot.
`knowledge_chunks.embedding` is a nullable `vector(1536)` column. Repository
queries apply source, page, and section filters before cosine top-k selection.
The repository owns vector ordering; the retriever owns fallback and citation
assembly. Alembic is the only supported schema migration path.

### Redis and RQ

Redis is optional for synchronous compatibility but required for the ready
queued Telegram runtime. The webhook claims a durable update record, serializes
the update through `QueueClient`, and returns before long-running work. The
worker consumes the `telegram` queue with the embedded scheduler enabled. The
scheduler promotes delayed media-group settle jobs and retry intervals; the
initial full-index job is enqueued immediately rather than scheduled.

Ordinary Telegram jobs use `TELEGRAM_JOB_TIMEOUT_SECONDS` (default `180`).
Review Accept work uses the dedicated `TELEGRAM_REVIEW_JOB_TIMEOUT_SECONDS`
(default `10800`) when the enqueue boundary can deterministically identify an
Accept command or typed review Accept callback. Review Reject, Change Target,
read-only callbacks, and other ordinary work remain on the ordinary bound.
Full indexing uses a dedicated job and
`TELEGRAM_INDEXING_JOB_TIMEOUT_SECONDS` (default `10800`). The latter is a
configurable execution bound, not a latency promise. The dedicated full-index
job has no automatic RQ retry; progress is represented by its persisted
workflow id. See [ADR-0007](decisions/0007-telegram-full-index-queue-reliability.md).

## Trust and write boundaries

Business rules are deterministic backend decisions:

- API bearer authentication and Telegram webhook/chat authorization.
- Target page resolution and `AI Supplement Zone` enforcement.
- Provider-output schema validation and source/target ownership.
- Production-RAG inclusion and exclusion.
- State transitions, row locks, idempotency claims, and retry policy.
- Error classification, safe metadata, and redacted operator output.

Prompt delimiters protect against instructions in source text and retrieved
context, but they are not authorization. An LLM cannot grant itself a tool,
Notion, target-page, or write permission.

## Failure and transaction model

Notion and PostgreSQL are not one distributed transaction. The accepted-append
workflow therefore verifies the durable Notion identity before committing the
accepted state and re-indexed page snapshot. If the result is uncertain,
recovery reads the identity before retrying. A final workflow audit update is
separate from business work; an audit failure does not roll back or rerun a
committed operation.

Page replacement is atomic per page. Incremental sync commits each page
independently so earlier successful pages remain durable when a later page
fails.

### Same-page indexing concurrency

Snapshot preparation, including embeddings, happens outside the database
transaction. During persistence, `NotionPageRepository` acquires a PostgreSQL
transaction-scoped advisory lock keyed by the external Notion page id:

```sql
pg_advisory_xact_lock(hashtextextended(:notion_page_id, 0))
```

After the lock is acquired, the repository reloads the persisted page. When
both snapshots have `last_edited_time`, an incoming value older than the
persisted value fails with `STALE_PAGE_SNAPSHOT` before any page-derived rows
are deleted. The page row, blocks, chunks, and vectors are then replaced in
one unit-of-work transaction, so readers do not observe a half-replaced page.
SQLite test/demo stores do not execute the PostgreSQL advisory lock.

### Review concurrency

Review mutations do not all use the same locking mechanism:

- Change Target opens a unit-of-work transaction, locks the Change Request with
  `SELECT ... FOR UPDATE`, and revalidates that it is still `pending` before
  changing the target row identity.
- Accept performs the Notion append and prepares the new page snapshot first.
  Its final unit-of-work transaction locks the Change Request, revalidates
  `pending`, persists the prepared page snapshot, and marks the request
  `accepted` together.
- Reject and Edit Later re-read and validate the row inside their update
  transaction but currently do not request a row lock. Transport idempotency
  and one-shot Telegram callback claims reduce duplicate delivery, but they are
  not a universal database serialization guarantee.

A stale final state is rejected with `INVALID_STATE_TRANSITION`. The visible
Notion identity remains the reconciliation boundary if an Accept append
succeeded before a competing final database transition was rejected. Because
the Accept row lock occurs after the external append, it is not a distributed
mutex around Notion; callers should use transport idempotency, and an uncertain
or competing outcome must be reconciled by identity. Concurrent Reject/Edit
Later requests that both read `pending` are likewise not fully serialized by
the current repository implementation.

## Extension points

Provider interfaces allow additional LLM or embedding adapters. Tool contracts
are schema-friendly and can later be exposed through MCP, but the MVP does not
run a standalone MCP server. Queue, repository, and policy interfaces are
similarly kept independent of their current infrastructure implementations.
