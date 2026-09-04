# 2026-09-04

## Done

- 將 Knowvia development state 統一到 `dev_state/`。
- 清除舊 LearnLoop development-state 內容，不遷移舊 history。
- 建立 `PROJECT_ROADMAP.md`、`DAILY_LOG.md` 與 `DECISIONS.md`。
- 將 development workflow 改為 SDD、TDD 與 Frontend Manual Acceptance 的
  incremental vertical slices。
- 保持本輪為 documentation/workflow correction，未開始 runtime implementation。

## Automated Evidence

- Repository scan 沒有找到舊 development directory 的 active reference。
- Repository scan 沒有找到已刪除 LearnLoop state subpath 的 active reference。
- `find dev_state -maxdepth 2 -type f | sort` 只列出三份正式 state 文件。
- `git diff --check` 通過。
- 只檢查與修改 documentation、development state、`.gitignore` 與指定 references。
- 未執行 application、worker、live integration 或 runtime implementation。

## Manual Verification

Not yet manually verified. 本輪沒有 frontend implementation，也沒有進行 feature
frontend acceptance。

## Issues

- Phase 0 foundation cleanup 尚未開始。
- Current QA 仍是 Notion-only baseline。

## Next

- 人工 review Full Roadmap table 與 `AGENTS.md` Documentation Navigation；確認後再
  開始下一個 implementation slice。

## Documentation refinement

### Done

- 將 Roadmap 轉為 compact table。
- 加入 stable hierarchical IDs。
- 在 `AGENTS.md` 加入 Documentation Navigation。
- 未修改 implementation。

### Automated Evidence

- Roadmap、navigation references 與 status vocabulary 已完成 structural review。
- `git diff --check` 通過。

### Manual Verification

Documentation reviewed structurally; no runtime manual verification required.

## 2026-09-04 Foundation Runtime Cleanup

### Done

- Production `src.app.main:app` 只註冊 Knowvia core routes，Telegram 與 Supplement
  改由明確 opt-in 的 `src.app.legacy:app` 提供 compatibility verification path。
- Default tool registry 保留 Notion reader 與 source parser tools，不再建立
  Notion writer 或 Telegram bot tool。
- Core readiness 不建立或要求 Redis/RQ。`rq` 移到 `legacy-worker` extra；API
  preflight 不再檢查 RQ，並保留 `legacy-worker` preflight profile。
- 保留 Notion indexing、QA、source ingestion 與 legacy implementation files。

### Automated Evidence

- Foundation acceptance、preflight、legacy compatibility 與 active baseline 測試均
  通過。
- 受影響的 Notion indexing、QA、source ingestion 測試通過。
- `src.app.main` import probe 未載入 `rq` 或 Telegram/Supplement route、orchestrator、
  service、repository、Notion writer 與 Telegram bot modules。
- 初始 broad pytest discovery 為 `1045 passed, 5 skipped, 1 failed`；唯一失敗來自
  ignored 的 `tests/evals/parser_note_completeness/`，其中一個 test 直接讀取不存在的
  `dev_state/parser-note-completeness/human-review-intake.json`。
- 確認 Parser Completeness governance 已 deferred，未建立舊 `dev_state` tree；在
  `pyproject.toml` 排除該 deferred eval directory 後，frozen full pytest 為
  `628 passed, 5 skipped`。
- Focused verification：foundation、health 與 Notion backend wiring 為 `15 passed`；
  active app startup probe 與 legacy explicit opt-in import probe 均通過。

### Manual Verification

- backend startup：PASS
- `/health`：PASS
- `/ready`：PASS
- PostgreSQL connection：PASS
- migration current：PASS
- pgvector extension：PASS
- Redis/RQ 已不再是 core readiness dependency：PASS
- Telegram routes 未掛載於 active app：PASS
- Supplement routes 未掛載於 active app：PASS
- QA baseline：PASS
- backend-owned citation：PASS
- `insufficient_info`：PASS
- source ingestion baseline：PASS
- Notion indexing automated regression：PASS
- live Notion single-page indexing：PASS
- live indexing 結果：`http_status=200`、`status=succeeded`、
  `indexed_block_count=35`、`workflow_run_id=490`

Frontend 不在本輪驗收範圍內，未啟動。

### Issues

- Parser Completeness governance 的 ignored legacy test 仍可在 explicit path 下執行，
  但不再屬於 Knowvia full regression。

### Next

- 0.2 文件與 backend manual verification 已完成。下一個 roadmap priority 是
  `1.0 Thin Frontend Harness`，本輪未開始。
