from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional


class ExternalErrorCategory(str, Enum):
    TIMEOUT = "timeout"
    TRANSPORT_UNAVAILABLE = "transport_unavailable"
    REQUEST_INVALID = "request_invalid"
    AUTHENTICATION_FAILED = "authentication_failed"
    REQUEST_TIMEOUT = "request_timeout"
    REQUEST_TOO_LARGE = "request_too_large"
    VALIDATION_FAILED = "validation_failed"
    RATE_LIMITED = "rate_limited"
    UPSTREAM_SERVER_ERROR = "upstream_server_error"
    RESPONSE_INVALID = "response_invalid"
    UNKNOWN_HTTP_ERROR = "unknown_http_error"


@dataclass(frozen=True)
class ExternalErrorDiagnostic:
    category: ExternalErrorCategory
    retryable: bool
    http_status: Optional[int] = None
    retry_after_seconds: Optional[int] = None


_RETRYABLE_SERVER_STATUSES = {500, 502, 503, 504}
_REQUEST_TOO_LARGE_PROVIDER_VALUES = {
    "context_length_exceeded",
    "input_too_large",
    "max_tokens_per_request",
    "request_too_large",
}
_VALIDATION_PROVIDER_VALUES = {
    "invalid_dimensions",
    "invalid_model",
    "model_not_found",
    "unsupported_dimensions",
}
_MAX_RETRY_AFTER_SECONDS = 3600


def classify_http_error(
    *,
    status_code: int,
    provider_category: Optional[ExternalErrorCategory] = None,
    retry_after_seconds: Optional[int] = None,
) -> ExternalErrorDiagnostic:
    if status_code == 400 and provider_category in {
        ExternalErrorCategory.REQUEST_TOO_LARGE,
        ExternalErrorCategory.VALIDATION_FAILED,
    }:
        category = provider_category
        retryable = False
    elif status_code == 400:
        category = ExternalErrorCategory.REQUEST_INVALID
        retryable = False
    elif status_code in {401, 403}:
        category = ExternalErrorCategory.AUTHENTICATION_FAILED
        retryable = False
    elif status_code == 408:
        category = ExternalErrorCategory.REQUEST_TIMEOUT
        retryable = True
    elif status_code == 413:
        category = ExternalErrorCategory.REQUEST_TOO_LARGE
        retryable = False
    elif status_code == 422:
        category = ExternalErrorCategory.VALIDATION_FAILED
        retryable = False
    elif status_code == 429:
        category = ExternalErrorCategory.RATE_LIMITED
        retryable = True
    elif 500 <= status_code <= 599:
        category = ExternalErrorCategory.UPSTREAM_SERVER_ERROR
        retryable = status_code in _RETRYABLE_SERVER_STATUSES
    else:
        category = ExternalErrorCategory.UNKNOWN_HTTP_ERROR
        retryable = False

    safe_retry_after = (
        retry_after_seconds
        if category == ExternalErrorCategory.RATE_LIMITED
        else None
    )
    return ExternalErrorDiagnostic(
        http_status=status_code,
        category=category,
        retryable=retryable,
        retry_after_seconds=safe_retry_after,
    )


def extract_provider_error_category(
    payload: Optional[Mapping[str, Any]],
) -> Optional[ExternalErrorCategory]:
    if not isinstance(payload, Mapping):
        return None
    error_payload = payload.get("error")
    if not isinstance(error_payload, Mapping):
        return None

    values = []
    for key in ("code", "type"):
        raw_value = error_payload.get(key)
        if isinstance(raw_value, str):
            values.append(raw_value.strip().lower())

    if any(value in _REQUEST_TOO_LARGE_PROVIDER_VALUES for value in values):
        return ExternalErrorCategory.REQUEST_TOO_LARGE
    if any(value in _VALIDATION_PROVIDER_VALUES for value in values):
        return ExternalErrorCategory.VALIDATION_FAILED
    return None


def parse_retry_after_seconds(value: Optional[str]) -> Optional[int]:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized.isdigit():
        return None
    seconds = int(normalized)
    if not 0 <= seconds <= _MAX_RETRY_AFTER_SECONDS:
        return None
    return seconds
