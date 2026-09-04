from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict

from src.observability.redaction import sanitize_sensitive_text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "event": sanitize_sensitive_text(record.getMessage()),
        }

        for key in (
            "workflow_id",
            "path",
            "method",
            "status_code",
            "duration_ms",
            "audit_action",
            "audit_status",
            "update_id",
            "failure_reason",
            "business_status",
            "callback_ack_status",
            "preview_delivery_status",
        ):
            value = getattr(record, key, None)
            if value is not None:
                if isinstance(value, str):
                    value = sanitize_sensitive_text(value)
                payload[key] = value

        return json.dumps(payload, ensure_ascii=True)


def _parse_log_level(log_level: str) -> int:
    level = getattr(logging, log_level.upper(), None)
    if isinstance(level, int):
        return level
    return logging.INFO


def configure_logging(log_level: str) -> None:
    root_logger = logging.getLogger()
    root_logger.setLevel(_parse_log_level(log_level))

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root_logger.handlers = [handler]


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
