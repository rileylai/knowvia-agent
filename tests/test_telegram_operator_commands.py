from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.app.dependencies import (
    get_embedding_client,
    get_readiness_service,
    get_telegram_index_session_store,
    get_telegram_session_store,
    get_telegram_sync_session_store,
    get_tool_registry,
    get_workflow_observability_service,
)
from src.app.legacy import app
from src.db.base import Base
from src.db.models import (
    ChangeRequest,
    KnowledgeChunk,
    NotionBlock,
    NotionPage,
    SourceDocument,
    TelegramUpdateLedger,
    WorkflowRun,
)
from src.db.session import get_db_session_factory, get_unit_of_work_factory
from src.db.session import get_db_session
from src.db.unit_of_work import SqlAlchemyUnitOfWork
from src.services import (
    CostBudgetService,
    InMemoryTelegramIndexSessionStore,
    InMemoryTelegramSessionStore,
    InMemoryTelegramSyncSessionStore,
    KnowledgeStatsService,
    ReadinessService,
    WorkflowObservabilityService,
    WorkflowRunService,
)
from src.tools import InMemoryTelegramBotClient, TelegramBotTool, ToolRegistry


def _session_factory():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            WorkflowRun.__table__,
            TelegramUpdateLedger.__table__,
            NotionPage.__table__,
            NotionBlock.__table__,
            SourceDocument.__table__,
            KnowledgeChunk.__table__,
            ChangeRequest.__table__,
        ],
    )
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _configure(
    *,
    session_factory,
    telegram_client,
    observability_service,
    session_store=None,
    readiness_service=None,
):
    def db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def tool_registry_override() -> ToolRegistry:
        registry = ToolRegistry()
        registry.register_tool(TelegramBotTool(telegram_client))
        return registry

    app.dependency_overrides[get_db_session] = db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_tool_registry] = tool_registry_override
    app.dependency_overrides[get_embedding_client] = lambda: None
    app.dependency_overrides[get_telegram_session_store] = (
        lambda: session_store or InMemoryTelegramSessionStore()
    )
    app.dependency_overrides[get_telegram_sync_session_store] = (
        lambda: InMemoryTelegramSyncSessionStore()
    )
    app.dependency_overrides[get_telegram_index_session_store] = (
        lambda: InMemoryTelegramIndexSessionStore()
    )
    app.dependency_overrides[get_workflow_observability_service] = (
        lambda: observability_service
    )
    if readiness_service is not None:
        app.dependency_overrides[get_readiness_service] = lambda: readiness_service


def _seed_workflow(
    session_factory,
    *,
    workflow_type: str,
    metadata: dict[str, object],
    started_at: datetime | None = None,
) -> int:
    workflow = WorkflowRunService(session_factory).start_workflow(
        workflow_type=workflow_type,
        metadata_json=json.dumps(metadata, sort_keys=True),
    )
    session = session_factory()
    try:
        stored = session.get(WorkflowRun, workflow.id)
        assert stored is not None
        stored.status = "succeeded"
        stored.finished_at = datetime.now(timezone.utc)
        if started_at is not None:
            stored.started_at = started_at
        session.commit()
    finally:
        session.close()
    return int(workflow.id)


def _telegram_update(update_id: int, text: str) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "chat": {"id": 123},
            "from": {"id": 456},
            "text": text,
        },
    }


def _seed_pending_proposal(session_factory) -> None:
    session = session_factory()
    try:
        session.add(
            NotionPage(
                id=1,
                notion_page_id="pending-target-page",
                title="NLP Week 5",
                notion_path="Knowledge/NLP/Week5",
            )
        )
        session.add(
            ChangeRequest(
                id=91,
                source_document_id=None,
                target_notion_page_id=1,
                status="pending",
                proposal_json=json.dumps(
                    {
                        "title": "Attention review proposal",
                        "target_path": (
                            "Knowledge/NLP/Week5/AI Supplement Zone/Attention review"
                        ),
                        "source": {
                            "source_type": "chat_text",
                            "source_display_name": "attention-notes.txt",
                        },
                        "summary": "Review query, key, and value alignment.",
                        "concepts": ["query key value"],
                        "notes": ["Keep the accepted supplement append-only."],
                        "citations": [],
                    },
                    sort_keys=True,
                ),
            )
        )
        session.add(
            ChangeRequest(
                id=92,
                source_document_id=None,
                target_notion_page_id=1,
                status="rejected",
                proposal_json=json.dumps(
                    {
                        "title": "Rejected proposal",
                        "target_path": "Knowledge/NLP/Week5/AI Supplement Zone/Rejected",
                        "source": {
                            "source_type": "chat_text",
                            "source_display_name": "rejected.txt",
                        },
                        "summary": "Must not appear in the pending inbox.",
                        "concepts": [],
                        "notes": [],
                        "citations": [],
                    },
                    sort_keys=True,
                ),
            )
        )
        session.commit()
    finally:
        session.close()


