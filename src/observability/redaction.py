from __future__ import annotations

import re

REDACTED_SECRET = "[REDACTED]"
REDACTED_PRIVATE_TEXT = "[REDACTED_PRIVATE_TEXT]"

_BEARER_TOKEN_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/\-=]+\b")
_TELEGRAM_BOT_URL_PATTERN = re.compile(
    r"(?i)(https://api\.telegram\.org/(?:file/)?bot)[^/\s]+"
)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)(?P<key>\b(?:openai_api_key|api_key|notion_token|telegram_bot_token|bot_token|authorization|api_bearer_token|telegram_webhook_secret)\b)"
    r"(?P<separator>\s*[:=]\s*)"
    r"(?P<value>Bearer\s+[^\s,}]+|\"[^\"]*\"|'[^']*'|[^,\s}]+)"
)
_PRIVATE_TEXT_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)(?P<key>\b(?:raw_text|source_text)\b)"
    r"(?P<separator>\s*[:=]\s*)"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^,\n}]+)"
)


def sanitize_sensitive_text(value: str) -> str:
    sanitized = _TELEGRAM_BOT_URL_PATTERN.sub(r"\1[REDACTED]", value)
    sanitized = _BEARER_TOKEN_PATTERN.sub("Bearer [REDACTED]", sanitized)
    sanitized = _SECRET_ASSIGNMENT_PATTERN.sub(_replace_secret_assignment, sanitized)
    sanitized = _PRIVATE_TEXT_ASSIGNMENT_PATTERN.sub(
        _replace_private_text_assignment,
        sanitized,
    )
    return sanitized


def _replace_secret_assignment(match: re.Match[str]) -> str:
    key = match.group("key")
    separator = match.group("separator")
    return f"{key}{separator}{REDACTED_SECRET}"


def _replace_private_text_assignment(match: re.Match[str]) -> str:
    key = match.group("key")
    separator = match.group("separator")
    return f"{key}{separator}{REDACTED_PRIVATE_TEXT}"
