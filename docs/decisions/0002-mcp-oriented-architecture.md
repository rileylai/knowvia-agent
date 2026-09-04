# ADR-0002: MCP-Oriented Architecture

## Status

Accepted

## Context

LearnLoop integrates providers, Notion, Telegram, parsers, OCR, PostgreSQL,
and Redis. Application workflows should not be coupled to a particular SDK or
infrastructure client, while the MVP should remain local and small.

## Decision

- LLM calls go through `ProviderRouter` and provider interfaces.
- External capabilities go through schema-friendly `Tool` contracts and
  `ToolRegistry`.
- PostgreSQL access goes through repositories and unit-of-work boundaries.
- Redis/RQ access goes through `QueueClient`.
- Routes and orchestrators do not import external SDKs directly.
- Tool contracts remain compatible with a future MCP client or extracted MCP
  server, but the MVP does not run a standalone MCP server.
- Permissions, target ownership, validation, RAG eligibility, write safety,
  state transitions, and queue/idempotency policy remain deterministic backend
  responsibilities.

## Consequences

Adapters can be replaced or tested with fakes without changing workflow code.
Future MCP exposure can reuse stable tool schemas. The architecture has more
interfaces than a direct SDK integration, but the boundaries protect the
Notion write policy and make external failures explicit.
