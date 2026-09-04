# ADR-0006: Bounded Embedding Execution Contract

## Status

Accepted

## Context

Large Notion pages can contain more embedding inputs than one provider request
allows. Page replacement must remain all-or-nothing even when embedding work
is divided into multiple requests.

## Decision

### Ownership

- The page indexer owns workflow sequencing and the complete prepared snapshot.
- `EmbeddingBatchService` owns planning, sequential execution, retry,
  validation, reassembly, and aggregate usage.
- The provider adapter owns one upstream request and its capability/error
  classification.
- The transport performs one attempt.

### Planning and execution

- Provider capability profiles provide hard ceilings; settings may only narrow
  them.
- Inputs remain in original order and are never deduplicated or sorted.
- Empty inputs and per-input budget violations fail before provider access.
- Batches are contiguous and executed with concurrency fixed at `1`.
- The default operational limits are 512 inputs, 32,768 bytes per input,
  8,000 estimated tokens per input, 1,000,000 aggregate bytes, and 250,000
  aggregate estimated tokens.
- The estimator is safety metadata, not provider billing usage.

### Retry and validation

Retry only timeout, transport, HTTP 408, HTTP 429, and allowlisted 500/502/503/
504 failures. Retry only the current batch with bounded backoff and a bounded
`Retry-After`. Do not retry deterministic 4xx errors, authentication errors,
invalid responses, count mismatches, or vector-dimension mismatches.

Validate provider/model, count, batch-local indices, original order, and
1536-dimensional vectors. Keep all results in memory until every input has one
valid vector.

### Consistency and full index

No repository replacement starts before complete page preparation succeeds.
Failure leaves the previous page, block, chunk, and vector rows unchanged.
Full index remains page-scoped: successful earlier pages stay committed while
the failed page and later pages retain their existing behavior.

### Usage and cost

Use provider-reported input usage only when every successful batch supplies
complete usage and no retry makes consumption unknowable. Otherwise cost is
unknown rather than undercounted.

## Consequences

Large pages can exceed a single provider request limit without weakening page
atomicity. Sequential execution is intentionally conservative; any future
concurrency or derived embedding-input persistence needs a separate decision.
