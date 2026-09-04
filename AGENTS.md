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
  `dev_state/DECISIONS.md`。
- Current implementation 以 application code、tests、migrations、config 與
  dependency lockfile 為準。
- 不把 planned、future 或 legacy capability 寫成已完成；詳細產品與 guardrail 規則
  留在 relevant spec，不要複製到本文件。
- Development state 只使用 tracked `dev_state/`，保留
  `PROJECT_ROADMAP.md`、`DAILY_LOG.md` 與 `DECISIONS.md`。

## Documentation Navigation

不要預設讀取全部文件。依 task 只讀必要的 source of truth：

| 要處理的問題 | 優先閱讀 |
| --- | --- |
| 產品 scope、MVP、非目標、使用者行為 | `docs/00-product-spec.md` |
| 架構邊界、component responsibility、current/target architecture | `docs/01-architecture.md` |
| Entity、schema、資料 authority、API/tool conceptual contract | `docs/02-data-and-contracts.md` |
| End-to-end workflow、ingestion、chat、memory flow | `docs/03-workflows.md` |
| Grounding、citation、tool safety、memory policy、evaluation | `docs/04-quality-and-guardrails.md` |
| SDD、TDD、manual acceptance、repository workflow | `docs/05-development.md` |
| Local deployment、Docker、demo flow | `docs/06-deployment-and-demo.md` |
| 目前 development priority、status、下一個 slice | `dev_state/PROJECT_ROADMAP.md` |
| 已確認、不能自行推翻的產品／架構 decision | `dev_state/DECISIONS.md` |
| 最近工作、驗證結果、目前問題 | `dev_state/DAILY_LOG.md` |

- 先判斷 task 類型，再讀最少必要文件。
- 跨多個 concern 時，再讀對應文件；不要 mechanical preload 全部 docs。
- Desired behavior 以相關 spec 與 decision 為準，current implementation 以 code
  與 tests 為準。

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
