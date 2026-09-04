# Backup and Restore Runbook

## Scope and safety

PostgreSQL/pgvector contains a rebuildable Notion index and durable application
records that Notion cannot reproduce, including sources, proposals, workflows,
and idempotency ledgers. Current Notion content remains authoritative for page
content. Never restore over an active database as the first validation. Stop
the API and worker before replacing a local target.

- Keep archives outside Git and protect them with filesystem permissions.
- Take a fresh backup before restore or migration.
- Do not use `pg_restore --clean` against an active or unknown database.
- Do not edit Notion while database state is being restored.
- Do not expose database URLs, passwords, tokens, private content, or raw
  driver errors in logs or reports.

## Create a backup

Use a password manager, `.pgpass`, or a process environment for credentials:

```bash
BACKUP_FILE="./data/backups/learnloop-YYYYMMDD-HHMMSS.dump"
mkdir -p ./data/backups
pg_dump --format=custom --no-owner \
  --file "$BACKUP_FILE" \
  --dbname "$POSTGRES_DATABASE_NAME"
pg_restore --list "$BACKUP_FILE" >/dev/null
```

The archive listing validates the file format. Keep the archive until restore
verification is complete.

## Disposable restore drill

The project drill creates disposable database names, applies real Alembic
migrations, restores a sentinel, verifies the migration table, and removes
only the generated databases:

```bash
uv run --no-env-file --frozen python \
  scripts/postgres_restore_drill.py --json
```

For a live drill, provide an administrator URL for a disposable PostgreSQL
server and an explicit disposable confirmation:

```bash
uv run --no-env-file --frozen python \
  scripts/postgres_restore_drill.py \
  --run \
  --confirm-disposable-target DISPOSABLE \
  --admin-database-url "$LEARNLOOP_RESTORE_DRILL_ADMIN_DATABASE_URL" \
  --json
```

If cleanup fails, do not reuse generated database names until their exact
scope and connections are verified.

## Restore an application database

1. Stop API and worker processes.
2. Record safe workflow, migration, and readiness status.
3. Take a fresh backup of the current database.
4. Restore into a new database:

   ```bash
   createdb "$RESTORE_DATABASE_NAME"
   pg_restore --exit-on-error --no-owner \
     --dbname "$RESTORE_DATABASE_NAME" "$BACKUP_FILE"
   uv run --no-env-file --frozen alembic upgrade head
   ```

5. Verify `alembic current`, database connectivity, pgvector, and `/ready`
   against the restored target.
6. Rebuild derived state from current Notion with
   `POST /api/notion/index/full`. Use incremental sync only when the affected
   page set is known and the restore is otherwise complete.
7. Verify a scoped QA citation and production-RAG exclusion before resuming
   accepted appends.

Record only archive format, drill checks, migration status, readiness, bounded
counts, citation count, operator, and timestamp.
