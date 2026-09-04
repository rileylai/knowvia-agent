# Workflows

This document describes the implemented business workflows. Routes only
validate transport requests and delegate to orchestrators.

## Notion indexing

### Full index

```text
Create indexing workflow
  -> discover accessible Notion pages
  -> read each page and its block tree
  -> build citation paths and chunks
  -> plan and execute bounded embeddings
  -> replace that page's derived snapshot atomically
  -> record counts and outcome
```

Pages are processed sequentially. If one page fails, previously committed pages
remain committed, the failed page keeps its previous complete snapshot, and
later pages are not attempted. With Redis, Telegram `/index-full` queues a
dedicated job and returns the workflow id for `/index-status`.

### Page index and manual sync

`POST /api/notion/index/page` indexes one external Notion page. `POST
/api/notion/index/incremental` accepts one or more known changed page ids and
reconciles each from current Notion content. Each page uses the same page
indexer, embedding service, and replacement transaction. Manual Notion changes
are not detected automatically.

Preparation occurs before persistence. PostgreSQL persistence serializes
writers for the same external page id with
`pg_advisory_xact_lock(hashtextextended(page_id, 0))`. Once the lock is held,
an older prepared `last_edited_time` is rejected as `STALE_PAGE_SNAPSHOT`
before the current blocks, chunks, or vectors are replaced. A complete newer
snapshot is committed in one transaction.

## Source ingestion

The supported source paths are:

```text
PDF / URL / YouTube transcript / screenshot batch / chat text
  -> validate size and source constraints
  -> parse and normalize
  -> persist SourceDocument and content hash
  -> optionally generate a proposal
```

The ingestion API stores a source document. The proposal API then runs the
duplicate check and provider path. Telegram combines upload, parsing, source
persistence, proposal generation, and preview delivery through the same
orchestrators.

Resource limits are enforced before expensive work and revalidated by adapters:

| Resource | Limit |
| --- | ---: |
| PDF upload | 10 MiB |
| PDF pages | 100 |
| Extracted text | 200,000 characters |
| OCR images per request | 10 |
| Each OCR image | 5 MiB |
| OCR batch | 20 MiB |
| Image pixels | 40,000,000 |
| Pasted chat text | 10,000 characters |

URL ingestion accepts public HTTP(S) article URLs, validates redirect targets,
blocks private/local addresses, limits response size, and accepts only text
content. YouTube ingestion requires a supported video URL and an available
transcript. Screenshot OCR requires Tesseract languages `eng`, `chi_tra`, and
`chi_sim`. PDF ingestion uses `pypdf` to read the PDF text layer. A scanned or
image-only PDF fails when it has no extractable text; it is not automatically
routed through the screenshot OCR pipeline.

### Screenshot and media-group ingestion

Screenshot OCR is normalized into one persisted `SourceDocument`. Proposal
validation builds a deterministic source snapshot from that OCR text and
checks generated title, summary, concepts, and notes against source evidence.
Only diagnosed grounding failures are repair-eligible. Title-only, summary,
and bounded body repair paths each make at most one provider attempt, and a
failed title repair may use a deterministic source-derived fallback where the
validator permits it. Every repaired or fallback proposal passes through the
same schema, canonical-target, and source-grounding validation before it can be
persisted.

Telegram groups photos by the owner-bound upload session and
`media_group_id`, deduplicates attachments by Telegram file identity, and
sorts the final batch by `message_id` with file identity as a stable tie-break.
Each arrival advances a settle version. A delayed RQ settle job can promote the
batch only when its expected version is still current, so an older job cannot
close a batch that received another image. Atomic settle, target, proposal,
retry, and preview claims prevent the same session phase from running twice.

`/retry-proposal` is available only for a failed proposal with a persisted
source and safe target. It atomically claims the retry and calls proposal
generation with the existing `source_document_id`; it does not download the
attachments again, rerun PDF extraction or screenshot OCR, or create another
source document.

## Proposal generation and review

```text
SourceDocument
  -> duplicate check against production-eligible Notion chunks
      -> duplicate found:
           build a citation-first proposal without provider generation
      -> sufficiently novel:
           generate and validate proposal content through the provider
           check the validated proposal content for an eligible duplicate
           replace a late duplicate with the citation-first proposal
  -> merge backend-owned source and canonical target identity
  -> validate
  -> persist Change Request(status=pending)
```

