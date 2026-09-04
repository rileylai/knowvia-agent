# Runbooks

- [Backup and restore](backup-restore.md): PostgreSQL/pgvector backup and
  disposable restore verification.
- [Incident recovery](incident-recovery.md): database divergence, uncertain
  Notion append, stale workflow, and Telegram delivery recovery.
- [Migration](migration.md): Alembic application and verification policy.

All procedures are dry-run first where a script supports it. Confirm exact
targets before any command that can create, restore, append, or reconcile data.
