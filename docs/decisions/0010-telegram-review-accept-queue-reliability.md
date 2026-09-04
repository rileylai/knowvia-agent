# ADR-0010: Telegram Review Accept Queue Reliability

## Status

Accepted

## Context

Telegram review Accept runs append verification and complete target-page
re-indexing inside the existing generic webhook worker. That work can exceed
the ordinary `TELEGRAM_JOB_TIMEOUT_SECONDS` bound. The existing RQ timeout
classifier already maps `JobTimeoutException` to the neutral
`InfrastructureExecutionTimeout`, but review orchestration previously caught
that signal as an unknown exception and recorded `REVIEW_WORKFLOW_FAILED` with
`UNKNOWN_ERROR`.

## Decision

- Keep review Accept inside the existing module-level generic Telegram webhook
  callable and preserve the Telegram update ledger and callback claim
  boundaries.
- Add `TELEGRAM_REVIEW_JOB_TIMEOUT_SECONDS`, default `10800`.
- Select the review timeout at enqueue time only for a typed review Accept
  callback or `/accept` command. Ordinary commands, Reject, Change Target, and
  read-only callbacks continue to use `TELEGRAM_JOB_TIMEOUT_SECONDS`.
- Translate the neutral infrastructure timeout to `QUEUE_JOB_TIMEOUT` in the
  review workflow and mark the target-page indexing workflow failed with the
  same safe reason.
- Preserve the existing RQ retry policy and append recovery contract. A
  timeout leaves the Change Request pending when final PostgreSQL commit has
  not completed; recovery must reconcile `change-request-<id>` before retrying.
- Keep callback acknowledgement status independent from business status.

## Consequences

Ordinary Telegram jobs remain bounded at their existing timeout while an
accepted supplement can complete a large target-page re-index. A timeout is
operator-visible as `QUEUE_JOB_TIMEOUT` rather than a Notion or unknown review
failure. No dedicated Accept worker or broad queue redesign is introduced.
