# Notion Permission Model

## Source of truth

Notion is authoritative for page content, block content, hierarchy, and the
visible result of an accepted append. PostgreSQL stores both a rebuildable
indexed snapshot and durable application records such as sources, proposals,
workflows, and idempotency ledgers. Redis stores short-lived coordination
state. See [State ownership and synchronization](04-memory-design.md).

## Ownership matrix

| Notion content | Read by agent | Edited by agent |
| --- | --- | --- |
| Existing original page content | Yes | No |
| Manually created notes and blocks | Yes | No |
| Existing AI supplement blocks | Yes | No |
| New accepted supplement | Yes | Append-only under `AI Supplement Zone` |

The agent never overwrites, deletes, moves, or merges existing blocks. There is
no per-page writable original-note mode in the MVP. A user may manually merge
or delete content in Notion; the resulting state is reconciled by explicit
sync.

## `AI Supplement Zone`

Accepted supplements use the following structure:

```text
Target page
└── AI Supplement Zone
    └── YYYY-MM-DD
        └── Topic title
            - Source: ...
            - Summary: ...
            - Key Concepts: ...
            - Notes:
                - ...
            - LearnLoop Change Request: change-request-<id>
```

The backend builds source and target data from persisted records. The provider
generates only title, summary, concepts, and notes. Source display names are
rendered by source type and are never parsed back into source identity.

The visible change-request identity is durable across client instances. The
writer reads the target page before appending and verifies the identity after
the append. A retry with an existing identity is an idempotent replay.

## Write contract

The only agent write is `append_ai_supplement_zone` through
`NotionWriterTool`. It accepts a page identity, change-request identity, and
validated generated content. It rejects targets outside the canonical
`<page path>/AI Supplement Zone` path and has no arbitrary Notion mutation
method.

The accept workflow commits the PostgreSQL `accepted` state only after the
append is visible and the target page has been re-indexed. Notion append and
PostgreSQL commit are not a distributed transaction; recovery must inspect the
durable identity before retrying.

## Manual reconciliation

Manual Notion edits are not watched continuously. Use:

- `POST /api/notion/index/incremental` when the affected page ids are known;
- `POST /api/notion/index/full` when the affected page set is unknown or a
  complete rebuild is required.

The indexer replaces the affected page's derived blocks, chunks, and vectors
from current Notion content. Pending and rejected change requests remain out
of production RAG.

See [Guardrails](03-guardrails.md), [Workflows](02-workflows.md), and the
[incident recovery runbook](runbooks/incident-recovery.md).
