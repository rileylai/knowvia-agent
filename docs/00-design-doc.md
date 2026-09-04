# LearnLoop Agent Design Overview

## Purpose

LearnLoop Agent turns external learning material into durable, reviewable
knowledge in Notion. It reads existing Notion pages without editing them,
creates AI supplement proposals, waits for a human decision, and appends only
accepted content to the page's `AI Supplement Zone`.

The knowledge lifecycle is:

```text
Learning source
  -> extraction and normalization
  -> grounded proposal
  -> human review
  -> append-only Notion update
  -> target-page re-index
  -> grounded QA
```

The backend, not the LLM, owns identity, permissions, validation, state
transitions, retrieval eligibility, citations, and write safety.

## Product scope

Implemented source types are PDF, article URL, YouTube transcript, screenshot
OCR, and pasted chat text. The service exposes HTTP APIs and a Telegram
webhook. Telegram supports ingestion, grounded `/ask`, review actions, page
selection, indexing operations, cost/workflow inspection, readiness, and
knowledge statistics.

The MVP is local-only. PostgreSQL with pgvector stores both durable application
records and the derived knowledge index. In the ready `local` profile, Redis
and RQ are required for queued Telegram processing and delayed media-group
settling. A synchronous compatibility path remains available without Redis for
test, demo, and constrained local use, but the `local` readiness check then
fails its queue dependency. Notion remains the source of truth for page
content. The service does not continuously synchronize Notion.

## Ownership model

Notion has three relevant content classes:

| Content | Agent behavior |
| --- | --- |
| Original or manually created page content | Read-only |
| Existing AI supplement blocks | Read-only; never rewritten or deleted |
| New accepted supplement | Append-only under `AI Supplement Zone` |

Every AI write follows:

```text
Change Request -> Human Accept -> Append to AI Supplement Zone
```

The append includes a visible `change-request-<id>` identity. Notion and
PostgreSQL do not share a transaction, so the writer searches for that identity
before appending and verifies it again afterward. If Notion accepted an append
but database workflow completion failed, a retry reconciles the visible
identity before deciding whether another append is allowed. Accepted content
is re-indexed before it becomes eligible for production QA.

See [Notion permissions](06-notion-permission-model.md) and
[Guardrails](03-guardrails.md) for the complete policy.

## Synchronization model

There are two sources of derived-state change:

1. A manual Notion edit is reconciled by an explicit full or page-scoped
   incremental sync.
2. An accepted agent append triggers target-page re-indexing in the accept
   workflow.

Page indexing prepares the complete current page snapshot, including blocks,
chunks, and vectors, before opening the replacement transaction. If preparation
fails, the previous page snapshot remains intact. A full index processes pages
sequentially; pages committed before a failed page remain available.

## Architecture summary

```mermaid
flowchart LR
    Client["HTTP or Telegram"] --> Route["FastAPI route"]
    Route --> Orchestrator["Application orchestrator"]
    Orchestrator --> Policy["Deterministic services"]
    Orchestrator --> Provider["Provider router"]
    Orchestrator --> Tools["Tool registry"]
    Orchestrator --> Repository["Repositories / unit of work"]
    Provider --> LLM["LLM and embedding adapters"]
    Tools --> External["Notion, Telegram, parsers"]
    Repository --> Database["PostgreSQL / pgvector"]
    Orchestrator --> Queue["QueueClient"]
    Queue --> Redis["Redis / RQ"]
    Redis --> Worker["Telegram worker"]
    Worker --> Orchestrator
```

Routes validate transport contracts and map responses. Orchestrators
coordinate workflows through repositories, tools, provider abstractions, and
queue interfaces. Routes and orchestrators do not instantiate or operate
provider SDKs, infrastructure clients, or database drivers directly. Services
enforce deterministic rules; providers isolate LLM and embedding APIs; tools
isolate Notion, Telegram, OCR, and source parsers; repositories own PostgreSQL
access; and `QueueClient` owns queue access.

## Data lifecycle

PostgreSQL contains two distinct classes of state:

- A rebuildable index: Notion page and block snapshots plus
  `knowledge_chunks`, citation metadata, and optional vectors.
- Durable application records: source documents, change requests, workflow
  runs, Telegram update-ledger entries, and API idempotency records.

Only the first class can be rebuilt by reading Notion again. The second class
requires PostgreSQL backup and restore; Notion cannot reproduce extracted
sources, proposal decisions, workflow outcomes, or replay records. See
[State ownership and synchronization](04-memory-design.md).

Production retrieval includes only eligible Notion-derived chunks. Pending and
rejected proposals are never retrieved. A successful append followed by a
successful page re-index is required before accepted knowledge is searchable.

## Architectural constraints

- Routes and orchestrators do not call provider SDKs, Notion, Redis,
  PostgreSQL, or other external APIs directly.
- LLM calls go through the provider router; external capabilities go through
  schema-friendly tools.
- PostgreSQL and Redis are infrastructure boundaries, not LLM-facing tools.
- Indexing fails closed if complete embeddings cannot be prepared.
- No direct original-note editing, always-on cloud sync, standalone MCP server,
  LangChain, LangGraph, reranker, or LLM-as-judge is part of the MVP.

See [Architecture](01-architecture.md), [Workflows](02-workflows.md), and the
[accepted decisions](decisions/) for implementation detail.
