# LearnLoop Agent Documentation

LearnLoop Agent is a local-first Notion knowledge agent. It indexes existing
Notion pages as read-only knowledge, turns learning material into reviewable
supplement proposals, and appends accepted content only to `AI Supplement
Zone`.

This directory documents the current implementation. Development logs and
roadmap records live under `dev_state/` and are intentionally not part of the
public documentation set.

## Start here

- [Design overview](00-design-doc.md): product boundary, data ownership, and
  core invariants.
- [Architecture](01-architecture.md): application layers, integrations, and
  persistence boundaries.
- [Workflows](02-workflows.md): indexing, ingestion, review, append, QA, and
  Telegram execution.
- [Guardrails](03-guardrails.md): write policy, RAG eligibility, security, and
  failure behavior.
- [RAG design](05-rag-design.md): chunking, embeddings, retrieval, fallback,
  and citations.
- [API contract](09-api-contract.md): HTTP routes, authentication, and
  idempotency.
- [Deployment](10-deployment.md): local setup, configuration, startup, and
  readiness checks.

## Reference documentation

| Document | Use it for |
| --- | --- |
| [State ownership and sync](04-memory-design.md) | Rebuildable index, durable application records, reconciliation, and recovery authority |
| [Notion permissions](06-notion-permission-model.md) | Ownership rules and `AI Supplement Zone` append semantics |
| [Evaluation plan](07-evaluation-plan.md) | Deterministic tests, evaluation commands, and opt-in live checks |
| [Observability](08-observability.md) | Workflow metadata, metrics, redaction, and recovery signals |
| [Telegram operator contract](11-telegram-operator-contract.md) | `/sync`, `/index-full`, review, cost, status, and statistics commands |
| [Contributing](../CONTRIBUTING.md) | Architecture boundaries, tests, documentation, and review expectations |

Operational procedures are in [`runbooks/`](runbooks/). Accepted architectural
decisions are in [`decisions/`](decisions/). Runtime prompt templates are in
[`prompts/`](prompts/); only the files named by the prompt loader are active.

## Current scope

The MVP runs locally and uses Telegram as its first operator channel. It
supports PDF, URL, YouTube transcript, screenshot OCR, and pasted chat-text
ingestion; Notion indexing and manual sync; grounded QA; human-reviewed
supplements; and append-only Notion writes. Cloud deployment, continuous
Notion synchronization, direct original-note editing, reranking, and
LLM-as-judge are outside the current scope.
