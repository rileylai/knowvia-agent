# RAG Design

## Retrieval pipeline

```text
Notion page tree
  -> deterministic block paths
  -> section-aware chunks
  -> OpenAI text-embedding-3-small, 1536 dimensions
  -> PostgreSQL / pgvector

Question
  -> query embedding
  -> production-scope filters
  -> cosine top-k or lexical fallback
  -> grounded answer with Notion path citations
```

The production retriever uses `source_kind=notion` by default. Page ids,
section path prefixes, and a bounded `top_k` can further restrict the search.
Pending, rejected, synthetic, and non-Notion rows are excluded before ranking.

## Block paths and chunks

`src/rag/block_path_builder.py` derives citation paths from the page path and
the nested block tree. Non-empty block text becomes a path segment; empty
blocks inherit their parent path. Child-page references remain in the parent
tree but their own pages are indexed separately.

`src/rag/chunker.py` starts a chunk at `heading_1`, `heading_2`, `heading_3`,
`toggle`, and `child_page` boundaries. It normalizes whitespace, skips empty
lines, preserves block order, and splits oversized sections at the configured
character limit. Each chunk stores:

- ordered `chunk_index`;
- `chunk_text`;
- `notion_path`;
- page title/path and page id in citation metadata;
- the contributing Notion block ids.

## Embedding contract

Indexing and query embeddings use the same OpenAI model and explicit
`dimensions=1536`. The indexing path plans stable contiguous batches with
limits on input count, individual input size, and aggregate size. The current
default operational limits are 512 inputs, 32,768 bytes per input, 8,000
estimated tokens per input, 1,000,000 aggregate bytes, and 250,000 aggregate
estimated tokens.

The estimator is an operational safety bound, not provider billing usage.
Provider-reported usage is used for cost when all successful batches provide
complete usage. A retry or missing usage makes the page-level usage/cost
unknown rather than undercounting it.

The batch service validates provider/model, response count, batch-local index
ordering, and vector dimensions. It retains results in memory until the full
page is valid; no partial vectors are persisted.

## Vector retrieval and fallback

When PostgreSQL and pgvector are available and a query embedding succeeds, the
repository applies all filters before exact cosine top-k ordering. Results use
the mode `pgvector_exact_cosine`.

If query embedding, vector data, or the vector query is unavailable, the
retriever uses deterministic lexical ranking over the same filtered scope. It
records `lexical_fallback` and a reason such as
`EMBEDDING_PROVIDER_NOT_CONFIGURED`, `EMBEDDING_PROVIDER_ERROR`,
`VECTOR_QUERY_FAILED`, `VECTOR_DIMENSION_MISMATCH`, or
`VECTOR_DATA_UNAVAILABLE`. Fallback is a safe QA result, not a permission to
include ineligible rows.

No reranker, hybrid fusion, parent-child retrieval, or LLM-as-judge is part of
the MVP.

## Citations and answer safety

Citations are built from the retrieved chunk metadata, not from model output.
Duplicate paths are collapsed deterministically. The answer prompt receives
the user query and retrieved text as untrusted data; instructions inside those
fields cannot change retrieval scope, citations, tools, or write policy.

When retrieval returns no supporting chunks, QA returns an explicit
`insufficient_info` result. The model must not invent facts or citations.

## Re-indexing and consistency

Manual sync and accepted supplement append both call the page indexer. The new
page snapshot is fully read, chunked, and embedded before page-level database
replacement. A failure leaves the previous complete page state unchanged.

For design rationale, see [ADR-0003](decisions/0003-live-embedding-pgvector-contract.md)
and [ADR-0006](decisions/0006-bounded-embedding-execution-contract.md). Quality
checks are described in the [evaluation plan](07-evaluation-plan.md).
