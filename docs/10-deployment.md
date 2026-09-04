# Deployment and Local Operations

The current runtime is local-only. Docker Compose supplies PostgreSQL/pgvector
and Redis; the FastAPI process and Telegram worker run from the locked `uv`
environment. The application reads process environment variables directly and
does not load `.env` automatically.

## Install

```bash
uv sync --dev
cp .env.example .env
```

Set the required values, export them, and start local services:

```bash
set -a
source .env
set +a
docker compose up -d
uv run --no-env-file --frozen alembic upgrade head
```

Set `DATABASE_URL` to match the PostgreSQL host port in use. In particular,
`.env.example` maps PostgreSQL to a non-default host port, while the
application's compatibility default points to the standard local port;
Compose's port mapping does not rewrite the application setting. Refer to
`.env.example` and `docker-compose.yml` rather than publishing credentials or
complete connection URLs. Compose exposes ports through `POSTGRES_PORT` and
the optional `REDIS_PORT` override.

## Configuration

| Variable | Purpose and default |
| --- | --- |
| `APP_ENV` | Runtime mode; default `local` |
| `LOG_LEVEL` | Log level; default `INFO` |
| `DATABASE_URL` | SQLAlchemy PostgreSQL URL; set it to the selected Compose host port |
| `REDIS_URL` | Redis/RQ connection; required by the ready `local` queue profile |
| `NOTION_BACKEND` | `mock` by default, or `live` |
| `MOCK_NOTION_DATA_DIR` | Optional mock Notion data directory |
| `NOTION_TOKEN` | Required when `NOTION_BACKEND=live` |
| `OPENAI_API_KEY` | Required for live embedding and LLM operations |
| `TELEGRAM_BOT_TOKEN` | Required for Telegram sends/downloads |
| `TELEGRAM_WEBHOOK_SECRET` | Optional only for local compatibility; required when the webhook is exposed |
| `TELEGRAM_ALLOWED_CHAT_IDS` | Optional local comma-separated allowlist; required for an exposed operator bot |
| `API_BEARER_TOKEN` | Optional only for local compatibility; required when protected routes are exposed |
| `MAX_WORKFLOW_COST_USD` | Optional per-workflow budget |
| `MAX_DAILY_COST_USD` | Optional daily budget |
| `WORKFLOW_STALE_AFTER_SECONDS` | Stale workflow threshold; default `3600` |
| `TELEGRAM_JOB_TIMEOUT_SECONDS` | Ordinary Telegram job bound; default `180` |
| `TELEGRAM_REVIEW_JOB_TIMEOUT_SECONDS` | Review Accept job bound; default `10800` |
| `TELEGRAM_INDEXING_JOB_TIMEOUT_SECONDS` | Full-index job bound; default `10800` |

Notion read and embedding retry/timeouts and batch limits are also configurable
through the names in `.env.example`: `NOTION_REQUEST_TIMEOUT_SECONDS`,
`NOTION_READ_MAX_ATTEMPTS`, `NOTION_READ_RETRY_BASE_SECONDS`,
`NOTION_READ_RETRY_MAX_SECONDS`, `EMBEDDING_BATCH_MAX_INPUTS`,
`EMBEDDING_BATCH_MAX_SINGLE_INPUT_BYTES`,
`EMBEDDING_BATCH_MAX_SINGLE_INPUT_TOKEN_ESTIMATE`,
`EMBEDDING_BATCH_MAX_AGGREGATE_BYTES`,
`EMBEDDING_BATCH_MAX_AGGREGATE_TOKEN_ESTIMATE`,
`EMBEDDING_REQUEST_MAX_ATTEMPTS`, `EMBEDDING_RETRY_BASE_SECONDS`, and
`EMBEDDING_RETRY_MAX_SECONDS`.

Keep `.env` and credentials outside Git. Do not print or commit secret-bearing
values.

## Start the API and worker

For a preflighted local API:

```bash
uv run --no-env-file --frozen python scripts/preflight.py --profile api --json
uv run --no-env-file --frozen uvicorn src.app.main:app --reload
```

