from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from src.app.config import get_settings, normalize_notion_backend
from src.app.dependencies import build_embedding_batch_service
from src.db.readiness import SqlAlchemyReadinessProbe
from src.db.session import engine
from src.db.session import SessionFactory, UnitOfWorkFactory
from src.orchestrators import (
    DocumentIngestionOrchestrator,
    ImageOCRIngestionOrchestrator,
    NotionPageIndexOrchestrator,
    NotionIncrementalIndexOrchestrator,
    NotionFullIndexOrchestrator,
    QAOrchestrator,
    SupplementProposeOrchestrator,
    SupplementReviewOrchestrator,
    SupplementQueryOrchestrator,
    TelegramGatewayOrchestrator,
    TelegramIngestionOrchestrator,
    TelegramPageOrchestrator,
    TelegramQAOrchestrator,
    TelegramReviewOrchestrator,
    TelegramSyncOrchestrator,
    TelegramIndexOrchestrator,
    TelegramOperatorOrchestrator,
)
from src.providers import EmbeddingClient, ProviderRouter
from src.queue import QueueClient
from src.rag import ProductionChunkRetriever
from src.repositories import (
    ChangeRequestRepository,
    ChunkRepository,
    NotionPageRepository,
    SourceDocumentRepository,
)
from src.services import (
    CostTracker,
    CostBudgetService,
    DuplicateKnowledgeChecker,
    KnowledgeStatsService,
    ReadinessService,
    PromptTemplateLoader,
    TelegramSessionStore,
    TelegramSyncSessionStore,
    InMemoryTelegramSyncSessionStore,
    TelegramIndexSessionStore,
    InMemoryTelegramIndexSessionStore,
    TelegramUpdateIdempotencyService,
    TrustBoundaryService,
    WorkflowRunService,
    WorkflowObservabilityService,
)
from src.tools import ToolRegistry


