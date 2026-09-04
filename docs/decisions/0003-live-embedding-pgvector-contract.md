# ADR-0003: Live Embedding and pgvector Retrieval Contract

## Status

Accepted

## Decision

- Use OpenAI `text-embedding-3-small` for the initial vector path.
- Send `dimensions=1536` explicitly for chunk and query embeddings.
- Store nullable `vector(1536)` values in `knowledge_chunks`; the serialized
  embedding field remains transitional for compatibility.
- Use cosine distance for semantic retrieval.
- Apply production, page, and section filters before top-k selection.
- Let the repository own PostgreSQL vector ordering and let the retriever own
  fallback behavior and citation assembly.
- Use deterministic lexical retrieval over the same safe scope when query
  embedding, vector data, or the vector query is unavailable.
- Record retrieval mode and safe fallback reason in QA workflow metadata.
- Fail closed during indexing if complete embeddings cannot be prepared; do not
  create a partial page vector snapshot.
- Do not add a reranker, hybrid fusion, or per-route embedding model selection
  to the MVP.

## Rationale

The same model and dimension for indexing and query avoids incompatible vector
spaces. Filter-before-top-k preserves page and production boundaries. Exact
filtered cosine search is the correctness baseline; an existing HNSW migration
index may accelerate PostgreSQL access without changing the contract.

## Consequences

QA remains useful when vector retrieval is unavailable because lexical fallback
is explicit and deterministic. A page cannot be partially updated with missing
vectors. A future retrieval change requires a separate decision and regression
evidence.