The repository-relative wrapper performs the same preflight:

```bash
./scripts/run_live.sh
```

With `REDIS_URL`, start the Telegram worker in another shell:

```bash
uv run --no-env-file --frozen python scripts/run_worker.py
```

The worker validates the importable callables before consuming the `telegram`
queue, enables RQ's embedded scheduler, and selects `SpawnWorker` on macOS or
the standard worker on other platforms. `REDIS_URL` is required by the worker.

Redis stores RQ jobs and short-lived Telegram sessions; PostgreSQL remains the
durable workflow and idempotency store. Ordinary Telegram jobs use
`TELEGRAM_JOB_TIMEOUT_SECONDS`. Review Accept uses
`TELEGRAM_REVIEW_JOB_TIMEOUT_SECONDS`; Reject, Change Target, read-only
callbacks, and other ordinary work remain on the ordinary bound. Confirmed
full indexing is enqueued as a dedicated job with
`TELEGRAM_INDEXING_JOB_TIMEOUT_SECONDS`, because a large workspace can take
much longer than an ordinary command. The initial
full-index job is immediate and has no automatic RQ retry. The scheduler is
still required by the ready queue profile because delayed media-group settling
and configured retry intervals depend on it.

Without `REDIS_URL`, the application retains an in-process synchronous path and
in-memory Telegram sessions for tests, demos, and constrained local use. It
does not provide queued `202` execution, cross-process session coordination,
or durable RQ retry/scheduling, and `APP_ENV=local` readiness reports the queue
dependency as unavailable. Modes `test`, `demo`, and `mock` explicitly mark the
queue as not required.

## Readiness and smoke checks

```bash
curl http://127.0.0.1:8000/health
curl --fail-with-body http://127.0.0.1:8000/ready
uv run --no-env-file --frozen python scripts/preflight.py --profile test --json
```

`/health` only proves that the process responds. `/ready` also checks the
database, migrations, pgvector, required provider configuration, Notion mode,
Redis, and scheduler. OCR requires Tesseract languages `eng`, `chi_tra`, and
`chi_sim`; use `--profile ocr` when verifying that host.

PDF ingestion extracts only the PDF text layer with `pypdf`. Scanned or
image-only PDFs are not sent through screenshot OCR automatically and fail when
no extractable text is available.

## Database operations

Alembic migrations are explicit:

```bash
uv run --no-env-file --frozen alembic current
uv run --no-env-file --frozen alembic upgrade head
```

Do not hide migrations in application startup. Review
[Migration](runbooks/migration.md), [Backup and restore](runbooks/backup-restore.md),
and [Incident recovery](runbooks/incident-recovery.md) before changing or
restoring a shared local database.

## Runtime modes and safety

`NOTION_BACKEND=mock` is safe for the deterministic demo and does not write to
real Notion. `NOTION_BACKEND=live` requires a token and uses read-only Notion
reads plus the append-only writer. A real append still requires explicit human
acceptance through the review workflow.

The default demo is offline and uses controlled adapters:

```bash
uv run --no-env-file --frozen python scripts/run_mock_demo.py
```

Live canaries, Telegram sends, database restore drills, and release gates are
opt-in. See the [evaluation plan](07-evaluation-plan.md).

## Exposure and hardening

The unset authentication settings in `.env.example` support loopback-only
compatibility; they are not a safe public deployment profile. Before exposing
any endpoint, configure `API_BEARER_TOKEN`, verify Telegram requests with
`TELEGRAM_WEBHOOK_SECRET`, restrict `TELEGRAM_ALLOWED_CHAT_IDS`, and place the
service behind TLS and an access-controlled reverse proxy. Store secrets
outside Git and avoid including them in process output.

The repository does not currently supply production-grade TLS termination,
secret management, rate limiting, process supervision, scheduled PostgreSQL
backups, centralized durable logs, tracing, dashboards, or a time-series
backend. These are deployment responsibilities and must not be inferred from
the local Compose profile.
