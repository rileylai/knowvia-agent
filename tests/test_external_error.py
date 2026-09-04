from src.observability.external_error import (
    ExternalErrorCategory,
    classify_http_error,
    extract_provider_error_category,
    parse_retry_after_seconds,
)


def test_http_error_matrix_is_deterministic() -> None:
    cases = [
        (400, ExternalErrorCategory.REQUEST_INVALID, False),
        (401, ExternalErrorCategory.AUTHENTICATION_FAILED, False),
        (403, ExternalErrorCategory.AUTHENTICATION_FAILED, False),
        (408, ExternalErrorCategory.REQUEST_TIMEOUT, True),
        (413, ExternalErrorCategory.REQUEST_TOO_LARGE, False),
        (422, ExternalErrorCategory.VALIDATION_FAILED, False),
        (429, ExternalErrorCategory.RATE_LIMITED, True),
        (500, ExternalErrorCategory.UPSTREAM_SERVER_ERROR, True),
        (502, ExternalErrorCategory.UPSTREAM_SERVER_ERROR, True),
        (503, ExternalErrorCategory.UPSTREAM_SERVER_ERROR, True),
        (504, ExternalErrorCategory.UPSTREAM_SERVER_ERROR, True),
        (501, ExternalErrorCategory.UPSTREAM_SERVER_ERROR, False),
        (418, ExternalErrorCategory.UNKNOWN_HTTP_ERROR, False),
    ]

    for status, category, retryable in cases:
        diagnostic = classify_http_error(status_code=status)
        assert diagnostic.http_status == status
        assert diagnostic.category == category
        assert diagnostic.retryable is retryable


def test_allowlisted_provider_categories_override_generic_http_400() -> None:
    size_payload = {
        "error": {
            "code": "context_length_exceeded",
            "message": "raw private provider message",
        }
    }
    validation_payload = {
        "error": {
            "type": "invalid_dimensions",
            "message": "raw private provider message",
        }
    }

    assert extract_provider_error_category(size_payload) == (
        ExternalErrorCategory.REQUEST_TOO_LARGE
    )
    assert extract_provider_error_category(validation_payload) == (
        ExternalErrorCategory.VALIDATION_FAILED
    )
    assert extract_provider_error_category(
        {"error": {"code": "unknown_private_code", "message": "private"}}
    ) is None


def test_explicit_http_status_takes_precedence_over_provider_category() -> None:
    diagnostic = classify_http_error(
        status_code=401,
        provider_category=ExternalErrorCategory.VALIDATION_FAILED,
    )

    assert diagnostic.category == ExternalErrorCategory.AUTHENTICATION_FAILED
    assert diagnostic.retryable is False


def test_retry_after_accepts_only_bounded_numeric_seconds() -> None:
    assert parse_retry_after_seconds("30") == 30
    assert parse_retry_after_seconds("0") == 0
    assert parse_retry_after_seconds("3600") == 3600
    assert parse_retry_after_seconds("3601") is None
    assert parse_retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT") is None
    assert parse_retry_after_seconds("private") is None
    assert parse_retry_after_seconds(None) is None
