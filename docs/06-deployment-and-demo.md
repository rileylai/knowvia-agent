# Knowvia Agent 部署與 Demo

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

Compose 仍保留 Redis service 給 legacy queue。現有 API 的 Notion index、PDF
validate/parse/index、source persistence 與 QA 都是 synchronous path；Image/OCR、
URL、YouTube 與 chat text 尚未完成 generic chunk/index pipeline。本地 positive QA
baseline 使用 `mock_data/` 中的 PDF，不需要 Notion discovery 或 page selection。

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

Knowvia core 需要的設定包括：

```text
APP_ENV
LOG_LEVEL
DATABASE_URL
OPENAI_API_KEY
API_BEARER_TOKEN
NOTION_BACKEND
NOTION_TOKEN（live Notion read 時）
embedding batch / retry settings
workflow cost limits
```

以下設定屬於 legacy queue 或 Telegram：

```text
REDIS_URL
TELEGRAM_BOT_TOKEN
TELEGRAM_WEBHOOK_SECRET
TELEGRAM_ALLOWED_CHAT_IDS
TELEGRAM_*_TIMEOUT_SECONDS
```

不要把 secrets 寫進文件、fixture、log 或 commit。

## Demo 流程

1. 開啟 Knowledge Tab。
2. 從 `mock_data/` 選取一份 PDF。
3. Backend 完成 validate、parse、normalize、chunk、embedding 與 indexing。
4. 開啟 Chat 並提出該 PDF 明確涵蓋的問題。
5. UI 顯示 grounded answer 與 backend-owned PDF citation。
6. 對沒有足夠 evidence 的問題回傳 `insufficient_info`。
7. 驗證 invalid upload 顯示 error，且 `Add URL` 維持 disabled。

如果 SSE 已完成，UI 可顯示 search、source count、memory search、generation、
answer delta、citations 與 done。不得顯示 private model chain-of-thought。

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
- tool allowlist、timeout、max tool calls 與 termination 有測試。
- SSE 不會輸出 chain-of-thought 或 secrets。
- current code 與文件中的 `EXISTING`、`MODIFY`、`NEW`、`FUTURE` 標記一致。
