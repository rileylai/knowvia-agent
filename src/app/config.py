from __future__ import annotations

import os
import math
from functools import lru_cache
from typing import FrozenSet, Optional

from pydantic import BaseModel, Field


NOTION_BACKEND_MOCK = "mock"
NOTION_BACKEND_LIVE = "live"
SUPPORTED_NOTION_BACKENDS = frozenset(
    {NOTION_BACKEND_MOCK, NOTION_BACKEND_LIVE}
)


class NotionBackendConfigurationError(ValueError):
    """Raised when Notion backend selection cannot be used safely."""


def _read_optional_env(name: str) -> Optional[str]:
    value = os.getenv(name)
    if value is None:
        return None

    stripped = value.strip()
    return stripped or None


def _read_csv_env(name: str) -> FrozenSet[str]:
    value = _read_optional_env(name)
    if value is None:
        return frozenset()
    return frozenset(item.strip() for item in value.split(",") if item.strip())


def _read_optional_positive_float_env(name: str) -> Optional[float]:
    value = _read_optional_env(name)
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive finite number") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return parsed


def _read_optional_positive_int_env(name: str, default: int) -> int:
    value = _read_optional_env(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


class Settings(BaseModel):
    app_env: str = Field(default="local")
    log_level: str = Field(default="INFO")
    database_url: Optional[str] = None
    redis_url: Optional[str] = None
    mock_notion_data_dir: Optional[str] = None
    notion_backend: str = Field(default=NOTION_BACKEND_MOCK)
    notion_token: Optional[str] = None
    notion_request_timeout_seconds: float = 30.0
    notion_read_max_attempts: int = 3
    notion_read_retry_base_seconds: float = 1.0
    notion_read_retry_max_seconds: float = 30.0
    openai_api_key: Optional[str] = None
    embedding_batch_max_inputs: int = 512
    embedding_batch_max_single_input_bytes: int = 32768
    embedding_batch_max_single_input_token_estimate: int = 8000
    embedding_batch_max_aggregate_bytes: int = 1000000
    embedding_batch_max_aggregate_token_estimate: int = 250000
    embedding_request_max_attempts: int = 3
    embedding_retry_base_seconds: float = 1.0
    embedding_retry_max_seconds: float = 30.0
    telegram_bot_token: Optional[str] = None
    api_bearer_token: Optional[str] = None
    telegram_webhook_secret: Optional[str] = None
    telegram_allowed_chat_ids: FrozenSet[str] = Field(default_factory=frozenset)
    max_workflow_cost_usd: Optional[float] = None
    max_daily_cost_usd: Optional[float] = None
    workflow_stale_after_seconds: int = 3600
    conversation_context_message_limit: int = 6
    conversation_context_token_budget: int = 2048
    telegram_job_timeout_seconds: int = 180
    telegram_review_job_timeout_seconds: int = 10800
    telegram_indexing_job_timeout_seconds: int = 10800

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            app_env=os.getenv("APP_ENV", "local"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            database_url=_read_optional_env("DATABASE_URL"),
            redis_url=_read_optional_env("REDIS_URL"),
            mock_notion_data_dir=_read_optional_env("MOCK_NOTION_DATA_DIR"),
            notion_backend=_read_optional_env("NOTION_BACKEND")
            or NOTION_BACKEND_MOCK,
            notion_token=_read_optional_env("NOTION_TOKEN"),
            notion_request_timeout_seconds=(
                _read_optional_positive_float_env(
                    "NOTION_REQUEST_TIMEOUT_SECONDS"
                )
                or 30.0
            ),
            notion_read_max_attempts=_read_optional_positive_int_env(
                "NOTION_READ_MAX_ATTEMPTS",
                default=3,
            ),
            notion_read_retry_base_seconds=(
                _read_optional_positive_float_env(
                    "NOTION_READ_RETRY_BASE_SECONDS"
                )
                or 1.0
            ),
            notion_read_retry_max_seconds=(
                _read_optional_positive_float_env(
                    "NOTION_READ_RETRY_MAX_SECONDS"
                )
                or 30.0
            ),
            openai_api_key=_read_optional_env("OPENAI_API_KEY"),
            embedding_batch_max_inputs=_read_optional_positive_int_env(
                "EMBEDDING_BATCH_MAX_INPUTS",
                default=512,
            ),
            embedding_batch_max_single_input_bytes=(
                _read_optional_positive_int_env(
                    "EMBEDDING_BATCH_MAX_SINGLE_INPUT_BYTES",
                    default=32768,
                )
            ),
            embedding_batch_max_single_input_token_estimate=(
                _read_optional_positive_int_env(
                    "EMBEDDING_BATCH_MAX_SINGLE_INPUT_TOKEN_ESTIMATE",
                    default=8000,
                )
            ),
            embedding_batch_max_aggregate_bytes=(
                _read_optional_positive_int_env(
                    "EMBEDDING_BATCH_MAX_AGGREGATE_BYTES",
                    default=1000000,
                )
            ),
            embedding_batch_max_aggregate_token_estimate=(
                _read_optional_positive_int_env(
                    "EMBEDDING_BATCH_MAX_AGGREGATE_TOKEN_ESTIMATE",
                    default=250000,
                )
            ),
            embedding_request_max_attempts=_read_optional_positive_int_env(
                "EMBEDDING_REQUEST_MAX_ATTEMPTS",
                default=3,
            ),
            embedding_retry_base_seconds=(
                _read_optional_positive_float_env(
                    "EMBEDDING_RETRY_BASE_SECONDS"
                )
                or 1.0
            ),
            embedding_retry_max_seconds=(
                _read_optional_positive_float_env(
                    "EMBEDDING_RETRY_MAX_SECONDS"
                )
                or 30.0
            ),
            telegram_bot_token=_read_optional_env("TELEGRAM_BOT_TOKEN"),
            api_bearer_token=_read_optional_env("API_BEARER_TOKEN"),
            telegram_webhook_secret=_read_optional_env("TELEGRAM_WEBHOOK_SECRET"),
            telegram_allowed_chat_ids=_read_csv_env("TELEGRAM_ALLOWED_CHAT_IDS"),
            max_workflow_cost_usd=_read_optional_positive_float_env(
                "MAX_WORKFLOW_COST_USD"
            ),
            max_daily_cost_usd=_read_optional_positive_float_env(
                "MAX_DAILY_COST_USD"
            ),
            workflow_stale_after_seconds=_read_optional_positive_int_env(
                "WORKFLOW_STALE_AFTER_SECONDS",
                default=3600,
            ),
            conversation_context_message_limit=_read_optional_positive_int_env(
                "CONVERSATION_CONTEXT_MESSAGE_LIMIT",
                default=6,
            ),
            conversation_context_token_budget=_read_optional_positive_int_env(
                "CONVERSATION_CONTEXT_TOKEN_BUDGET",
                default=2048,
            ),
            telegram_job_timeout_seconds=_read_optional_positive_int_env(
                "TELEGRAM_JOB_TIMEOUT_SECONDS",
                default=180,
            ),
            telegram_review_job_timeout_seconds=_read_optional_positive_int_env(
                "TELEGRAM_REVIEW_JOB_TIMEOUT_SECONDS",
                default=10800,
            ),
            telegram_indexing_job_timeout_seconds=_read_optional_positive_int_env(
                "TELEGRAM_INDEXING_JOB_TIMEOUT_SECONDS",
                default=10800,
            ),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()


def normalize_notion_backend(value: Optional[str]) -> str:
    normalized = (value or NOTION_BACKEND_MOCK).strip().lower()
    if normalized not in SUPPORTED_NOTION_BACKENDS:
        supported = ", ".join((NOTION_BACKEND_MOCK, NOTION_BACKEND_LIVE))
        raise NotionBackendConfigurationError(
            f"NOTION_BACKEND must be one of: {supported}"
        )
    return normalized
