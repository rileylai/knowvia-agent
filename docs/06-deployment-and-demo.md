# Knowvia Agent 部署與 Demo

本文件只描述 local setup、依賴、migration、health/readiness、demo preflight 與 release checklist。產品 scope、runtime topology 與 guardrail 規則分別由 `docs/00-product-spec.md`、`docs/01-architecture.md` 與 `docs/04-quality-and-guardrails.md` 負責。

## MVP 部署目標

Target MVP runtime：

```text
Frontend
  -> FastAPI backend
       -> PostgreSQL + pgvector
```

Redis/RQ 不屬於 Knowvia MVP core dependency。它們目前只服務 inherited Telegram
worker，應與 active Knowvia runtime 分離。

部署文件不預設 AWS、Kubernetes、production HA、distributed MCP service 或
cloud sync。這些是 Future Work。

## Current local 狀態

目前 code 可以從 uv environment 啟動 FastAPI。Docker Compose 以
`knowvia-postgres`、dedicated `knowvia-postgres-data` volume、`knowvia` role 與
`knowvia` database 提供 PostgreSQL + pgvector，host port 維持 5433。舊
`learnloop-postgres` 與原有 bind-mounted data 不屬於 active Compose project。

Compose 仍保留 Redis service 給 legacy queue。現有 API 的 Notion index、PDF、URL 與
Image/OCR validate/parse/index、source persistence 與 QA 都是 synchronous path；
YouTube 與 chat text 尚未完成 generic chunk/index pipeline。本地正向 QA 與正式 demo
使用 `mock_data/` 中的 PDF，不需要 Notion discovery 或 page selection。

Knowvia API startup、API preflight 與 core readiness 不建立或要求 Redis/RQ。
Redis service 只在需要執行 legacy worker 時使用。

## 本地設定

```bash
uv sync --dev
cp .env.example .env
```

設定 process environment 後啟動目前的 local dependencies：

```bash
set -a
source .env
set +a
docker compose up -d postgres
uv run --no-env-file --frozen alembic upgrade head
uv run --no-env-file --frozen uvicorn src.app.main:app --reload
```

需要 legacy worker 時，另外安裝 `legacy-worker` extra 並啟動 Redis：

```bash
uv sync --extra legacy-worker
docker compose up -d redis
```