class _OperatorReadinessProbe:
    def __init__(self, *, database: bool = True) -> None:
        self._database = database

    def check_database(self) -> bool:
        return self._database

    def check_migration(self) -> bool:
        return True

    def check_vector_extension(self) -> bool:
        return True


class _OperatorQueueClient:
    def is_available(self) -> bool:
        return False

    def is_scheduler_available(self, *, queue_name: str) -> bool:
        _ = queue_name
        return False


def _seed_stats(session_factory) -> None:
    session = session_factory()
    try:
        session.add_all(
            [
                NotionPage(
                    id=1,
                    notion_page_id="stats-page-1",
                    title="Page 1",
                    notion_path="Knowledge/Page1",
                ),
                NotionPage(
                    id=2,
                    notion_page_id="stats-page-2",
                    title="Page 2",
                    notion_path="Knowledge/Page2",
                ),
                NotionBlock(
                    id=1,
                    notion_block_id="stats-block-1",
                    notion_page_id=1,
                    block_type="paragraph",
                    content_text="private content is not shown",
                    block_path="Knowledge/Page1/paragraph",
                    block_order=0,
                ),
                NotionBlock(
                    id=2,
                    notion_block_id="stats-block-2",
                    notion_page_id=1,
                    block_type="paragraph",
                    content_text="another private block",
                    block_path="Knowledge/Page1/paragraph-2",
                    block_order=1,
                ),
                KnowledgeChunk(
                    id=1,
                    notion_block_id=1,
                    chunk_index=0,
                    chunk_text="private chunk",
                    notion_path="Knowledge/Page1",
                    embedding_text="[0.1]",
                    source_kind="notion",
                ),
                KnowledgeChunk(
                    id=2,
                    notion_block_id=2,
                    chunk_index=1,
                    chunk_text="unembedded chunk",
                    notion_path="Knowledge/Page1",
                    embedding_text=None,
                    source_kind="notion",
                ),
                ChangeRequest(
                    id=101,
                    status="pending",
                    proposal_json="{}",
                ),
                ChangeRequest(
                    id=102,
                    status="accepted",
                    proposal_json="{}",
                ),
                ChangeRequest(
                    id=103,
                    status="rejected",
                    proposal_json="{}",
                ),
                WorkflowRun(
                    id=201,
                    workflow_type="indexing",
                    status="succeeded",
                    started_at=datetime(2026, 8, 3, 9, 0),
                    finished_at=datetime(2026, 8, 3, 9, 5),
                    metadata_json=json.dumps({"operation": "index_full"}),
                ),
                WorkflowRun(
                    id=202,
                    workflow_type="indexing",
                    status="succeeded",
                    started_at=datetime(2026, 8, 3, 10, 0),
                    finished_at=datetime(2026, 8, 3, 10, 3),
                    metadata_json=json.dumps(
                        {"operation": "index_incremental"}, separators=(",", ":")
                    ),
                ),
            ]
        )
        session.commit()
    finally:
        session.close()


