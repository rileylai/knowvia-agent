# ADR-0005: Typed Telegram Review Callback Routing

## Status

Accepted

## Context

Telegram inline buttons carry opaque `ll:<token>` data. A free-form action is
not sufficient to distinguish review callbacks from page-picker navigation,
especially when mappings outlive a process restart.

## Decision

- Persist `callback_kind` for review, picker, and operator callback families.
- Normalize legacy mappings from their allowlisted action; reject unknown or
  mismatched mappings.
- Dispatch review actions before generic picker handling.
- Route Accept and Reject through `TelegramReviewOrchestrator`; Change target
  opens the review target picker.
- Resolve review targets from the durable change request and indexed page, not
  from a canary-only setting.
- Keep Telegram `update_id` idempotency as the outer retry boundary.

## Consequences

Fresh and legacy review buttons use one deterministic path. Opaque callback
data remains free of page ids, source text, secrets, and raw image content.
