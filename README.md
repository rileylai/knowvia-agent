# Knowvia Agent

Knowvia Agent 是一個 Enterprise Knowledge Agent。它把企業分散在 PDF、
Screenshot / Image、Web URL、YouTube 與 Notion 的內容整理成可檢索的
Knowledge Layer，再用持續對話提供可追溯來源的回答。

目前 repository 仍包含從 LearnLoop Agent 重用的 Notion indexing、source
parser 與 RAG foundation。這些程式碼是現況，不代表目標產品已經完成。

## 狀態

| 範圍 | 狀態 |
| --- | --- |
| Notion read、page listing、single-page/full/incremental indexing | 已存在；目前 RAG 仍以 Notion chunks 為主 |
| PDF、Image/OCR、URL、YouTube、chat text ingestion | 已存在 parser 與 SourceDocument persistence |
| pgvector retrieval、lexical fallback、backend-owned citations | 已存在，但 retrieval 仍是 Notion-only |
| Generic multi-source Knowledge Layer | Planned |
| Conversation sessions、short-term context、LongTermMemory | Planned |
| 單一 bounded Knowledge Agent、MCP tools、tool chaining | Planned |
| SSE streaming 與 Web UI | Planned |
| Telegram、Supplement、Notion write-back、RQ worker | Legacy；不屬於 Knowvia active product flow |

Foundation cleanup 後，開發採 incremental vertical slices。Phase 1 先建立最小
frontend manual-acceptance harness，之後每個主要 capability 同時交付 backend
behavior、automated test 與最小 frontend surface。

## 邏輯架構

```text
PDF / Image / URL / YouTube / Notion
  -> deterministic ingestion and sync
  -> Knowledge Layer
  -> Retrieval Service
  -> bounded Knowledge Agent
  -> grounded answer and citations
  -> SSE / Web UI

Explicit user memory request
  -> Memory Service
  -> LongTermMemory
```

產品只有一個 bounded Knowledge Agent。Retrieval、Memory、Notion sync 與
MCP 都是 capability、service 或 adapter，不是另一個 Agent。

## 技術棧

| 層 | 技術 |
| --- | --- |
| Backend | Python、FastAPI、Pydantic、Uvicorn |
| Persistence | PostgreSQL、SQLAlchemy、Alembic |
| Retrieval | pgvector、cosine similarity、OpenAI embeddings |
| Source processing | pypdf、trafilatura、YouTube transcript API、Pillow、Tesseract OCR |
| Planned UI | React / Next.js、SSE |
| Planned tool boundary | MCP-compatible adapters |
| Local tooling | uv、Docker Compose |

## 目標 Demo

1. 在 Knowledge Tab 選取一個 Notion page。
2. 上傳一份 PDF。
3. 等待 deterministic parse、chunk、embedding 與 indexing。
4. 在 Chat 中詢問企業內容相關問題。
5. Agent 使用 `search_knowledge`，回答並附 backend-generated citations。
6. 在同一個 session 追問，再用明確指令保存一個 decision。
7. 開啟 New Chat，透過 `search_memory` 找回該 decision。
8. 詢問沒有足夠 evidence 的問題，回傳 `insufficient_info`。

## 限制

目前 source ingestion 尚未接到 generic chunk 與 retrieval pipeline。現有
QA 主要使用 Notion-derived `KnowledgeChunk`。Conversation session、long-term
memory、MCP execution loop、SSE 與 frontend 尚未在 runtime 實作。

Docling 是候選 parser，會以 3 至 5 份代表性文件做 time-boxed 評估；parser
completeness benchmark 不會阻塞 Agent MVP。

## 文件入口

- [Product Spec](docs/00-product-spec.md)
- [Architecture](docs/01-architecture.md)
- [Data and Contracts](docs/02-data-and-contracts.md)
- [Workflows](docs/03-workflows.md)
- [Quality and Guardrails](docs/04-quality-and-guardrails.md)
- [Development](docs/05-development.md)
- [Deployment and Demo](docs/06-deployment-and-demo.md)
- [Roadmap](dev_state/PROJECT_ROADMAP.md)
- [Decisions](dev_state/DECISIONS.md)

`docs/prompts/` 保留 runtime prompt templates；它們不是產品 scope 的
source of truth。`dev_state/` 是正式追蹤的 Knowvia development state，包含
roadmap、daily log 與 decisions。
