import json
import logging

from src.observability.logger import JsonFormatter


def test_json_formatter_emits_structured_payload() -> None:
    logger = logging.getLogger("tests.logger")
    record = logger.makeRecord(
        name="tests.logger",
        level=logging.INFO,
        fn=__file__,
        lno=10,
        msg="request_completed",
        args=(),
        exc_info=None,
        extra={
            "workflow_id": "wf-123",
            "path": "/health",
            "method": "GET",
            "status_code": 200,
            "duration_ms": 1.23,
        },
    )

    payload = json.loads(JsonFormatter().format(record))

    assert payload["level"] == "INFO"
    assert payload["event"] == "request_completed"
    assert payload["workflow_id"] == "wf-123"
    assert payload["path"] == "/health"
    assert payload["method"] == "GET"
    assert payload["status_code"] == 200
    assert payload["duration_ms"] == 1.23
    assert "timestamp" in payload


def test_json_formatter_redacts_secrets_and_private_text_in_event_message() -> None:
    logger = logging.getLogger("tests.logger")
    record = logger.makeRecord(
        name="tests.logger",
        level=logging.ERROR,
        fn=__file__,
        lno=42,
        msg=(
            "Telegram request failed: https://api.telegram.org/bot123456:ABC/sendMessage "
            "Authorization=Bearer sk-test-secret raw_text='private note body'"
        ),
        args=(),
        exc_info=None,
        extra={},
    )

    payload = json.loads(JsonFormatter().format(record))

    assert payload["event"] == (
        "Telegram request failed: https://api.telegram.org/bot[REDACTED]/sendMessage "
        "Authorization=[REDACTED] raw_text=[REDACTED_PRIVATE_TEXT]"
    )
    assert "sk-test-secret" not in payload["event"]
    assert "private note body" not in payload["event"]


def test_json_formatter_ignores_unapproved_extra_fields() -> None:
    logger = logging.getLogger("tests.logger")
    record = logger.makeRecord(
        name="tests.logger",
        level=logging.INFO,
        fn=__file__,
        lno=64,
        msg="request_completed",
        args=(),
        exc_info=None,
        extra={
            "workflow_id": "wf-321",
            "path": "/api/qa",
            "method": "POST",
            "status_code": 200,
            "duration_ms": 9.87,
            "api_key": "secret-value",
            "raw_text": "private note body",
        },
    )

    payload = json.loads(JsonFormatter().format(record))

    assert payload["workflow_id"] == "wf-321"
    assert payload["path"] == "/api/qa"
    assert "api_key" not in payload
    assert "raw_text" not in payload
