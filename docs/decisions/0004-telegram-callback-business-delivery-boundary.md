# ADR-0004: Separate Telegram Callback Acknowledgement from Business Delivery

## Status

Accepted

## Context

Telegram callback acknowledgement is a user-interface side effect. It can
fail independently from OCR, proposal creation, review, or preview delivery.
Treating it as the business transaction can incorrectly mark committed work as
failed and make a retry ambiguous.

## Decision

- Validate callback ownership and session state before business work.
- Call `answerCallbackQuery` before long OCR, provider, or proposal work.
- Record acknowledgement status separately and classify delivery failure as
  `TELEGRAM_CALLBACK_ACK_FAILED`.
- Commit source/proposal business state before preview delivery.
- Record preview delivery separately; a failure uses
  `TELEGRAM_PREVIEW_DELIVERY_FAILED` and keeps the pending proposal recoverable.
- Use the Telegram update ledger and upload-session claims as idempotency
  boundaries so retries do not repeat business work.
- Persist only safe metadata.

## Consequences

Business success, callback acknowledgement, and preview delivery can be
diagnosed independently. Recovery can resend an existing preview without
rerunning OCR, the LLM, or proposal persistence.
