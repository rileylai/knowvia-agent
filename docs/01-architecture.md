# Knowvia Agent 架構

## 架構邊界

```text
Frontend
  -> FastAPI API
  -> Orchestrator
  -> Service / Provider Router / Tool Registry
  -> Repository 或 External Adapter
```

Route 負責 transport contract、authentication hook、dependency wiring 與
response mapping。Orchestrator 負責 workflow sequencing。Service 負責
deterministic policy。Repository 負責 PostgreSQL access。Provider 與 Tool
adapter 隔離外部能力。

```mermaid
flowchart LR
    UI["Web UI"] --> API["FastAPI API"]
    API --> ORCH["Application Orchestrators"]
    ORCH --> INGEST["Knowledge Ingestion"]
    ORCH --> AGENT["Bounded Knowledge Agent"]
    AGENT --> MCP["MCP-compatible Tool Layer"]
    AGENT --> CONTEXT["Context Assembly"]
    INGEST --> KNOWLEDGE["Knowledge Layer"]
    CONTEXT --> RETRIEVAL["Retrieval Service"]
    CONTEXT --> MEMORY["Memory Service"]
    RETRIEVAL --> PG["PostgreSQL + pgvector"]
    MEMORY --> PG
    KNOWLEDGE --> PG
    ORCH --> PROVIDER["Provider Router"]
    PROVIDER --> LLM["LLM / Embedding Adapters"]
    MCP --> TOOLS["Allowed Tool Adapters"]
    API --> SSE["SSE"]
    SSE --> UI
```

## Component 狀態

| Component | 狀態 | 責任 |
| --- | --- | --- |
| Web App | `NEW` | Knowledge Tab、Chat、Memory Inspector、SSE client |
| FastAPI backend | `EXISTING` | API boundary、auth、dependency wiring |
| Knowledge APIs | `MODIFY` | 將 source ingestion 與 Notion sync 統一成 Knowledge flow |
| Source ingestion | `EXISTING` / `MODIFY` | 現有 parser/persist；補 generic chunk/index |
| Notion sync | `EXISTING` | deterministic page listing、sync、chunk、embed、index |
| Knowledge Layer | `MODIFY` | 統一 `KnowledgeSource`、`SourceDocument`、`KnowledgeChunk` |
| Retrieval Service | `EXISTING` / `MODIFY` | 共用 Notion、PDF、URL 的 pgvector 與 lexical fallback；套用 source eligibility |
| Conversation State | `NEW` | session、message、short-term context budget |
| Context Assembly | `NEW` | 組合 knowledge evidence、memory 與 conversation context |
| Memory Service | `NEW` | explicit save、owner scope、semantic retrieval |
| Bounded Knowledge Agent | `NEW` | 單一 Agent 的有限 tool loop 與 answer generation |
| MCP Tool Layer | `NEW` | standardized adapter；不擁有 business logic |
| Provider Layer | `EXISTING` / `MODIFY` | Provider Router、LLM 與 embedding adapters |
| PostgreSQL + pgvector | `EXISTING` | durable records、chunks、vectors、future session/memory |
| SSE | `NEW` | browser streaming transport |
| Redis/RQ | `LEGACY` | Telegram worker；不列入 Knowvia MVP core |

## Knowledge ingestion

所有 source 應走同一個 deterministic boundary：

```text
Source adapter
  -> validation
  -> parse
  -> normalize
  -> SourceDocument
  -> chunk
  -> embedding
  -> KnowledgeChunk
  -> retrieval index
```

目前 code 的 PDF 與 URL flow 都會經過 `SourceDocument`、共用 chunk、embedding
與 retrieval eligibility。Notion flow 也已完成 chunk、embedding 與 indexing；
Image/OCR、YouTube 與 chat text 仍在 `SourceDocument` 後停止。URL 仍使用既有
parser adapter，不建立 source-specific chunk 或 retrieval contract。

## Notion boundary

Notion page listing、page selection、full index 與 incremental sync 都是
deterministic backend operations。LLM 不決定 page id、sync scope 或 eligibility。

Knowvia active path 只讀 Notion。Notion writer、Supplement、ChangeRequest 與
AI Supplement Zone 屬於 inherited LearnLoop legacy，不加入新 Agent runtime。

## Agent runtime

Agent runtime 只建立一個 bounded Knowledge Agent：

```text
User message
  -> load session context
  -> decide whether an allowed tool is needed
  -> validate and run tool
  -> add bounded result to context
  -> answer or run another allowed tool
  -> stop at max iterations / max tool calls
```

初始每次 run 的 tool call 上限為 3。Backend 會檢查 allowlist、schema、timeout、
write permission、memory policy、citation 與 termination。LLM 不得自行取得新
權限或改寫 Agent state。

## MCP boundary

MCP adapter 只負責 protocol mapping：

```text
MCP search_knowledge
  -> Retrieval Service
  -> Chunk Repository
  -> pgvector
```

Tool adapter 不直接讀 raw PostgreSQL 或 Redis，也不放置 business rules。`save_memory`
仍須交給 Memory Service 做 explicit-save policy、owner check、schema validation
與 persistence。

## Context assembly

Context Assembly 分開處理三種資料：

1. session 最近 6 則 messages，受 token budget 限制。
2. Knowledge evidence，附 source provenance 與 backend citation metadata。
3. LongTermMemory，標示為 saved memory，不當作 enterprise document citation。

KnowledgeChunk 與 LongTermMemory 不能共用 retrieval corpus。

## Provider 與 persistence

LLM 與 embedding 必須經 Provider Router / Provider interface。Database access
必須經 Repository 與 Unit of Work。所有狀態轉移、retrieval eligibility、citation
與 limits 由 deterministic backend 控制。

## Streaming

Backend 以 SSE 發送 bounded execution events、answer delta、citations 與 done。
SSE 是 transport，不改變 Agent 的 permission、tool 或 persistence policy。不得
傳送 private model chain-of-thought。

## 外部系統

| 系統 | Active Knowvia 用途 | 備註 |
| --- | --- | --- |
| PostgreSQL | application state、KnowledgeChunk、vectors、planned sessions/memory | 必要 |
| pgvector | knowledge 與 memory semantic retrieval | 必要 |
| Notion | knowledge source | read/sync only |
| OpenAI 或其他 provider | LLM、embedding | 經 Provider Router |
| MCP server/adapter | allowed tool protocol boundary | Planned |
| Redis/RQ | inherited Telegram queue | Legacy，不是 MVP core |
