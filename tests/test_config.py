import pytest

from src.app.config import get_settings


def _clear_settings_cache() -> None:
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _isolate_settings_cache():
    _clear_settings_cache()
    yield
    _clear_settings_cache()


def test_settings_load_without_optional_env(monkeypatch) -> None:
    env_keys = [
        "APP_ENV",
        "LOG_LEVEL",
        "DATABASE_URL",
        "REDIS_URL",
        "MOCK_NOTION_DATA_DIR",
        "NOTION_BACKEND",
        "NOTION_TOKEN",
        "NOTION_REQUEST_TIMEOUT_SECONDS",
        "NOTION_READ_MAX_ATTEMPTS",
        "NOTION_READ_RETRY_BASE_SECONDS",
        "NOTION_READ_RETRY_MAX_SECONDS",
        "OPENAI_API_KEY",
        "EMBEDDING_BATCH_MAX_INPUTS",
        "EMBEDDING_BATCH_MAX_SINGLE_INPUT_BYTES",
        "EMBEDDING_BATCH_MAX_SINGLE_INPUT_TOKEN_ESTIMATE",
        "EMBEDDING_BATCH_MAX_AGGREGATE_BYTES",
        "EMBEDDING_BATCH_MAX_AGGREGATE_TOKEN_ESTIMATE",
        "EMBEDDING_REQUEST_MAX_ATTEMPTS",
        "EMBEDDING_RETRY_BASE_SECONDS",
        "EMBEDDING_RETRY_MAX_SECONDS",
        "TELEGRAM_BOT_TOKEN",
        "API_BEARER_TOKEN",
        "TELEGRAM_WEBHOOK_SECRET",
        "TELEGRAM_ALLOWED_CHAT_IDS",
        "MAX_WORKFLOW_COST_USD",
        "MAX_DAILY_COST_USD",
        "WORKFLOW_STALE_AFTER_SECONDS",
        "TELEGRAM_JOB_TIMEOUT_SECONDS",
        "TELEGRAM_REVIEW_JOB_TIMEOUT_SECONDS",
        "TELEGRAM_INDEXING_JOB_TIMEOUT_SECONDS",
    ]
    for key in env_keys:
        monkeypatch.delenv(key, raising=False)

    _clear_settings_cache()
    settings = get_settings()

    assert settings.app_env == "local"
    assert settings.log_level == "INFO"
    assert settings.database_url is None
    assert settings.redis_url is None
    assert settings.mock_notion_data_dir is None
    assert settings.notion_backend == "mock"
    assert settings.notion_token is None
    assert settings.notion_request_timeout_seconds == 30
    assert settings.notion_read_max_attempts == 3
    assert settings.notion_read_retry_base_seconds == 1
    assert settings.notion_read_retry_max_seconds == 30
    assert settings.openai_api_key is None
    assert settings.embedding_batch_max_inputs == 512
    assert settings.embedding_batch_max_single_input_bytes == 32768
    assert settings.embedding_batch_max_single_input_token_estimate == 8000
    assert settings.embedding_batch_max_aggregate_bytes == 1000000
    assert settings.embedding_batch_max_aggregate_token_estimate == 250000
    assert settings.embedding_request_max_attempts == 3
    assert settings.embedding_retry_base_seconds == 1
    assert settings.embedding_retry_max_seconds == 30
    assert settings.telegram_bot_token is None
    assert settings.api_bearer_token is None
    assert settings.telegram_webhook_secret is None
    assert settings.telegram_allowed_chat_ids == frozenset()
    assert settings.max_workflow_cost_usd is None
    assert settings.max_daily_cost_usd is None
    assert settings.workflow_stale_after_seconds == 3600
    assert settings.telegram_job_timeout_seconds == 180
    assert settings.telegram_review_job_timeout_seconds == 10800
    assert settings.telegram_indexing_job_timeout_seconds == 10800


