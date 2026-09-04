# Knowvia Agent Project Roadmap

本文件只記錄 development priority、交付結果與驗證方式。詳細 implementation
behavior 應放在相關 `docs/` 與 just-in-time SDD spec。

Phase 0 完成後，每個主要 feature 都先切成可人工操作的 end-to-end vertical
slice。Automated test 不能取代 frontend manual verification。未完成所需人工驗收
時不可標記 `done`。新發現的 follow-up 使用 `N.1`、`N.1.1` 等新 ID append，不重編
既有 roadmap。Status 只使用固定 vocabulary，純 foundation 或 documentation work
可依自己的 Verification contract 在沒有 frontend gate 的情況下完成。

## Full Roadmap

| ID | Phase / Slice | Status | Goal | Deliverable | Verification | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| 0.1 | Documentation Baseline | done | 建立 Knowvia source of truth、Roadmap、Decisions 與 repository instructions。 | `docs/` baseline、`dev_state/` 三份文件、精簡 `AGENTS.md`。 | Automated: path、state layout 與 diff checks pass.<br>Manual: Documentation structurally reviewed. | 純 documentation work，不需要 frontend gate。 |
| 0.2 | Foundation Runtime Cleanup | done | 斷開 inherited LearnLoop runtime dependency，保留 Knowvia backend baseline。 | Telegram、Supplement、Notion writer、Redis/RQ core readiness dependency disconnected；Notion indexing、QA、source ingestion preserved。 | Automated: foundation regression and readiness checks pass.<br>Manual: Backend baseline independently verified; frontend not required. | Automated checks and backend manual verification complete. 未開始新的 frontend feature。 |
| 0.2.1 | Knowvia Local Runtime Isolation | manual_verification | 將 active local PostgreSQL 與 LearnLoop data 分開。 | `knowvia-postgres`、dedicated volume、`knowvia` role/database 與更新後的 local config。 | Automated: migration、pgvector、readiness 與 database identity checks pass.<br>Manual: Docker state and preserved legacy data reviewed. | Local verification 通過。舊 LearnLoop container、bind mount 與資料保留，等待使用者確認。 |
| 1.0 | Thin Frontend Harness | manual_verification | 提供後續每個 slice 的最小人工驗收入口。 | Frontend shell + Knowledge、Chat、Memory surfaces；Memory 先是 placeholder。 | Automated: API connection、loading、success、error 與 Chat baseline tests pass.<br>Manual: Chat baseline request → loading/success/error visible. | Automated tests、production build 與 startup probe 已通過。Not yet manually verified. Current QA 仍是 Notion-only baseline。 |
| 1.0.1 | Chat Keyboard Submission | manual_verification | 讓 textarea 以 Enter 送出，Shift+Enter 保留換行。 | IME-safe keyboard submit，不改變 button、loading 或 error flow。 | Automated: Enter、Shift+Enter、IME、empty query 與 duplicate-submit tests pass.<br>Manual: Keyboard behavior reviewed in browser. | Automated tests 通過；`1.0` 仍等待完整 manual verification。 |
| 1.0.2 | Insufficient Info Citation Contract | manual_verification | 確保 evidence 不足時不附 citation。 | Backend 保證 `insufficient_info=true` 時 `citations=[]`；frontend 清除前次結果。 | Automated: orchestrator、API 與 frontend regression pass.<br>Manual: `hi` 顯示 insufficient info 與 zero citations。 | Local `hi` probe 通過，等待使用者確認；未調整 retrieval architecture 或 ranking。 |
| 2.0 | Generic Knowledge Contract | planned | 定義所有 source 共用的最小 ingestion、chunk、embedding、retrieval 與 citation contract。 | Generic `SourceDocument`、`KnowledgeChunk`、retrieval 與 backend citation contract。 | Automated: Contract and eligibility regression passes.<br>Manual: Combined with 2.1 source flow. | 不建立 abstraction-only backend phase。 |
| 2.1 | PDF Knowledge Pipeline | planned | 先完成一條可驗收的 generic source path。 | PDF → `SourceDocument` → `KnowledgeChunk` → embedding → retrieval → citation → frontend verification。 | Automated: PDF indexing, retrieval and citation regression passes.<br>Manual: Upload PDF → indexing → Chat question → citation. | 後續修正可 append `2.1.1`、`2.1.2`。 |
| 2.2 | URL Knowledge Pipeline | planned | 以同一 generic pipeline 加入 URL。 | URL ingest、index、RAG、citation 與 Knowledge UI flow。 | Automated: URL ingest and retrieval regression passes.<br>Manual: Paste URL → indexing → Chat → citation. | 不另建 source-specific contract。 |
| 2.3 | Screenshot / Image Knowledge Pipeline | planned | 以 OCR 將 image 加入 generic Knowledge。 | Image upload、OCR、index、retrieval、citation 與 UI flow。 | Automated: OCR and image retrieval regression passes.<br>Manual: Upload image → OCR/index → Chat → citation. | OCR failure 要有可見 error。 |
| 2.4 | YouTube Knowledge Pipeline | planned | 以 transcript 將 YouTube 加入 generic Knowledge。 | YouTube URL、transcript、index、retrieval、citation 與 UI flow。 | Automated: Transcript and retrieval regression passes.<br>Manual: Paste YouTube URL → transcript/index → Chat → citation. | 沿用 generic source contract。 |
| 2.5 | Notion UX Integration | planned | 在既有 Notion index foundation 上補 deterministic Knowledge UX。 | `list pages`、select、sync、status、ask、citation 的 API 與 UI flow。 | Automated: Page listing, sync, permission and citation regression passes.<br>Manual: List pages → select → sync → ask → citation. | 完成後 Knowledge Tab 才達到 MVP manual acceptance。 |
| 3.0 | Conversation Sessions | planned | 建立 session isolation 與 last-N short-term context。 | `ConversationSession`、`ConversationMessage`、last-N context、New Chat 與 session interaction。 | Automated: Session isolation and context regression passes.<br>Manual: Ask → follow-up → New Chat → old short-term context absent. | 驗收後新增工作使用 `3.1` 或 `3.1.1`。 |
| 4.0 | Persistent Memory | planned | 建立 explicit save 的 owner-scoped long-term memory。 | Explicit save、vector retrieval、owner scope、Memory Inspector、View/Delete。 | Automated: Save, owner filter, search and delete regression passes.<br>Manual: Session A save → New Chat → Session B recall → Inspector delete. | 不把 memory 混入 Knowledge corpus。 |
| 5.0 | MCP and Bounded Agent Runtime | planned | 將 Knowledge、Memory 與 save capability 放入 bounded tool loop。 | `search_knowledge`、`search_memory`、`save_memory`、MCP adapter、validation、max tool calls、termination。 | Automated: Allowlist, schema, permission, max 3 calls and termination regression passes.<br>Manual: Same Chat UI verifies three tools and bounded chaining. | 不顯示 chain-of-thought。 |
| 6.0 | SSE Streaming and UX Hardening | planned | 將既有 Chat request/response 升級為 streaming lifecycle。 | Execution status、`answer_delta`、citations、`done` 與 error behavior。 | Automated: SSE event and disconnect regression passes.<br>Manual: Chat visibly follows streaming lifecycle. | Frontend 由 Phase 1 延續，不重建。 |
| 7.0 | Evaluation and Demo Hardening | planned | 將 Agent behavior 與正式 demo 變成可重複 regression。 | Agent Golden Set、retrieval、grounding、citation、session、memory、tool safety、5 至 10 分鐘 demo flow、README alignment。 | Automated: Golden Set and demo regression passes.<br>Manual: Complete the formal Demo Story. | 每次人工結果記錄在 `dev_state/DAILY_LOG.md`。 |

ID 規則：`N.0` 是 major capability，`N.M` 是 feature slice 或 follow-up，`N.M.K`
是驗證後新增的更細 follow-up。新工作直接 append，不能為了排序重編既有 ID。

Status vocabulary：`planned`、`in_progress`、`automated_verified`、
`manual_verification`、`done`、`blocked`、`deferred`。
