# Knowvia Agent repository instructions

## Language

- Codex 回覆使用繁體中文。
- Human-facing project documentation 使用繁體中文。
- 技術名稱保留英文；code identifiers、API、schema、database fields 與 test names
  使用英文。
- Runtime log message 使用英文。

## Documentation

- 撰寫或大幅修改 human-facing Markdown 前，先使用 `/avoid-ai-writing` skill。
- Desired behavior 讀 `docs/`；development priority 與 current phase 讀
  `dev_state/PROJECT_ROADMAP.md`；已確認的 decisions 讀
  `docs/decisions/DECISIONS.md`。
- Current implementation 以 application code、tests、migrations、config 與
  dependency lockfile 為準。
- 不把 planned、future 或 legacy capability 寫成已完成；詳細產品與 guardrail 規則
  留在 relevant spec，不要複製到本文件。
- `README.md` 與 `docs/*.md` 面向第三方工程師，使用 capability-oriented current-state
  wording；internal roadmap IDs 與 development slice tracking 只保留在 `dev_state/`。
- Development state 使用 `dev_state/PROJECT_ROADMAP.md` 與
  `dev_state/DAILY_LOG.md`；accepted decisions 使用
  `docs/decisions/DECISIONS.md`。不要把 daily log 當成 current spec。

## Documentation Navigation

不要 mechanical preload 全部文件。先判斷 task 類型，讀一份 primary source；只有跨越多個 concern 時才讀 secondary source。任何 current implementation claim 都要回查 application code、tests、migrations、config 與 lockfile，文件不是 runtime authority。

| Task | Primary source | Secondary source when needed |
| --- | --- | --- |
| Product scope、MVP、non-goal、user behavior | `docs/00-product-spec.md` | `docs/04-quality-and-guardrails.md` |
| Architecture、component responsibility、authority boundary | `docs/01-architecture.md` | `docs/decisions/DECISIONS.md`、relevant code |
| Entity、schema、data ownership、API/tool contract | `docs/02-data-and-contracts.md` | migrations、schemas、tests |
| Knowledge ingestion / retrieval workflow | `docs/03-workflows.md` | `docs/04-quality-and-guardrails.md`、`src/orchestrators/`、`src/rag/` |
| Conversation、Memory、Agent、tool calling、MCP workflow | `docs/03-workflows.md` | `docs/02-data-and-contracts.md`、`src/agent/`、`src/mcp/` |
| Grounding、citation、`insufficient_info`、retrieval safety、evaluation | `docs/04-quality-and-guardrails.md` | `docs/02-data-and-contracts.md`、`eval/`、tests |
| Frontend behavior、SSE、manual acceptance | `docs/03-workflows.md` | `frontend/src/`、`docs/05-development.md` |
| SDD、TDD、tests、manual verification、repository workflow | `docs/05-development.md` | relevant tests and `dev_state/DAILY_LOG.md` |
| Local setup、Docker、migration、health/readiness、demo | `docs/06-deployment-and-demo.md` | `README.md`、`docker-compose.yml`、`.env.example`、scripts |
| Current priority、status、next slice | `dev_state/PROJECT_ROADMAP.md` | `dev_state/DAILY_LOG.md` |
| Accepted product / architecture decision | `docs/decisions/DECISIONS.md` | current implementation and roadmap |
| Recent investigation、verification result、known issue | `dev_state/DAILY_LOG.md` | roadmap and relevant code/tests |

`README.md` 是 onboarding 與 navigation entry point，不是 detailed spec。`PROJECT_ROADMAP.md` 是 status source，不是 architecture source；`DAILY_LOG.md` 是 chronological evidence，不是 product spec。若 spec、decision 與 implementation 不一致，先確認 current code/tests/config，再只修正文件或在 report 列出 discrepancy。

## Development workflow

- 使用 Spec-Driven Development（SDD）與 Test-Driven Development（TDD）。
- 每次只處理一個小 vertical slice：先定義 observable behavior，再寫 failing
  automated test，完成 minimal implementation 與 regression。
- Phase 0 cleanup 後，每個主要 feature slice 必須有最小 frontend manual acceptance
  path。
- Automated tests 不取代 frontend manual verification；尚未人工驗證時，在
  `dev_state/DAILY_LOG.md` 明確記錄 `Not yet manually verified.`。
- Documentation-only task 不開始 runtime implementation，也不順手修改 unrelated
  files。

## Fixed project constraints

- 產品只有一個 bounded Knowledge Agent；不建立 Multi-Agent system，也不加入
  LangChain 或 LangGraph migration。
- MCP 是 adapter boundary，不放 business logic。
- Notion page listing 與 sync 是 deterministic backend operation。
- Long-term memory 只接受 explicit save。
- Backend 擁有 citations、permission boundaries、validation、persistence policy 與
  termination control。
- Redis/RQ 不列入 Knowvia MVP core。
- Telegram、Supplement、ChangeRequest 與 Notion write-back 是 Legacy；除非使用者
  明確要求，不得接回 Knowvia execution path。

## Git and safety

- 不自行 commit、push、merge、stash，或使用 destructive git commands。
- 不修改使用者未要求的 unrelated files。
- 不把 secrets、credentials、private source content、private page ids 或完整
  database URLs 寫入 repository 或 logs。
