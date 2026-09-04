# State Ownership and Synchronization

## Persistence layers

LearnLoop separates authoritative Notion content, a rebuildable search index,
durable application records, and ephemeral queue/session coordination.

| Layer | Current state | Recovery authority |
| --- | --- | --- |
| Notion | Page content, manual edits, and accepted supplement blocks | Source of truth for page content |
| PostgreSQL derived index | `notion_pages`, `notion_blocks`, and Notion-derived `knowledge_chunks`, including embeddings | Re-read Notion and index again |
| PostgreSQL durable application state | `source_documents`, `change_requests`, `workflow_runs`, `telegram_update_ledger`, and `api_idempotency_records` | PostgreSQL backup and restore |
| Redis/RQ | Queue jobs, upload aggregation, callback mappings, and short-lived operator sessions | Ephemeral coordination; reconstruct from durable state or restart the interaction |

`knowledge_chunks` can also refer to a persisted source document. Those rows
are derived from the source document and are excluded from production RAG;
production retrieval accepts only `source_kind=notion` through the repository
eligibility filter.

The schema also contains `audit_logs`, but the current runtime has no writer
for that table and does not rely on it as an audit trail.

## Derived index

A Notion page snapshot contains its canonical page identity, hierarchy path,
`last_edited_time`, complete block tree, ordered chunks, citation paths, and
optional vectors. Page indexing prepares all parsing, chunking, and embeddings
before opening the replacement transaction.

Within that transaction, the complete page-derived rows are replaced together.
This state can be reconstructed from current Notion content. A full index is
workspace-partial by design: every completed page is durable, while a failed
page retains its previous complete snapshot and stops later page processing.

## Durable application state

Notion cannot reconstruct the original extracted source, rejected or pending
proposal, workflow outcome, Telegram replay result, or HTTP idempotency
response. These records require PostgreSQL backup and restore:

- `source_documents` stores normalized source text and its content hash;
- `change_requests` stores proposal content, target row identity, and review
  status;
- `workflow_runs` stores status, failure reason, timestamps, and safe metadata;
- `telegram_update_ledger` prevents a delivered Telegram `update_id` from
  repeating work and stores a bounded terminal or running result;
- `api_idempotency_records` stores request fingerprints and replayable mutation
  responses for optional `Idempotency-Key` use.

Notion is therefore the source of truth for authored page content, not for all
application state. Re-indexing Notion is not a substitute for a PostgreSQL
backup.

## Synchronization

Manual Notion changes are reconciled explicitly:

```text
Manual Notion edit
  -> full or page-scoped incremental sync
  -> read the current page
  -> prepare a complete replacement snapshot
  -> atomically replace that page's derived index
```

There is no background Notion watcher. Accepted agent appends use the same
page indexer immediately after the writer verifies the visible
`change-request-<id>` identity.

## Recovery boundaries

After restoring PostgreSQL, compare durable application records with Notion
before resuming review mutations, then rebuild the derived index from Notion.
If an append outcome is uncertain, search the target page for the visible
change-request identity before allowing another append. If identity visibility
cannot be established, stop rather than guessing.

See [Architecture](01-architecture.md), [Workflows](02-workflows.md),
[incident recovery](runbooks/incident-recovery.md), and
[backup and restore](runbooks/backup-restore.md).
