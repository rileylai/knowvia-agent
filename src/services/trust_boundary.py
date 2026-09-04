from __future__ import annotations

import hmac
from typing import FrozenSet, Optional


class TrustBoundaryError(Exception):
    def __init__(
        self,
        *,
        error_code: str,
        message: str,
        http_status_code: int,
        failure_reason: str,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.http_status_code = http_status_code
        self.failure_reason = failure_reason


class TrustBoundaryService:
    """Apply deterministic caller authentication and authorization rules."""

    def __init__(
        self,
        *,
        api_bearer_token: Optional[str] = None,
        telegram_webhook_secret: Optional[str] = None,
        telegram_allowed_chat_ids: FrozenSet[str] = frozenset(),
    ) -> None:
        self._api_bearer_token = api_bearer_token
        self._telegram_webhook_secret = telegram_webhook_secret
        self._telegram_allowed_chat_ids = telegram_allowed_chat_ids

    def require_api_bearer(self, authorization_header: Optional[str]) -> None:
        if self._api_bearer_token is None:
            return

        scheme, separator, supplied_token = (authorization_header or "").partition(" ")
        if (
            scheme.lower() != "bearer"
            or not separator
            or not supplied_token.strip()
            or not hmac.compare_digest(
                supplied_token.strip(), self._api_bearer_token
            )
        ):
            raise TrustBoundaryError(
                error_code="API_UNAUTHORIZED",
                message="A valid API bearer token is required",
                http_status_code=401,
                failure_reason="AUTHENTICATION_FAILED",
            )

    def require_telegram_webhook_secret(
        self,
        supplied_secret: Optional[str],
    ) -> None:
        if self._telegram_webhook_secret is None:
            return
        if supplied_secret is None or not hmac.compare_digest(
            supplied_secret, self._telegram_webhook_secret
        ):
            raise TrustBoundaryError(
                error_code="TELEGRAM_WEBHOOK_FORBIDDEN",
                message="Telegram webhook secret is invalid",
                http_status_code=403,
                failure_reason="AUTHENTICATION_FAILED",
            )

    def require_allowed_telegram_chat(self, chat_id: Optional[str]) -> None:
        if not self._telegram_allowed_chat_ids:
            return
        normalized_chat_id = (chat_id or "").strip()
        if normalized_chat_id not in self._telegram_allowed_chat_ids:
            raise TrustBoundaryError(
                error_code="TELEGRAM_CHAT_NOT_ALLOWED",
                message="Telegram chat is not allowed",
                http_status_code=403,
                failure_reason="AUTHORIZATION_FAILED",
            )
