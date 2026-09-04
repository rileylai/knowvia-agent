from __future__ import annotations

from functools import lru_cache
from typing import Optional

from fastapi import Depends, Header, HTTPException

from src.app.config import (
    NOTION_BACKEND_LIVE,
    NotionBackendConfigurationError,
    Settings,
    get_settings,
    normalize_notion_backend,
)
from src.db.readiness import SqlAlchemyReadinessProbe
from src.db.session import (
    SessionFactory,
    UnitOfWorkFactory,
    engine,
    get_db_session_factory,
)
from src.db.unit_of_work import SqlAlchemyUnitOfWork
from src.providers import (
    EmbeddingClient,
    OpenAIClient,
    OpenAIEmbeddingClient,
    ProviderRouter,
)
from src.services import (
    CostTracker,
    CostBudgetService,
    EmbeddingBatchLimits,
    EmbeddingBatchService,
    EmbeddingRetryPolicy,
    MetricsService,
    PromptTemplateLoader,
    ReadinessService,
    TrustBoundaryError,
    TrustBoundaryService,
    WorkflowObservabilityService,
)
from src.tools import (
    DEFAULT_MOCK_NOTION_DATA_DIR,
    ImageOCRTool,
    InMemoryNotionReaderClient,
    JSONMockNotionReaderClient,
    NotionReaderTool,
    NotionAPIReaderClient,
    NotionReaderClient,
    PDFParserTool,
    PyPDFParserClient,
    TesseractImageOCRParserClient,
    TrafilaturaURLArticleParserClient,
    ToolRegistry,
    URLArticleParserTool,
    YouTubeTranscriptAPIClient,
    YouTubeTranscriptTool,
)


def get_business_unit_of_work_factory(
    session_factory: SessionFactory = Depends(get_db_session_factory),
) -> UnitOfWorkFactory:
    return lambda: SqlAlchemyUnitOfWork(session_factory)


def get_readiness_service() -> ReadinessService:
    settings = get_settings()
    notion_backend = normalize_notion_backend(settings.notion_backend)
    return ReadinessService(
        probe=SqlAlchemyReadinessProbe(engine=engine),
        mode=settings.app_env,
        openai_configured=bool(settings.openai_api_key),
        notion_backend=notion_backend,
        notion_configured=(notion_backend == "mock" or bool(settings.notion_token)),
    )


@lru_cache(maxsize=1)
def get_queue_client() -> Optional["QueueClient"]:
    """Build the legacy queue adapter only when a legacy caller opts in."""

    settings = get_settings()
    if not settings.redis_url:
        return None

    from src.queue import QueueClient, RQQueueClient
    from redis import Redis

    return RQQueueClient(connection=Redis.from_url(settings.redis_url))


def get_trust_boundary() -> TrustBoundaryService:
    settings = get_settings()
    return TrustBoundaryService(
        api_bearer_token=settings.api_bearer_token,
        telegram_webhook_secret=settings.telegram_webhook_secret,
        telegram_allowed_chat_ids=settings.telegram_allowed_chat_ids,
    )


@lru_cache(maxsize=1)
def get_telegram_session_store() -> TelegramSessionStore:
    from src.services import (
        InMemoryTelegramSessionStore,
        RedisTelegramSessionStore,
        TelegramSessionStore,
    )

    settings = get_settings()
    if settings.redis_url:
        from redis import Redis

        return RedisTelegramSessionStore(
            redis_client=Redis.from_url(settings.redis_url)
        )
    return InMemoryTelegramSessionStore()


@lru_cache(maxsize=1)
def get_telegram_sync_session_store() -> TelegramSyncSessionStore:
    from src.services import (
        InMemoryTelegramSyncSessionStore,
        RedisTelegramSyncSessionStore,
        TelegramSyncSessionStore,
    )

    settings = get_settings()
    if settings.redis_url:
        from redis import Redis

        return RedisTelegramSyncSessionStore(redis_client=Redis.from_url(settings.redis_url))
    return InMemoryTelegramSyncSessionStore()


@lru_cache(maxsize=1)
def get_telegram_index_session_store() -> TelegramIndexSessionStore:
    from src.services import (
        InMemoryTelegramIndexSessionStore,
        RedisTelegramIndexSessionStore,
        TelegramIndexSessionStore,
    )

    settings = get_settings()
    if settings.redis_url:
        from redis import Redis

        return RedisTelegramIndexSessionStore(redis_client=Redis.from_url(settings.redis_url))
    return InMemoryTelegramIndexSessionStore()


def get_cost_budget_service() -> CostBudgetService:
    settings = get_settings()
    return CostBudgetService(
        daily_budget_usd=settings.max_daily_cost_usd,
        workflow_budget_usd=settings.max_workflow_cost_usd,
    )


def get_workflow_observability_service(
    session_factory: SessionFactory = Depends(get_db_session_factory),
    cost_budget_service: CostBudgetService = Depends(get_cost_budget_service),
) -> WorkflowObservabilityService:
    settings = get_settings()
    return WorkflowObservabilityService(
        session_factory,
        cost_budget_service=cost_budget_service,
        stale_after_seconds=settings.workflow_stale_after_seconds,
    )