def test_cost_scopes_preserve_unknowns_and_split_recorded_cost() -> None:
    now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
    today = WorkflowRun(
        id=1,
        workflow_type="qa",
        status="succeeded",
        started_at=now,
        metadata_json=json.dumps(
            {"estimated_cost": 0.3, "embedding_estimated_cost": 0.2}
        ),
    )
    unknown = WorkflowRun(
        id=2,
        workflow_type="indexing",
        status="succeeded",
        started_at=now,
        metadata_json=json.dumps({"embedding_estimated_cost": None}),
    )
    old = WorkflowRun(
        id=3,
        workflow_type="qa",
        status="succeeded",
        started_at=now - timedelta(days=8),
        metadata_json=json.dumps({"estimated_cost": 0.9}),
    )
    month_recent = WorkflowRun(
        id=4,
        workflow_type="qa",
        status="succeeded",
        started_at=now - timedelta(days=2),
        metadata_json=json.dumps({"estimated_cost": 0.9}),
    )
    service = CostBudgetService(daily_budget_usd=1.0, workflow_budget_usd=0.4)

    today_summary = service.summarize_scope(
        [today, unknown, old], scope="today", now=now
    )
    assert today_summary.total_cost_usd == pytest.approx(0.5)
    assert today_summary.llm_cost_usd == pytest.approx(0.3)
    assert today_summary.embedding_cost_usd == pytest.approx(0.2)
    assert today_summary.unknown_cost_workflow_count == 1
    assert today_summary.budget_status == "unknown"

    seven_day_summary = service.summarize_scope(
        [today, unknown, old, month_recent], scope="7d", now=now
    )
    assert seven_day_summary.workflow_count == 3
    assert seven_day_summary.total_cost_usd == pytest.approx(1.4)

    month_summary = service.summarize_scope(
        [today, unknown, old, month_recent], scope="month", now=now
    )
    assert month_summary.workflow_count == 3
    assert month_summary.total_cost_usd == pytest.approx(1.4)

    workflow_summary = service.summarize_scope(
        [unknown], scope="workflow", workflow_run_id=2, now=now
    )
    assert workflow_summary.budget_status == "unknown"
    assert workflow_summary.unknown_cost_workflow_count == 1


def test_telegram_cost_and_workflow_commands_are_bounded_and_read_only() -> None:
    session_factory = _session_factory()
    seeded_workflow_id = _seed_workflow(
        session_factory,
        workflow_type="qa",
        metadata={
            "operation": "qa_answer",
            "estimated_cost": 0.25,
            "prompt": "private prompt content",
            "source_text": "private source content",
            "api_key": "private secret",
            "citation_count": 2,
        },
    )
    _seed_workflow(
        session_factory,
        workflow_type="indexing",
        metadata={"operation": "index_full", "embedding_estimated_cost": None},
    )
    observability_service = WorkflowObservabilityService(
        session_factory,
        cost_budget_service=CostBudgetService(
            daily_budget_usd=1.0,
            workflow_budget_usd=0.5,
        ),
    )
    telegram_client = InMemoryTelegramBotClient()
    _configure(
        session_factory=session_factory,
        telegram_client=telegram_client,
        observability_service=observability_service,
    )

    try:
        client = TestClient(app)
        cost = client.post("/api/telegram/webhook", json=_telegram_update(7101, "/cost"))
        assert cost.status_code == 200
        cost_payload = cost.json()
        assert cost_payload["cost_scope"] == "today"
        assert cost_payload["cost_llm_usd"] == pytest.approx(0.25)
        assert cost_payload["cost_unknown_workflow_count"] == 1
        assert "Pricing: unknown" in cost_payload["reply_text"]
        assert "private prompt content" not in cost_payload["reply_text"]

        for update_id, command_text in ((7106, "/cost 7d"), (7107, "/cost month")):
            scoped = client.post(
                "/api/telegram/webhook",
                json=_telegram_update(update_id, command_text),
            )
            assert scoped.status_code == 200
            assert scoped.json()["cost_scope"] in {"7d", "month"}

        workflow_cost = client.post(
            "/api/telegram/webhook",
            json=_telegram_update(7108, f"/cost workflow {seeded_workflow_id}"),
        )
        assert workflow_cost.status_code == 200
        assert workflow_cost.json()["cost_scope"] == "workflow"
        assert workflow_cost.json()["cost_workflow_run_id"] == seeded_workflow_id

        workflow = client.post(
            "/api/telegram/webhook",
            json=_telegram_update(7102, f"/workflow {seeded_workflow_id}"),
        )
        assert workflow.status_code == 200
        workflow_payload = workflow.json()
        assert workflow_payload["workflow_detail_run_id"] == seeded_workflow_id
        assert workflow_payload["workflow_detail_type"] == "qa"
        assert "qa_answer" in workflow_payload["reply_text"]
        assert "private prompt content" not in workflow_payload["reply_text"]
        assert "private source content" not in workflow_payload["reply_text"]
        assert "private secret" not in workflow_payload["reply_text"]

        recent = client.post(
            "/api/telegram/webhook",
            json=_telegram_update(7103, "/workflow"),
        )
        assert recent.status_code == 200
        assert recent.json()["workflow_recent_count"] >= 1
        assert "Recent workflows:" in recent.json()["reply_text"]

        invalid = client.post(
            "/api/telegram/webhook",
            json=_telegram_update(7104, "/cost 30d"),
        )
        assert invalid.status_code == 400
        assert invalid.json()["detail"]["error_code"] == "INVALID_ARGUMENT"

        missing = client.post(
            "/api/telegram/webhook",
            json=_telegram_update(7105, "/workflow 999999"),
        )
        assert missing.status_code == 404
        assert missing.json()["detail"]["error_code"] == "WORKFLOW_NOT_FOUND"
    finally:
        app.dependency_overrides.clear()


