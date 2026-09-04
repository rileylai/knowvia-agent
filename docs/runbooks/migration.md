# Migration Runbook

## Policy

Alembic is the only supported schema migration path. Apply a migration to a
disposable or staging database before an active local database. Migrations are
not hidden in application startup.

Before applying a migration:

1. Confirm the exact database target and pause shared API/worker writes.
2. Take a backup using [the backup and restore runbook](backup-restore.md).
3. Inspect history and current revision:

   ```bash
   uv run --no-env-file --frozen alembic history
   uv run --no-env-file --frozen alembic current
   ```

4. Use the restore drill when the change affects data shape, indexes,
   extensions, or vector columns.

## Apply and verify

`DATABASE_URL` is read from the process environment; `.env` is not loaded
automatically.

```bash
uv run --no-env-file --frozen alembic upgrade head
uv run --no-env-file --frozen alembic current
uv run --no-env-file --frozen python scripts/preflight.py --profile api --json
curl --fail-with-body http://127.0.0.1:8000/ready
```

Verify current migrations, database connectivity, and pgvector. `/health` is
only a liveness check.

## Failure and rollback

- Preserve safe failure status and restore evidence when an upgrade fails.
- Do not blindly downgrade a live database. Restore the last verified backup
  into a new database instead.
- Use downgrade only for explicitly disposable local development targets.
- After restore or schema change, rebuild derived Notion page state before
  resuming accepted appends.
