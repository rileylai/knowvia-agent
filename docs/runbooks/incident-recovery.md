# Incident Recovery Runbook

## First response

Pause API and worker mutations while database state, workflow outcome, or
append identity is uncertain. Record only workflow references, status,
failure reasons, migration/readiness checks, bounded counts, and whether the
affected Notion page was manually edited.

Do not capture secrets, connection URLs, page content, proposal text, or raw
external exception bodies.

## Recovery checklist

Generate a deterministic read-only checklist first:

```bash
uv run --no-env-file --frozen python \
  scripts/notion_db_recovery_drill.py --json
```

### PostgreSQL was restored

1. Verify migrations, `/ready`, and pgvector against the restored target.
2. Preserve restored source documents, change requests, workflow runs, and
   idempotency ledgers; they cannot be rebuilt from Notion.
3. Treat only the Notion page/block/chunk/vector index as rebuildable state,
   not authoritative note content.
4. Run full Notion indexing; do not append during the rebuild.
5. Verify scoped citations and exclusion of pending/rejected content.

### A user changed Notion

1. Treat current Notion content as authoritative.
2. Run page-scoped incremental indexing for known affected page ids.
3. Use full indexing when the affected page set is unknown.
4. Never repair divergence by editing, deleting, moving, or manually appending
   Notion blocks from the application database.

### An append result is uncertain

1. Read the target page and search for `change-request-<id>`.
2. If present, run page-level indexing and reconcile the workflow after it
   succeeds.
3. If absent, keep the request unresolved and retry only through the existing
   human accept flow after confirming target and approval.
4. If identity visibility is unavailable, stop; do not guess or retry.

### A workflow is stale or audit reconciliation failed

1. Inspect protected workflow detail and confirm business outcome from safe
   external evidence.
2. Run `scripts/reconcile_workflow.py` without `--apply`.
3. Apply only a known terminal outcome. The command never reruns business work.
4. Leave the workflow unresolved when the outcome is unknown.

### Telegram business committed but preview delivery failed

Do not upload again or rerun OCR, LLM generation, source persistence, or
proposal creation. Inspect the existing outcome first:

```bash
uv run --no-env-file --frozen python \
  scripts/reconcile_telegram_outcome.py \
  --update-id <id> \
  --workflow-id <id> \
  --source-document-id <id> \
  --change-request-id <id> \
  --action resend-preview \
  --json
```

The command is dry-run unless `--apply` is supplied. Apply only after the
ledger, workflow, source, pending request, source link, and target page agree.
If any identity is uncertain, stop and leave rows unchanged.

## Resume criteria

Resume mutations only after migrations and readiness pass, Notion-derived page
state is current, any append identity is resolved, scoped QA returns a current
Notion citation, and pending/rejected content remains outside production RAG.

The MVP does not provide automatic incident remediation or continuous Notion
sync.