def test_telegram_status_distinguishes_liveness_from_safe_readiness_checks() -> None:
    session_factory = _session_factory()
    readiness_service = ReadinessService(
        probe=_OperatorReadinessProbe(database=False),
        mode="local",
        openai_configured=True,
        queue_client=_OperatorQueueClient(),
        queue_required=True,
        notion_backend="live",
        notion_configured=False,
    )
    observability_service = WorkflowObservabilityService(
        session_factory,
        cost_budget_service=CostBudgetService(),
    )
    _configure(
        session_factory=session_factory,
        telegram_client=InMemoryTelegramBotClient(),
        observability_service=observability_service,
        readiness_service=readiness_service,
    )

    try:
        response = TestClient(app).post(
            "/api/telegram/webhook",
            json=_telegram_update(7150, "/status"),
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "succeeded"
    assert payload["status_liveness"] == "ok"
    assert payload["status_readiness"] == "not_ready"
    assert payload["status_checks"]["database"] == "failed"
    assert payload["status_checks"]["redis"] == "failed"
    assert payload["status_checks"]["scheduler"] == "failed"
    assert payload["status_checks"]["notion"] == "failed"
    assert "private" not in payload["reply_text"]


def test_telegram_stats_returns_only_aggregate_counts_and_safe_timestamps() -> None:
    session_factory = _session_factory()
    _seed_stats(session_factory)
    observability_service = WorkflowObservabilityService(
        session_factory,
        cost_budget_service=CostBudgetService(),
    )
    _configure(
        session_factory=session_factory,
        telegram_client=InMemoryTelegramBotClient(),
        observability_service=observability_service,
    )

    try:
        response = TestClient(app).post(
            "/api/telegram/webhook",
            json=_telegram_update(7151, "/stats"),
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["stats_page_count"] == 2
    assert payload["stats_block_count"] == 2
    assert payload["stats_chunk_count"] == 2
    assert payload["stats_vector_count"] == 1
    assert payload["stats_proposal_count"] == 3
    assert payload["stats_pending_proposal_count"] == 1
    assert payload["stats_accepted_proposal_count"] == 1
    assert payload["stats_rejected_proposal_count"] == 1
    assert payload["stats_latest_full_index_at"].endswith("Z")
    assert payload["stats_latest_incremental_sync_at"].endswith("Z")
    assert "private content" not in payload["reply_text"]
    assert "stats-block-1" not in payload["reply_text"]


def test_telegram_status_and_stats_reject_extra_arguments() -> None:
    session_factory = _session_factory()
    observability_service = WorkflowObservabilityService(
        session_factory,
        cost_budget_service=CostBudgetService(),
    )
    _configure(
        session_factory=session_factory,
        telegram_client=InMemoryTelegramBotClient(),
        observability_service=observability_service,
    )

    try:
        client = TestClient(app)
        for update_id, command_text in ((7152, "/status extra"), (7153, "/stats extra")):
            response = client.post(
                "/api/telegram/webhook",
                json=_telegram_update(update_id, command_text),
            )
            assert response.status_code == 400
            assert response.json()["detail"]["error_code"] == "INVALID_ARGUMENT"
    finally:
        app.dependency_overrides.clear()


def test_telegram_pending_inbox_is_bounded_and_reuses_review_callbacks() -> None:
    session_factory = _session_factory()
    _seed_pending_proposal(session_factory)
    telegram_client = InMemoryTelegramBotClient()
    session_store = InMemoryTelegramSessionStore()
    observability_service = WorkflowObservabilityService(
        session_factory,
        cost_budget_service=CostBudgetService(),
    )
    _configure(
        session_factory=session_factory,
        telegram_client=telegram_client,
        observability_service=observability_service,
        session_store=session_store,
    )

    try:
        client = TestClient(app)
        inbox = client.post(
            "/api/telegram/webhook",
            json=_telegram_update(7201, "/pending"),
        )
        assert inbox.status_code == 200
        inbox_payload = inbox.json()
        assert inbox_payload["command"] == "pending"
        assert inbox_payload["pending_count"] == 1
        assert "Attention review proposal" in inbox_payload["reply_text"]
        assert "Review query, key, and value alignment." in inbox_payload["reply_text"]
        assert "Source: attention-notes.txt" in inbox_payload["reply_text"]
        assert "Target: Knowledge/NLP/Week5" in inbox_payload["reply_text"]
        assert "Rejected proposal" not in inbox_payload["reply_text"]
        assert len(inbox_payload["reply_text"]) <= 4096

        inbox_markup = telegram_client.list_sent_messages()[-1].reply_markup
        assert inbox_markup is not None
        view_token = inbox_markup["inline_keyboard"][0][0]["callback_data"]
        assert view_token.startswith("ll:")
        assert "91" not in view_token

        cross_user_view = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 7202,
                "callback_query": {
                    "id": "pending-view",
                    "from": {"id": 999},
                    "data": view_token,
                    "message": {"message_id": 7202, "chat": {"id": 123}},
                },
            },
        )
        assert cross_user_view.status_code == 400
        assert cross_user_view.json()["detail"]["error_code"] == "INVALID_CALLBACK"

        view = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 7203,
                "callback_query": {
                    "id": "pending-view-valid",
                    "from": {"id": 456},
                    "data": view_token,
                    "message": {"message_id": 7203, "chat": {"id": 123}},
                },
            },
        )
        assert view.status_code == 200
        view_payload = view.json()
        assert view_payload["change_request_id"] == 91
        assert view_payload["change_request_status"] == "pending"
        assert "Concepts: query key value" in view_payload["reply_text"]
        assert "Status: pending" in view_payload["reply_text"]
        assert view_payload["business_status"] == "succeeded"

        view_markup = telegram_client.list_sent_messages()[-1].reply_markup
        assert view_markup is not None
        reject_token = view_markup["inline_keyboard"][0][1]["callback_data"]
        assert reject_token.startswith("ll:")

        duplicate_view = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 7204,
                "callback_query": {
                    "id": "pending-view-duplicate",
                    "from": {"id": 456},
                    "data": view_token,
                    "message": {"message_id": 7204, "chat": {"id": 123}},
                },
            },
        )
        assert duplicate_view.status_code == 400
        assert duplicate_view.json()["detail"]["error_code"] == "INVALID_CALLBACK"

        rejected = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 7205,
                "callback_query": {
                    "id": "pending-reject",
                    "from": {"id": 456},
                    "data": reject_token,
                    "message": {"message_id": 7203, "chat": {"id": 123}},
                },
            },
        )
        assert rejected.status_code == 200
        rejected_payload = rejected.json()
        assert rejected_payload["review_action"] == "reject"
        assert rejected_payload["change_request_status"] == "rejected"

        verify_session = session_factory()
        try:
            proposal = verify_session.get(ChangeRequest, 91)
            assert proposal is not None
            assert proposal.status == "rejected"
            assert verify_session.query(NotionPage).count() == 1
        finally:
            verify_session.close()
    finally:
        app.dependency_overrides.clear()