The duplicate checker reads repository candidates with `source_kind=notion`;
source-document chunks are excluded, and known synthetic page ids are excluded
from PostgreSQL production queries. It detects normalized exact hashes or a
configured text-similarity threshold. The early duplicate branch reuses the
matched Notion path as a grounded citation and avoids unnecessary provider
cost. The late check prevents newly generated wording from duplicating indexed
knowledge.

The provider never owns source identity, target path, internal page identity,
citations, permissions, or attachment count. The backend derives those fields
from the persisted source and indexed target page and validates the merged
proposal. Invalid output fails with `LLM_OUTPUT_INVALID`. Proposal generation
does not write to Notion.

Review transitions are:

```text
pending -> accepted
pending -> rejected
pending -> pending  (edit later)
```

Reject and Edit Later do not call Notion. Edit Later retains `pending`. Both
re-read and validate current state within their update transaction, but unlike
Change Target and the final Accept commit, they do not currently acquire a
Change Request row lock. The pending query surface is read-only and provides
proposal content, source display information, citations, and the current target
page.

## Accept, append, and re-index

```text
Load pending Change Request
  -> validate target and proposal
  -> append under AI Supplement Zone
  -> verify change-request-<id> is visible
  -> prepare complete target-page snapshot
  -> begin final database transaction
  -> lock Change Request and revalidate pending state
  -> replace page blocks/chunks/vectors and mark accepted in one DB transaction
```

The Notion append is append-only and carries a durable identity. A retry first
checks that identity, so a successfully committed Notion append is not
duplicated. If append verification or re-indexing fails, the change request
remains pending for safe recovery. Only the successful accept path makes the
new content eligible for production QA.

## Grounded QA

```text
Validate question and scope
  -> create query embedding when configured
  -> retrieve eligible Notion chunks
  -> use lexical fallback when vector retrieval is unavailable
  -> render untrusted context for the LLM
  -> return answer and backend-owned Notion path citations
```

QA supports page and section scopes and a bounded `top_k`. It never retrieves
pending or rejected proposals. If no supporting chunks are available, the
response marks `insufficient_info` rather than inventing an answer.

## Telegram processing

With Redis:

```text
Webhook
  -> validate secret and allowed chat
  -> claim update idempotency record
  -> enqueue serializable envelope on telegram queue
  -> return 202
  -> worker runs the gateway and persists terminal outcome
```

The generic webhook callable remains the execution path for review callbacks;
the enqueue boundary selects `TELEGRAM_REVIEW_JOB_TIMEOUT_SECONDS` only for a
deterministically identified Accept action. Reject, Change Target, read-only
callbacks, and ordinary commands use `TELEGRAM_JOB_TIMEOUT_SECONDS`. RQ timeout
signals are translated to `QUEUE_JOB_TIMEOUT` before review error handling can
rewrite them as an application or Notion failure.

Without Redis, a synchronous compatibility path is retained for local/test
operation, but the ready `local` profile reports the queue dependency as
unavailable. Duplicate Telegram `update_id` values replay a running or terminal
outcome and do not repeat OCR, LLM calls, appends, or replies. Callback data is
opaque, TTL-bound, owner-bound, and atomically claimed.

Callback acknowledgement, business completion, and preview delivery are
tracked independently. Acknowledgement failure does not undo committed
business work. A post-commit preview failure keeps the pending proposal and is
recoverable by resending the existing preview.

See the [Telegram operator contract](11-telegram-operator-contract.md) for
operator commands and callback classes.

## Reliability properties

- Notion reads and embedding batches use bounded retry for retryable transport,
  timeout, rate-limit, and allowlisted upstream server failures.
- Invalid requests, authentication failures, invalid provider responses, and
  dimension/count mismatches fail without retry.
- Same-page indexing uses a transaction-scoped advisory lock and stale-snapshot
  comparison before atomic replacement.
- Change Target and the final Accept database commit use a Change Request row
  lock. Reject and Edit Later currently rely on transaction-local revalidation
  rather than that lock.
- API mutations support optional `Idempotency-Key` replay; Telegram uses its
  update ledger instead.
- Workflow audit failure is reconciled separately and never reruns business
  work.

For operational recovery, see [incident recovery](runbooks/incident-recovery.md)
and [backup and restore](runbooks/backup-restore.md).
