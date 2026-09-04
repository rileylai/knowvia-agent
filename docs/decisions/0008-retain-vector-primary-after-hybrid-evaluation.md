# ADR-0008: Retain Vector-Primary Retrieval After Hybrid Evaluation

## Status

Accepted

## Context

ADR-0003 makes successful vector retrieval and deterministic lexical fallback
mutually exclusive. Step 99 evaluated whether a continuously fused vector and
keyword path justified changing that contract. The evaluation was offline and
used only the frozen public-safe Step 98 body-only corpus and captured vectors.
It did not read Notion, request embeddings, or change production retrieval.

## Alternatives

1. Keep exact-cosine `vector_only` as the primary path and use the existing
   lexical scorer only when vector retrieval fails or is unavailable.
2. Use the current deterministic lexical scorer as `keyword_only`.
3. Fuse vector and keyword rankings with weighted reciprocal rank fusion.

Keyword-only was a comparison arm, not an adoption candidate. The preregistered
fusion used RRF constant `60`, candidate depth `20` from each ranking, and fixed
candidate weights `(0.50, 0.50)`, `(0.65, 0.35)`, and `(0.80, 0.20)`.
The frozen contract explicitly made weighted RRF the only possible Step 100
candidate. Keyword-only therefore had no preregistered authority to replace
production retrieval, even if its aggregate metrics were highest.

## Evidence

The formal experiment was `step99-exp-003`, manifest digest
`06dccd42319c7f9513d6c595343fe44e4dd83fe881c0e80b3c6e70257fc5e73d`.
It reused 18 pages, 108 exact body-only chunks, 72 query vectors, and 108 body
vectors from the complete Step 98 capture. Eighteen queries selected the fixed
weight without decision-set feedback; 54 separate queries were used for the
decision.

The selected weight was vector `0.65`, keyword `0.35`. Decision-set results
were:

| Variant | Hit@1 | Hit@3 | Hit@5 | MRR@5 | nDCG@5 | RR sum |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `vector_only` | 0.129630 | 0.518519 | 0.796296 | 0.346605 | 0.377947 | 18.716667 |
| `keyword_only` | 0.166667 | 0.666667 | 0.962963 | 0.435185 | 0.467728 | 23.500000 |
| `weighted_rrf` | 0.166667 | 0.592593 | 0.870370 | 0.395988 | 0.423849 | 21.383333 |

Weighted RRF produced four Hit@3 gains and zero Hit@3 losses relative to
vector-only. Its reciprocal-rank sum improved by `2.666666666666`, below the
preregistered `2.700` minimum. Eight query ranks improved, 43 were unchanged,
and three worsened. Citation recall and precision improved over vector-only,
independent citation and golden citation checks were exact `1.000`, repository
safety passed, the isolated pgvector adapter gate passed, and a separate
same-contract replay reproduced result digest
`6a08aab34455d21e0c361e1a6007a4f58047ae9f803c3f7a4e555595007f7bdf`.
In this evidence, an invalid-to-qrel path means a retrieved path absent from the
annotated qrels for that query. It does not mean the path was structurally
invalid, unsafe, or fabricated. Independent and golden citation conformance
each recorded zero invalid citations under their own exact contracts.

Two earlier experiment ids ended before tuning or decision scoring. Exp-001
found a disposable fixture seed-order defect. Exp-002 corrected that defect but
found an incompatible source-vector digest algorithm. Both were preserved as
pre-scoring aborts; neither influenced weight selection or the formal result.

## Decision

Retain the ADR-0003 production contract:

- exact body-only document embeddings remain unchanged;
- query-embedding-first retrieval and repository-owned exact filtered cosine
  ranking remain the primary retrieval path;
- deterministic lexical retrieval remains a fallback only when the vector
  query fails or eligible vectors are unavailable;
- do not introduce weighted RRF or keyword-only retrieval into production.

The canonical Step 99 decision is `maintain_vector_primary`. Step 100 is not
authorized by this ADR.

## Consequences

Production behavior, schema, indexing, migrations, citations, fallback,
observability, and rollout configuration remain unchanged. The better
keyword-only aggregate scores are informative, but keyword-only was not an
adoption candidate and its ASCII tokenization has known multilingual limits.
The result supports the value of retaining lexical fallback; it does not
support promoting the current lexical scorer to keyword-primary retrieval.

A future hybrid proposal requires a new experiment identity and preregistered
contract. It must use an independent or expanded body-only evaluation set,
preserve filter-before-top-k and citation safety, meet quality gates without
post-hoc tuning, provide realistic latency and resource evidence, and receive a
new rollout decision before production work begins.

## Limitations

- The dataset is public-safe synthetic and was inherited from Step 98, so it is
  not a human-blind holdout.
- The lexical scorer intentionally mirrors the current ASCII-token production
  fallback; it is not a general multilingual keyword implementation.
- The experiment contains no production-scale latency, resource, or traffic
  evidence for keyword-only or weighted-RRF retrieval.
- Vector ranking used complete captured vectors offline. Disposable PostgreSQL
  verified repository filtering and adapter correctness, not production scale.
- Computational overhead is expressed as deterministic operation counts, not
  wall-clock production latency.