def test_settings_load_with_env_override(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost:5432/learnloop")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("MOCK_NOTION_DATA_DIR", "tests/fixtures/notion_pages")
    monkeypatch.setenv("NOTION_BACKEND", "live")
    monkeypatch.setenv("NOTION_TOKEN", "placeholder-notion-token")
    monkeypatch.setenv("NOTION_REQUEST_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("NOTION_READ_MAX_ATTEMPTS", "4")
    monkeypatch.setenv("NOTION_READ_RETRY_BASE_SECONDS", "2")
    monkeypatch.setenv("NOTION_READ_RETRY_MAX_SECONDS", "20")
    monkeypatch.setenv("OPENAI_API_KEY", "placeholder-openai-key")
    monkeypatch.setenv("EMBEDDING_BATCH_MAX_INPUTS", "256")
    monkeypatch.setenv("EMBEDDING_BATCH_MAX_SINGLE_INPUT_BYTES", "16384")
    monkeypatch.setenv("EMBEDDING_BATCH_MAX_SINGLE_INPUT_TOKEN_ESTIMATE", "4000")
    monkeypatch.setenv("EMBEDDING_BATCH_MAX_AGGREGATE_BYTES", "500000")
    monkeypatch.setenv("EMBEDDING_BATCH_MAX_AGGREGATE_TOKEN_ESTIMATE", "125000")
    monkeypatch.setenv("EMBEDDING_REQUEST_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("EMBEDDING_RETRY_BASE_SECONDS", "3")
    monkeypatch.setenv("EMBEDDING_RETRY_MAX_SECONDS", "15")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "placeholder-telegram-token")
    monkeypatch.setenv("API_BEARER_TOKEN", "placeholder-api-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "placeholder-webhook-secret")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "555, -100123, 555")
    monkeypatch.setenv("MAX_WORKFLOW_COST_USD", "0.25")
    monkeypatch.setenv("MAX_DAILY_COST_USD", "5")
    monkeypatch.setenv("WORKFLOW_STALE_AFTER_SECONDS", "900")
    monkeypatch.setenv("TELEGRAM_JOB_TIMEOUT_SECONDS", "240")
    monkeypatch.setenv("TELEGRAM_REVIEW_JOB_TIMEOUT_SECONDS", "7200")
    monkeypatch.setenv("TELEGRAM_INDEXING_JOB_TIMEOUT_SECONDS", "7200")

    _clear_settings_cache()
    settings = get_settings()

    assert settings.app_env == "test"
    assert settings.log_level == "DEBUG"
    assert settings.database_url == "postgresql://localhost:5432/learnloop"
    assert settings.redis_url == "redis://localhost:6379/0"
    assert settings.mock_notion_data_dir == "tests/fixtures/notion_pages"
    assert settings.notion_backend == "live"
    assert settings.notion_token == "placeholder-notion-token"
    assert settings.notion_request_timeout_seconds == 45
    assert settings.notion_read_max_attempts == 4
    assert settings.notion_read_retry_base_seconds == 2
    assert settings.notion_read_retry_max_seconds == 20
    assert settings.openai_api_key == "placeholder-openai-key"
    assert settings.embedding_batch_max_inputs == 256
    assert settings.embedding_batch_max_single_input_bytes == 16384
    assert settings.embedding_batch_max_single_input_token_estimate == 4000
    assert settings.embedding_batch_max_aggregate_bytes == 500000
    assert settings.embedding_batch_max_aggregate_token_estimate == 125000
    assert settings.embedding_request_max_attempts == 2
    assert settings.embedding_retry_base_seconds == 3
    assert settings.embedding_retry_max_seconds == 15
    assert settings.telegram_bot_token == "placeholder-telegram-token"
    assert settings.api_bearer_token == "placeholder-api-token"
    assert settings.telegram_webhook_secret == "placeholder-webhook-secret"
    assert settings.telegram_allowed_chat_ids == frozenset({"555", "-100123"})
    assert settings.max_workflow_cost_usd == 0.25
    assert settings.max_daily_cost_usd == 5.0
    assert settings.workflow_stale_after_seconds == 900
    assert settings.telegram_job_timeout_seconds == 240
    assert settings.telegram_review_job_timeout_seconds == 7200
    assert settings.telegram_indexing_job_timeout_seconds == 7200


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("NOTION_REQUEST_TIMEOUT_SECONDS", "nan"),
        ("NOTION_READ_MAX_ATTEMPTS", "0"),
        ("NOTION_READ_RETRY_BASE_SECONDS", "-1"),
        ("EMBEDDING_BATCH_MAX_INPUTS", "0"),
        ("EMBEDDING_BATCH_MAX_AGGREGATE_BYTES", "invalid"),
        ("EMBEDDING_REQUEST_MAX_ATTEMPTS", "-1"),
        ("EMBEDDING_RETRY_MAX_SECONDS", "inf"),
        ("TELEGRAM_JOB_TIMEOUT_SECONDS", "0"),
        ("TELEGRAM_REVIEW_JOB_TIMEOUT_SECONDS", "invalid"),
        ("TELEGRAM_INDEXING_JOB_TIMEOUT_SECONDS", "invalid"),
    ],
)
def test_step_97_numeric_settings_fail_closed(
    monkeypatch,
    name: str,
    value: str,
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError):
        get_settings()
