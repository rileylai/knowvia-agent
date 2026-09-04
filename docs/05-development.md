# Knowvia Agent 開發流程

本 repository 採 Spec-Driven Development（SDD）與 Test-Driven Development
（TDD）。目標是讓每個功能先有可觀察的行為，再進入實作。

## 一次只處理一個 vertical slice

```text
Requirement
  -> Spec
  -> Observable acceptance behavior
  -> Failing public-interface test
  -> Minimal implementation
  -> Minimal frontend surface
  -> Automated regression
  -> Human manual verification
  -> DAILY_LOG
```

測試循環是 Red、Green、Refactor。不要為尚未實作的 architecture 一次撰寫大量
speculative tests，也不要把 internal helper 當成主要 acceptance surface。

Foundation cleanup 完成後，每個主要 feature 都必須以 end-to-end vertical slice
交付。Slice 要同時包含 SDD、TDD 與 Frontend Manual Acceptance，不得先累積一批
只有 backend 的 feature，最後才一次接上 UI。

## SDD 規則

開始 coding 前：

1. 讀取相關 source-of-truth 文件。
2. 確認需求屬於 MVP、High Value、Bonus 或 Future。
3. 定義 public API、workflow 或 observable state。
4. 如果 spec 與 code 不一致，先記錄 current 與 target 的差異。
5. 只有需求或 decision 改變時才修改 spec。

詳細產品需求只放在 `docs/00-product-spec.md`。其他文件應引用它，避免複製
同一套完整規則。

## TDD 規則

每個 slice 至少包含：

- 一個從 public interface 驗證成功行為的 failing test。
- 相關 fail-closed、permission、limit 或 error behavior。
- 只覆蓋該 slice 的 regression。

Core tests 應使用 fixtures、injected clients 或 isolated database。Live dependency
checks 必須明確 opt-in、bounded、redacted，並使用專用資源。

## Frontend Manual Acceptance

Frontend manual verification 與 automated test 的目的不同，前者不能取代後者，
後者也不能取代前者。每個 slice 都要提供最小 frontend interaction，讓使用者能
操作實際入口並確認 visible result、loading、success 或 error behavior。

如果尚未完成 frontend manual verification，不能把 slice 標記為完成，並在
`dev_state/DAILY_LOG.md` 記錄：

```text
Not yet manually verified.
```

## Code convention

- Backend 使用 Python、FastAPI、Pydantic 與 type hints。
- Function、class、module、variable、schema 與 database identifiers 使用英文。
- 優先小函式與清楚的 interface。
- Route 不放 business logic。
- LLM 呼叫經 Provider Router。
- 外部能力經 Tool interface。
- PostgreSQL 經 Repository / Unit of Work。
- Queue 經 `QueueClient`。
- MCP adapter 不放 business logic。
- 不加入 LangChain、LangGraph 或 Multi-Agent，除非有新的明確 decision。

## Runtime log convention

新增或修改 runtime log、structured log、error log、warning log、debug log 與
audit log message 時，一律使用英文。Log 不得包含 secrets 或 private raw source
content。

## 文件 convention

Human-facing project documentation 使用繁體中文。必要的技術名詞保留英文。
修改文件前先使用 `/avoid-ai-writing` skill。文件應說明目前行為，不應把
planned architecture 寫成 current implementation。

## Repository safety

- 不使用 `git reset --hard`、`git checkout --`、`git clean` 或其他破壞性命令。
- 不自行 stash、commit、push 或 merge。
- 不把 `.env`、credentials、private Notion content、raw source text 或完整
  database URL 加入 repository。
- 不在測試中未經批准呼叫 live Notion、Telegram、provider、shared database 或
  Redis cleanup。
- Documentation-only task 不得順手修改 runtime、test、migration、Docker、
  dependency 或 lockfile。

## 本地驗證

依賴與測試命令以 repository current setup 為準：

```bash
uv sync --dev
uv run --no-env-file --frozen pytest -q
```

執行 live dependency check 前，先確認 command 的 environment guard、資源 scope
與輸出是否 redacted。

## 完成條件

一個 implementation slice 完成時：

1. acceptance behavior 有測試。
2. relevant regression 通過，或明確記錄未驗證原因。
3. guardrails 沒有放寬。
4. 受影響的 source-of-truth 文件已更新。
5. Frontend manual verification 已完成，或明確記錄尚未完成的原因。
6. `dev_state/DAILY_LOG.md` 有簡短紀錄，包含實際人工驗證內容。
7. 若有產品或架構決策，`dev_state/DECISIONS.md` 已更新。