需要確認目前 process 狀態時可使用：

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
```

`/health` 是 liveness check。`/ready` 是 dependency-aware check；在 runtime
disconnect 完成後，其結果只包含 Knowvia core dependency checks。

## Configuration 邊界

Local core path 的必要設定是 `DATABASE_URL` 與 `OPENAI_API_KEY`。`APP_ENV`、`LOG_LEVEL`、embedding batch/retry limits 與 workflow cost limits 有 application defaults；`API_BEARER_TOKEN` 是 optional。`.env.example` 只提供不含 secret 的 local baseline。

Notion 預設使用 `NOTION_BACKEND=mock` 與 `tests/fixtures/notion_pages`。需要 live Notion read 時，設定 `NOTION_BACKEND=live`、`NOTION_TOKEN`，並在 process environment 中提供相關 timeout/retry settings。

以下設定只供 legacy queue 或 Telegram path：

```text
REDIS_URL
TELEGRAM_BOT_TOKEN
TELEGRAM_WEBHOOK_SECRET
TELEGRAM_ALLOWED_CHAT_IDS
TELEGRAM_*_TIMEOUT_SECONDS
```

不要把 secrets 寫進文件、fixture、log 或 commit。

## Demo preflight

執行以下 bounded command。它只讀取 health/readiness、migration、source inventory，
並透過 native MCP `initialize` 與 `tools/list` 檢查三個 allowlisted tools；不會刪除
database、Knowledge 或 Memory。

```bash
npm --prefix frontend run build
uv run --no-env-file --frozen python scripts/demo_preflight.py
```

Preflight 會檢查 frontend `dist/` build artifact；如果 frontend 或 API 使用不同 host，
可傳入 `--api-url`、`--frontend-url`；正式 demo
的 PDF 預設名稱是 `Best Practices for Building AI Agents That Work in Production.pdf`。

## 5 至 10 分鐘 Demo Story

| Step | What I do | Expected screen | What this proves | Fallback |
| --- | --- | --- | --- | --- |
| 1 | 開啟 Knowledge Tab，確認 Indexed Sources。 | PDF source 與 chunk count 可見。 | Source inventory 可查。 | 重新整理 Knowledge Tab。 |
| 2 | 在 Chat 問：`What practices keep an AI agent reliable in production?` | `Searching knowledge...`、progressive answer、`Sources · N`。 | PDF grounding 與 backend citation。 | `What does the article say about deterministic control flow?` |
| 3 | 在同一個 Chat 問：`Can you summarize that in one sentence?` | 回答沿用同一 session context。 | Short-term conversation context。 | `Give me the main point in one sentence.` |
| 4 | 明確輸入：`Remember that our API convention is snake_case.` | `Saving memory...` 後顯示 `Memory saved`。 | Explicit persistent memory。 | `Please remember our API convention is snake_case.` |
| 5 | 按 `New Chat`，問：`What API convention did we save?` | `Used saved memory`，沒有 `Sources`。 | Cross-session memory 與 authority 分離。 | `What do you remember about our API convention?` |
| 6 | 回到 Knowledge question，問：`Which practices does the indexed PDF describe?` | `Sources · N` 再次出現。 | Saved memory 不冒充 enterprise citation。 | 使用 Step 2 的 backup query。 |
| 7 | 問：`What is our 2027 acquisition budget?` | `insufficient_info`，zero Sources。 | Evidence 不足時 fail closed。 | `What is our undocumented 2027 acquisition budget?` |
| 8 | Refresh browser，查看目前 session 與 Memory Inspector。 | conversation、citation disclosure 與 memory indicator 保留。 | Durable state。 | 重新開啟同一個 `session_id`。 |
| 9 | （Technical appendix）執行 `initialize` 與 `tools/list`。 | 只列出三個 Agent tools。 | Native MCP boundary。 | 使用 deterministic eval report。 |

正式 browser acceptance 仍未完成，下一個 active mainline 是 `7.0 Evaluation and Demo Hardening`，目前為 `manual_verification`。`8.5`、`8.6` 與 `8.6.1` 已完成，`8.6.2` 與 `8.6.3` 的 automated evidence 已完成但 manual gate 仍開放；`8.6.4` 已 rejected。`5.0.3.1` query-side semantic normalization、`5.0.3.3` unresolved actual-provider stability 與 `8.4.1` mixed readiness false negative 依 roadmap 維持 deferred。

## Browser acceptance checklist

用 desktop 100% zoom 完成 Demo Story，確認以下行為：

- Knowledge grounded answer 會顯示 progressive answer 與 `Sources · N`。
- same-session follow-up 能使用前一則對話，New Chat 不會帶入舊 short-term context。
- explicit save 顯示 `Memory saved`，New Chat recall 顯示 `Used saved memory`，且不顯示 document Sources。
- unsupported enterprise question 顯示 `insufficient_info` 與 zero Sources。
- refresh 後 conversation、citation disclosure 與 memory indicator 仍存在。
- Global Navigation、session list、conversation、Knowledge 與 Memory 各自可 scroll，composer 位於 Chat pane 底部。

## Parser 評估

Docling 是 candidate，不是 blocking dependency。評估採 time-boxed gate：

```text
3–5 representative documents
  -> current parser vs candidate
  -> human verification
  -> integration test
```

若 migration regression 太大，回到 current parser。Parser Golden Set 與
Completeness governance 暫時 deferred，不阻塞 Agent MVP。

## Release 邊界

在開始對外 demo 前，至少確認：

- source scope 與 owner filter 正確。
- QA citations 由 backend 產生。
- evidence 不足會回傳 `insufficient_info`。
- session isolation 通過測試。
- memory 只在 explicit save 後保存。
- tool allowlist、timeout、max tool calls、termination、MCP boundary、SSE redaction 與 source ownership 有測試或明確 verification evidence。
- SSE 不會輸出 chain-of-thought 或 secrets。
- `/health`、`/ready`、migration head、pgvector extension 與 provider configuration 通過檢查。
- PDF、Image/OCR、URL、Notion read/sync 與 Knowledge citation path 使用 current backend contract。
- explicit save、cross-session Memory recall、session isolation 與 `insufficient_info` negative path 通過 relevant tests 或已記錄未完成的 browser gate。