def build_telegram_gateway_orchestrator(
    *,
    db_session: Session,
    db_session_factory: SessionFactory,
    unit_of_work_factory: UnitOfWorkFactory,
    tool_registry: ToolRegistry,
    provider_router: ProviderRouter,
    embedding_client: Optional[EmbeddingClient],
    cost_tracker: CostTracker,
    prompt_template_loader: PromptTemplateLoader,
    trust_boundary: TrustBoundaryService,
    telegram_session_store: Optional[TelegramSessionStore] = None,
    telegram_sync_session_store: Optional[TelegramSyncSessionStore] = None,
    telegram_index_session_store: Optional[TelegramIndexSessionStore] = None,
    workflow_observability_service: Optional[WorkflowObservabilityService] = None,
    queue_client: Optional[QueueClient] = None,
    readiness_service: Optional[ReadinessService] = None,
    knowledge_stats_service: Optional[KnowledgeStatsService] = None,
) -> TelegramGatewayOrchestrator:
    workflow_run_service = WorkflowRunService(db_session_factory)
    settings = get_settings()
    update_idempotency_service = TelegramUpdateIdempotencyService(
        db_session_factory
    )

    telegram_ingestion_orchestrator = TelegramIngestionOrchestrator(
        tool_registry=tool_registry,
        document_ingestion_orchestrator=DocumentIngestionOrchestrator(
            tool_registry=tool_registry,
            unit_of_work_factory=unit_of_work_factory,
            workflow_run_service=workflow_run_service,
            index_knowledge=False,
        ),
        image_ocr_ingestion_orchestrator=ImageOCRIngestionOrchestrator(
            tool_registry=tool_registry,
            unit_of_work_factory=unit_of_work_factory,
            workflow_run_service=workflow_run_service,
        ),
        supplement_propose_orchestrator=SupplementProposeOrchestrator(
            provider_router=provider_router,
            cost_tracker=cost_tracker,
            prompt_template_loader=prompt_template_loader,
            source_document_repository=SourceDocumentRepository(db_session),
            notion_page_repository=NotionPageRepository(db_session),
            change_request_repository=ChangeRequestRepository(db_session),
            unit_of_work_factory=unit_of_work_factory,
            duplicate_checker=DuplicateKnowledgeChecker(
                chunk_repository=ChunkRepository(db_session),
            ),
            workflow_run_service=workflow_run_service,
        ),
        supplement_query_orchestrator=SupplementQueryOrchestrator(
            change_request_repository=ChangeRequestRepository(db_session),
            notion_page_repository=NotionPageRepository(db_session),
        ),
        session_store=telegram_session_store,
    )
    notion_page_index_orchestrator = NotionPageIndexOrchestrator(
        tool_registry=tool_registry,
        unit_of_work_factory=unit_of_work_factory,
        workflow_run_service=workflow_run_service,
        embedding_batch_service=build_embedding_batch_service(embedding_client),
        cost_tracker=cost_tracker,
        source_is_synthetic=get_settings().notion_backend == "mock",
    )
    telegram_qa_orchestrator = TelegramQAOrchestrator(
        qa_orchestrator=QAOrchestrator(
            retriever=ProductionChunkRetriever(
                chunk_repository=ChunkRepository(db_session),
            ),
            embedding_client=embedding_client,
            provider_router=provider_router,
            cost_tracker=cost_tracker,
            prompt_template_loader=prompt_template_loader,
            workflow_run_service=workflow_run_service,
        )
    )
    telegram_review_orchestrator = TelegramReviewOrchestrator(
        supplement_review_orchestrator=SupplementReviewOrchestrator(
            change_request_repository=ChangeRequestRepository(db_session),
            notion_page_repository=NotionPageRepository(db_session),
            unit_of_work_factory=unit_of_work_factory,
            tool_registry=tool_registry,
            page_index_orchestrator=notion_page_index_orchestrator,
            workflow_run_service=workflow_run_service,
        )
    )
    incremental_index_orchestrator = NotionIncrementalIndexOrchestrator(
        page_index_orchestrator=notion_page_index_orchestrator,
        workflow_run_service=workflow_run_service,
    )
    full_index_orchestrator = NotionFullIndexOrchestrator(
        tool_registry=tool_registry,
        page_index_orchestrator=notion_page_index_orchestrator,
        workflow_run_service=workflow_run_service,
    )
    if workflow_observability_service is None:
        settings = get_settings()
        workflow_observability_service = WorkflowObservabilityService(
            db_session_factory,
            cost_budget_service=CostBudgetService(
                daily_budget_usd=settings.max_daily_cost_usd,
                workflow_budget_usd=settings.max_workflow_cost_usd,
            ),
            stale_after_seconds=settings.workflow_stale_after_seconds,
        )
    if readiness_service is None:
        settings = get_settings()
        notion_backend = normalize_notion_backend(settings.notion_backend)
        readiness_service = ReadinessService(
            probe=SqlAlchemyReadinessProbe(engine=engine),
            mode=settings.app_env,
            openai_configured=bool(settings.openai_api_key),
            queue_client=queue_client,
            queue_required=settings.app_env not in {"test", "demo", "mock"},
            notion_backend=notion_backend,
            notion_configured=(
                notion_backend == "mock" or bool(settings.notion_token)
            ),
        )
    if knowledge_stats_service is None:
        knowledge_stats_service = KnowledgeStatsService(db_session_factory)
    telegram_index_orchestrator = TelegramIndexOrchestrator(
        full_index_orchestrator=full_index_orchestrator,
        index_session_store=telegram_index_session_store
        or InMemoryTelegramIndexSessionStore(),
        workflow_run_service=workflow_run_service,
        workflow_observability_service=workflow_observability_service,
        queue_client=queue_client,
        indexing_job_timeout_seconds=settings.telegram_indexing_job_timeout_seconds,
    )
    telegram_operator_orchestrator = TelegramOperatorOrchestrator(
        workflow_observability_service=workflow_observability_service,
        readiness_service=readiness_service,
        knowledge_stats_service=knowledge_stats_service,
        supplement_query_orchestrator=SupplementQueryOrchestrator(
            change_request_repository=ChangeRequestRepository(db_session),
            notion_page_repository=NotionPageRepository(db_session),
        ),
    )
    telegram_sync_orchestrator = TelegramSyncOrchestrator(
        tool_registry=tool_registry,
        session_store=telegram_sync_session_store
        or InMemoryTelegramSyncSessionStore(),
        incremental_index_orchestrator=incremental_index_orchestrator,
        workflow_run_service=workflow_run_service,
    )
    telegram_page_orchestrator = TelegramPageOrchestrator(
        notion_page_repository=NotionPageRepository(db_session)
    )

    return TelegramGatewayOrchestrator(
        tool_registry=tool_registry,
        workflow_run_service=workflow_run_service,
        telegram_ingestion_orchestrator=telegram_ingestion_orchestrator,
        telegram_qa_orchestrator=telegram_qa_orchestrator,
        telegram_review_orchestrator=telegram_review_orchestrator,
        telegram_page_orchestrator=telegram_page_orchestrator,
        telegram_sync_orchestrator=telegram_sync_orchestrator,
        telegram_index_orchestrator=telegram_index_orchestrator,
        telegram_operator_orchestrator=telegram_operator_orchestrator,
        telegram_session_store=telegram_session_store,
        telegram_index_session_store=telegram_index_session_store,
        trust_boundary=trust_boundary,
        update_idempotency_service=update_idempotency_service,
        queue_client=queue_client,
        telegram_job_timeout_seconds=settings.telegram_job_timeout_seconds,
        telegram_review_job_timeout_seconds=settings.telegram_review_job_timeout_seconds,
    )