def get_metrics_service(
    session_factory: SessionFactory = Depends(get_db_session_factory),
    cost_budget_service: CostBudgetService = Depends(get_cost_budget_service),
) -> MetricsService:
    settings = get_settings()
    return MetricsService(
        session_factory,
        cost_budget_service=cost_budget_service,
        stale_after_seconds=settings.workflow_stale_after_seconds,
    )


def require_api_bearer_token(
    authorization_header: Optional[str] = Header(default=None, alias="Authorization"),
    trust_boundary: TrustBoundaryService = Depends(get_trust_boundary),
) -> None:
    try:
        trust_boundary.require_api_bearer(authorization_header)
    except TrustBoundaryError as exc:
        raise HTTPException(
            status_code=exc.http_status_code,
            detail={
                "error_code": exc.error_code,
                "message": exc.message,
                "failure_reason": exc.failure_reason,
                "workflow_run_id": None,
            },
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def _build_mock_notion_clients(
    settings,
) -> NotionReaderClient:
    if settings.mock_notion_data_dir:
        notion_reader_client = JSONMockNotionReaderClient.from_directory(
            settings.mock_notion_data_dir
        )
    elif DEFAULT_MOCK_NOTION_DATA_DIR.is_dir():
        notion_reader_client = JSONMockNotionReaderClient.from_directory(
            DEFAULT_MOCK_NOTION_DATA_DIR
        )
    else:
        notion_reader_client = InMemoryNotionReaderClient(pages={})

    return notion_reader_client


def _build_notion_clients(
    settings,
) -> NotionReaderClient:
    backend = normalize_notion_backend(settings.notion_backend)
    if backend == NOTION_BACKEND_LIVE:
        if not settings.notion_token:
            raise NotionBackendConfigurationError(
                "NOTION_BACKEND=live requires NOTION_TOKEN"
            )
        return NotionAPIReaderClient(
            token=settings.notion_token,
            timeout_seconds=settings.notion_request_timeout_seconds,
            max_attempts=settings.notion_read_max_attempts,
            retry_base_seconds=settings.notion_read_retry_base_seconds,
            retry_max_seconds=settings.notion_read_retry_max_seconds,
        )
    return _build_mock_notion_clients(settings)


@lru_cache(maxsize=1)
def get_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    settings = get_settings()
    notion_reader_client = _build_notion_clients(settings)
    registry.register_tool(NotionReaderTool(notion_reader_client))
    registry.register_tool(PDFParserTool(PyPDFParserClient()))
    registry.register_tool(URLArticleParserTool(TrafilaturaURLArticleParserClient()))
    registry.register_tool(YouTubeTranscriptTool(YouTubeTranscriptAPIClient()))
    registry.register_tool(ImageOCRTool(TesseractImageOCRParserClient()))
    return registry


@lru_cache(maxsize=1)
def get_provider_router() -> ProviderRouter:
    router = ProviderRouter()
    settings = get_settings()
    if settings.openai_api_key:
        router.register_provider(OpenAIClient(api_key=settings.openai_api_key))
    return router


@lru_cache(maxsize=1)
def get_embedding_client() -> Optional[EmbeddingClient]:
    settings = get_settings()
    if not settings.openai_api_key:
        return None
    return OpenAIEmbeddingClient(
        api_key=settings.openai_api_key,
    )


def build_embedding_batch_service(
    embedding_client: Optional[EmbeddingClient],
    *,
    settings: Optional[Settings] = None,
) -> Optional[EmbeddingBatchService]:
    if embedding_client is None:
        return None
    selected_settings = settings or get_settings()
    return EmbeddingBatchService(
        embedding_client=embedding_client,
        model="text-embedding-3-small",
        dimensions=1536,
        limits=EmbeddingBatchLimits(
            max_inputs=selected_settings.embedding_batch_max_inputs,
            max_single_input_bytes=(
                selected_settings.embedding_batch_max_single_input_bytes
            ),
            max_single_input_tokens=(
                selected_settings.embedding_batch_max_single_input_token_estimate
            ),
            max_aggregate_bytes=(
                selected_settings.embedding_batch_max_aggregate_bytes
            ),
            max_aggregate_tokens=(
                selected_settings.embedding_batch_max_aggregate_token_estimate
            ),
        ),
        retry_policy=EmbeddingRetryPolicy(
            max_attempts=selected_settings.embedding_request_max_attempts,
            base_delay_seconds=selected_settings.embedding_retry_base_seconds,
            max_delay_seconds=selected_settings.embedding_retry_max_seconds,
        ),
    )


@lru_cache(maxsize=1)
def get_prompt_template_loader() -> PromptTemplateLoader:
    return PromptTemplateLoader()


@lru_cache(maxsize=1)
def get_cost_tracker() -> CostTracker:
    return CostTracker()
