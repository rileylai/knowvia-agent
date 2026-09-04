# Contributing

LearnLoop Agent protects manually authored Notion content and requires human
review before every AI append. Changes must preserve those boundaries as well
as the behavior they implement.

## Architecture boundaries

- Keep the flow `route -> orchestrator -> service/tool -> repository or
  external adapter`.
- Routes validate transport contracts and map responses; business policy stays
  in deterministic services and orchestrators.
- Use the provider router for LLM calls, the tool registry for external
  capabilities, repositories and units of work for PostgreSQL, and
  `QueueClient` for Redis/RQ.
- Do not give an LLM authority over targets, permissions, state transitions,
  citations, retrieval eligibility, or writes.
- Preserve append-only writes under `AI Supplement Zone`. Do not add update,
  delete, move, or original-note write paths without an accepted architectural
  decision.

## Code and tests

- Use typed, explicit Python with small functions, FastAPI request/response
  schemas, and stable `error_code` and `failure_reason` values.
- Keep provider, Notion, Telegram, parser, database, Redis, and RQ clients
  behind their interfaces.
- Cover success and fail-closed behavior, especially authorization,
  idempotency, concurrency, redaction, production-RAG eligibility, and
  recovery boundaries.
- The default test suite must use fixtures, injected clients, or isolated
  databases. Live checks must be opt-in, bounded, redacted, and targeted at
  dedicated non-production resources.
- Never perform an unapproved Notion mutation, Telegram send, provider call,
  shared-database mutation, or Redis cleanup as part of a test.

Run the deterministic suite with:

```bash
uv sync --dev
uv run --no-env-file --frozen pytest -q
```

## Documentation

Document current behavior and durable rationale, not development chronology or
one-time verification output. Use simple English, link to the canonical
document for shared concepts, and keep the product label `AI Supplement Zone`
unchanged. Record durable architectural decisions under `docs/decisions/` and
operational procedures under `docs/runbooks/`.

## Pull requests

A pull request should identify the behavior or documentation change, affected
safety boundaries, verification performed, and any live dependency or release
capability that remains unverified. Reviewers should confirm:

- architecture and permission boundaries remain intact;
- original notes and old supplements cannot be modified;
- pending and rejected proposals remain outside production retrieval;
- idempotency, concurrency, failure, and recovery claims match the code;
- API, workflow, deployment, and runbook documentation stays aligned;
- no secret, `.env`, private Notion content, callback token, raw source text,
  generated database, or temporary live report is included.
