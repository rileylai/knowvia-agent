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

## 2026-09-04 Thin Frontend Harness

### Done

- 新增 Vite、React 與 TypeScript frontend。Knowledge、Chat、Memory 三個 surface
  使用單一 client-side navigation，不含 session persistence。
- Chat 連接現有 `POST /api/qa`，顯示 idle、loading、success、
  `insufficient_info` 與 error state。送出期間會停用輸入與按鈕。
- Citation 只讀取 backend response 的 `notion_path`、`page_id` 與 `score`。
- Knowledge 顯示目前的 Notion-only baseline；upload 與 URL controls 維持 disabled。
- Memory 只顯示 phase 4.0 placeholder，沒有 localStorage 或模擬資料。
- Local Vite proxy 將 `/api` 轉送至 `KNOWVIA_API_BASE_URL`。若 backend 設定
  `API_BEARER_TOKEN`，token 由 dev server 加入 request，不進入 browser bundle。

### Automated Evidence

- TDD Red：frontend test suite 先因 `src/App` 不存在而失敗。
- `npm test`：`6 passed`。
- `npm run build`：TypeScript check 與 Vite production build 通過。
- Vite dev server startup probe：`http://127.0.0.1:5173/` 回傳 frontend HTML。
- QA、trust boundary 與 foundation focused regression：`14 passed`。
- Frozen backend full regression：`628 passed, 5 skipped`。

### Manual Verification

Not yet manually verified.

等待使用者驗證 navigation、Chat success、backend citation、`insufficient_info`、
backend offline error，以及 Knowledge 與 Memory placeholder。

### Issues

- 本輪只設定 local Vite dev/preview proxy。若直接部署 `dist/`，hosting layer 仍需將
  `/api` reverse proxy 到 FastAPI。

### Next

- 使用者完成 frontend manual verification 後，再決定是否將 `1.0` 標記為
  `done`。本輪不開始 `2.0 Generic Knowledge Contract`。

## 2026-09-04 Runtime isolation and frontend follow-ups

### Done

- `0.2.1`：active Compose project 改為 `knowvia`。PostgreSQL 使用
  `knowvia-postgres`、`knowvia-postgres-data`、`knowvia` role/database 與 host port
  5433。舊 `learnloop-postgres` 已停止，原 bind mount 未刪除或修改。
- 新 database 已執行 Alembic migration。Database identity、pgvector、migration、
  schema、`/health` 與 `/ready` 均通過 local verification。
- `mock_data/` 現在包含三份 Knowvia PDF。三份 Notion JSON 已搬到
  `tests/fixtures/notion_pages/`，Notion mock tests 改讀 test-only fixtures。
- `1.0.1`：Chat 支援 Enter submit、Shift+Enter newline 與 IME composition guard。
  Empty query 與 loading 中的 Enter 不會送出 request。
- `1.0.2`：QA prompt v3 要求 evidence 不足時回傳 `INSUFFICIENT_INFO`。Backend 將
  sentinel 與已觀察到的 legacy insufficient phrase 映射為 canonical answer、
  `insufficient_info=true` 與空 citations。
- Frontend 在新 request 開始時清除前次 answer、citation 與 error；insufficient
  response 不顯示 citations。

### Automated Evidence

- Runtime isolation TDD：Red 為 `2 failed, 8 passed`；Green 為 `10 passed`。
- Keyboard TDD：Red 為 `2 failed, 10 passed`；Green 為 `12 passed`，加入 citation
  frontend regression 後為 `13 passed`。
- Insufficient citation TDD：Red 為 `4 failed, 10 passed`；Green 為 `14 passed`。
- Mock Notion relocation regression：`15 passed`。
- Combined targeted regression：`39 passed`。
- Frontend production build 通過。
- 第一次 full pytest 為 `631 passed, 5 skipped, 1 failed`。失敗項目是未修改的
  SQLite concurrent idempotency test；單獨重跑通過。第二次 full pytest 為
  `632 passed, 5 skipped`。

### Local Runtime Verification

- `knowvia-postgres`：healthy，使用 `knowvia-postgres-data`。
- PostgreSQL identity：role 與 database 均為 `knowvia`。
- pgvector：0.8.2。
- Alembic：`9c5e7b1a2d4f (head)`。
- Public schema：10 tables。
- `/health`：PASS。
- `/ready`：PASS。
- Local `hi` QA probe：`insufficient_info=true`、zero citations、canonical answer。
- 驗證期間 `knowvia-postgres` 曾短暫不存在，但 named volume 仍存在。使用同一
  volume 重建 container 後，schema、pgvector 與 Alembic revision 均保留；目前無法
  從現有 evidence 判定移除來源。

### Manual Verification

Not yet manually verified.

等待使用者確認 Docker state、舊 LearnLoop data 保留、三份 PDF、keyboard/IME
行為，以及有 evidence 與無 evidence 的 Chat 結果。

### Next

- `0.2.1`、`1.0.1`、`1.0.2` 與 parent `1.0` 維持
  `manual_verification`。`2.0` 與 `2.1` 未開始。
