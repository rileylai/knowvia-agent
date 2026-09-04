# AGENTS.md

## 1. Project Mission

LearnLoop Agent is a local-first Notion knowledge agent.
It indexes existing Notion notes as read-only knowledge, generates AI supplement proposals from learning materials, and writes accepted content only into `AI Supplement Zone`.

## 2. Core Safety Rules

1. Never directly overwrite existing Notion notes.
2. Never directly edit manually created notes.
3. Never directly edit old AI supplement blocks.
4. Never create per-page writable original-note mode in MVP.
5. All AI writes must follow: `Change Request -> Human Accept -> Append to AI Supplement Zone`.
6. Pending and rejected change requests must not be used in production RAG.
7. Notion is the source of truth.
8. User manual Notion edits require manual incremental sync.
9. Accepted agent appends trigger immediate page re-index.
10. Never log secrets or private raw source content.

## 3. Architecture Rule

- Use flow: API Route -> Orchestrator -> Service / Tool -> Repository -> External System.
- LLM flow: API Route -> Orchestrator -> Provider Router -> Provider Adapter.
- Tool flow: API Route -> Orchestrator -> Tool Registry -> Local Tool Adapter or future MCP Client.
- API routes and orchestrators must not directly import or call OpenAI, Claude, Gemini, Notion, Redis, PostgreSQL, or external APIs.
- Queue backend must be accessed only through a QueueClient interface.
- LLM calls must go through a provider router/interface.
- External capabilities must go through schema-friendly tool interfaces.
- DB access still goes through repositories; queue access still goes through QueueClient.
- Raw PostgreSQL and Redis must not become LLM-facing tools.
- Tool interfaces should be MCP-compatible so they can later be exposed through MCP, but standalone MCP servers are not implemented in MVP.
- Business rules, permission checks, output validation, RAG inclusion rules, and Notion write safety must remain deterministic backend logic, not delegated to the LLM.
- Do not add LangChain or LangGraph in MVP unless a future ADR explicitly approves it.

## 4. Documentation Navigation

- `docs/00-design-doc.md`: main design source.
- `docs/01-architecture.md`: system architecture details.
- `docs/02-workflows.md`: workflow definitions and state transitions.
- `docs/03-guardrails.md`: safety and write policy.
- `docs/04-memory-design.md`: state ownership, persistence, and sync model.
- `docs/05-rag-design.md`: indexing, chunking, retrieval, citation.
- `docs/06-notion-permission-model.md`: Notion ownership and permissions.
- `docs/07-evaluation-plan.md`: eval metrics and golden tests.
- `docs/08-observability.md`: logs, metrics, tracing, cost.
- `docs/09-api-contract.md`: API contract.
- `docs/10-deployment.md`: local-first deployment and future V2 cloud.
- `CONTRIBUTING.md`: coding, testing, documentation, and collaboration rules.
- `docs/11-telegram-operator-contract.md`: Telegram operator commands,
  callbacks, authorization, safe output, and queue boundaries.
- `docs/prompts/`: prompt templates.
- `docs/decisions/`: ADRs.

## 5. Task Start Rule

Before making code changes, always read:

1. `AGENTS.md`
2. `docs/00-design-doc.md`
3. `dev_state/PROJECT_ROADMAP.md`
4. `dev_state/DAILY_LOG.md`
5. Task-related docs from the map above.

Task-related examples:

- DB or repository task: read `docs/01-architecture.md` and `CONTRIBUTING.md`.
- Workflow or queue task: read `docs/02-workflows.md` and `docs/08-observability.md`.
- Notion or write-policy task: read `docs/03-guardrails.md` and `docs/06-notion-permission-model.md`.
- RAG task: read `docs/05-rag-design.md`.
- API task: read `docs/09-api-contract.md`.

Repo docs are development and maintenance context by default.
`AGENTS.md` is development-agent guidance, not the LearnLoop runtime system prompt.
They are not production RAG source unless a future ADR and implementation explicitly wire them into runtime retrieval.

## 6. Coding Style Rules

- Use Python for backend.
- Use FastAPI for API layer when implementation starts.
- Use simple, explicit code.
- Prefer small functions.
- Use type hints.
- Use Pydantic schemas for API inputs/outputs.
- Use clear error names and `failure_reason` values.
- Keep business logic out of routes.
- Keep RQ-specific code behind queue interface.
- Keep Notion/OpenAI clients behind tools/providers.
- Add English comments only when they explain purpose or non-obvious logic.
- Do not over-comment obvious lines.

## 7. Documentation Style Rules

- Use simple English.
- Keep critical product label `AI Supplement Zone`.
- Prefer short sections and tables.
- Record major decisions as ADR files under `docs/decisions/`.
- Update `dev_state/DAILY_LOG.md` after meaningful local development work.
- If behavior changes, update related docs.

## 8. Definition of Done

A task is done only when:

- The implementation matches the design doc.
- Unit tests or documentation acceptance checks pass.
- Guardrails are not weakened.
- Relevant docs are updated.
- `dev_state/DAILY_LOG.md` has a short entry for meaningful local development work.
- No secrets or private Notion content are committed.

## 9. Current MVP Constraints

- Local-only MVP.
- Telegram first.
- No WhatsApp, LINE, Discord, Bilibili in MVP.
- No standalone MCP server in MVP; MCP-oriented provider/tool interfaces are allowed.
- No LangChain or LangGraph in MVP.
- No always-on cloud sync.
- No direct original note editing.
- No inline proposal edit UI.
- No reranker.
- No LLM-as-judge.
