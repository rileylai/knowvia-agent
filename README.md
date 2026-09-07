# Knowvia Agent

Knowvia Agent 是一個 Enterprise Knowledge Agent。它把 PDF、Screenshot / Image、
Web URL 與 Notion 內容整理成可檢索的 Knowledge Layer，再用持續對話提供可追溯
來源的回答。

目前 repository 仍包含從 LearnLoop Agent 重用的 Notion indexing、source
parser 與 RAG foundation。這些程式碼是現況，不代表目標產品已經完成。

## 狀態

| 範圍 | 狀態 |
| --- | --- |
| Notion read、page listing、single-page/full/incremental indexing | 已存在 |
| PDF、URL、Image/OCR ingestion | 已接入 generic chunk、embedding、retrieval 與 citation |
| YouTube、chat text ingestion | 已存在 parser 與 SourceDocument persistence，尚未接 generic indexing |
| pgvector retrieval、lexical fallback、backend-owned citations | 已存在；支援 Notion、PDF、URL 與 Image |
| Generic multi-source Knowledge Layer | 已完成 PDF、URL、Image 與既有 Notion indexing path；Notion UX 仍是 planned |
| Conversation sessions、short-term context、LongTermMemory | 已完成；owner-scoped、explicit save、Memory Inspector |
| 單一 bounded Knowledge Agent、MCP tools、tool chaining | 已完成；`search_knowledge`、`search_memory`、`save_memory`，最多 3 次 tool calls |
| Native MCP stdio | 已完成；`initialize`、`tools/list`、`tools/call` |
| SSE streaming 與 Web UI | 已完成；execution status、answer delta、citations、done 與多 pane workspace |
| 7.0 Golden Set 與 deterministic evaluation | Automated verified；正式 browser Demo Story 尚待人工驗證 |
| Telegram、Supplement、Notion write-back、RQ worker | Legacy；不屬於 Knowvia active product flow |

Foundation cleanup 後，開發採 incremental vertical slices。Phase 1 先建立最小
frontend manual-acceptance harness，之後每個主要 capability 同時交付 backend
behavior、automated test 與最小 frontend surface。

## 邏輯架構

```text
PDF / Image / URL / Notion
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
| UI | React、Vite、SSE client |
| Tool boundary | Native MCP stdio adapter、bounded in-process registry |
| Local tooling | uv、Docker Compose |

## Demo

正式 5 至 10 分鐘 Demo Story 與 fallback query 位於
[Deployment and Demo](docs/06-deployment-and-demo.md)。主線使用 `mock_data/` 的
PDF，展示 Knowledge inventory、grounded answer、same-session follow-up、explicit
memory save、New Chat recall、Knowledge/Memory authority 分離，以及
`insufficient_info`。

Demo 前可執行：

```bash
npm --prefix frontend run build
uv run --no-env-file --frozen python scripts/demo_preflight.py
uv run --no-env-file --frozen python -m eval.run_agent_eval --report /tmp/knowvia-eval.json
```

## 限制

YouTube 與 chat text source ingestion 尚未接到 generic chunk 與 retrieval pipeline。
Notion 的 deterministic indexing 已存在，但目前沒有新的 Notion page-selection UX。
Provider-native token streaming、SSE replay/reconnect、remote MCP 與 5.0.3 / 5.0.4
follow-up 不在目前 scope。

7.0 deterministic evaluation 不依賴 live LLM、live URL、live OCR、private Notion 或
production database。正式 Demo Story 仍需由使用者在 browser 中完成一次，完成前
roadmap 狀態維持 `manual_verification`。

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
