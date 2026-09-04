# ADR-0007: Dedicated Queue Contract for Telegram Full Indexing

## Status

Accepted

## Context

Full indexing can take substantially longer than ordinary Telegram processing.
A shared implicit queue timeout can terminate a full index while a large page
is still being read and can misclassify infrastructure timeout as a Notion
transport failure.

## Decision

`QueueClient.enqueue()` and `enqueue_in()` accept an optional backend-neutral
`timeout_seconds`. RQ maps it to `job_timeout`. Ordinary Telegram work uses
`TELEGRAM_JOB_TIMEOUT_SECONDS` (default `180`); the dedicated full-index job
uses `TELEGRAM_INDEXING_JOB_TIMEOUT_SECONDS` (default `10800`). These are
execution safety bounds, not latency SLAs, and no global long queue timeout is
configured.

The generic Telegram worker claims the confirmation and creates a durable
indexing workflow, then queues the module-level full-index callable with that
workflow id. The dedicated job has no automatic RQ retry. Without Redis, the
synchronous compatibility path remains available.

RQ-specific timeout exceptions are translated at the queue boundary into a
neutral infrastructure timeout. The indexing workflow records the safe reason
`QUEUE_JOB_TIMEOUT`; Notion layers do not import RQ or misclassify the failure.

## Consequences

Ordinary Telegram jobs remain bounded while full-index progress is durable and
visible through `/index-status`. Full-index execution still requires an
operator confirmation and explicit readiness of Redis/RQ.
