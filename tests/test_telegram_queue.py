from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.app.dependencies import (
    get_queue_client,
    get_tool_registry,
)
from src.app.legacy import app
from src.db.base import Base
from src.db.models import TelegramUpdateLedger, WorkflowRun
from src.db.unit_of_work import SqlAlchemyUnitOfWork
from src.db.session import (
    get_db_session,
    get_db_session_factory,
    get_unit_of_work_factory,
)
from src.orchestrators import (
    TelegramCallbackAttachment,
    TelegramGatewayOrchestrator,
)
from src.orchestrators.telegram_index_orchestrator import TelegramIndexError, TelegramIndexOrchestrator
from src.queue import FakeQueueClient
from src.services import (
    InMemoryTelegramIndexSessionStore,
    InMemoryTelegramSessionStore,
    TelegramUpdateIdempotencyService,
    WorkflowRunService,
)
from src.tools import InMemoryTelegramBotClient, TelegramBotTool, ToolRegistry
from src.worker.telegram import (
    TELEGRAM_FULL_INDEX_JOB_PATH,
    TELEGRAM_WEBHOOK_JOB_PATH,
)


def _build_session_factory():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[WorkflowRun.__table__, TelegramUpdateLedger.__table__],
    )
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def test_gateway_claims_once_and_enqueues_once() -> None:
    import asyncio

    session_factory = _build_session_factory()
    queue_client = FakeQueueClient()
    gateway = TelegramGatewayOrchestrator(
        tool_registry=ToolRegistry(),
        workflow_run_service=WorkflowRunService(session_factory),
        update_idempotency_service=TelegramUpdateIdempotencyService(
            session_factory
        ),
        queue_client=queue_client,
    )

    first = asyncio.run(
        gateway.enqueue_webhook(
            update_id=7001,
            chat_id="555",
            text="/ask private question",
            caption=None,
            document=None,
            photos=[],
            request_workflow_id="request-7001",
        )
    )
    second = asyncio.run(
        gateway.enqueue_webhook(
            update_id=7001,
            chat_id="555",
            text="/ask private question",
            caption=None,
            document=None,
            photos=[],
            request_workflow_id="request-7001-retry",
        )
    )

    assert first.status == "running"
    assert first.skipped_reason == "QUEUED"
    assert second.status == "running"
    assert second.skipped_reason == "DUPLICATE_UPDATE_IN_PROGRESS"
    assert len(queue_client.enqueued_jobs) == 1
    assert queue_client.enqueued_jobs[0].function_name == TELEGRAM_WEBHOOK_JOB_PATH
    assert queue_client.enqueued_jobs[0].timeout_seconds == 180

    session = session_factory()
    try:
        ledger = session.get(TelegramUpdateLedger, 7001)
        assert ledger is not None
        assert ledger.status == "running"
    finally:
        session.close()


def test_review_accept_uses_dedicated_timeout_and_preserves_queue_retry_policy() -> None:
    import asyncio

    session_factory = _build_session_factory()
    queue_client = FakeQueueClient()
    session_store = InMemoryTelegramSessionStore()
    callback_token = session_store.create_callback(
        session_id="proposal-44",
        chat_id="555",
        user_id="777",
        action="accept",
        change_request_id=44,
    )
    gateway = TelegramGatewayOrchestrator(
        tool_registry=ToolRegistry(),
        workflow_run_service=WorkflowRunService(session_factory),
        update_idempotency_service=TelegramUpdateIdempotencyService(
            session_factory
        ),
        telegram_session_store=session_store,
        queue_client=queue_client,
        telegram_job_timeout_seconds=180,
        telegram_review_job_timeout_seconds=7200,
    )
    callback = TelegramCallbackAttachment(
        callback_query_id="review-accept-44",
        callback_data=f"ll:{callback_token}",
    )

    first = asyncio.run(
        gateway.enqueue_webhook(
            update_id=7044,
            chat_id="555",
            text=None,
            caption=None,
            document=None,
            photos=[],
            request_workflow_id="request-7044",
            user_id="777",
            callback=callback,
        )
    )
    second = asyncio.run(
        gateway.enqueue_webhook(
            update_id=7044,
            chat_id="555",
            text=None,
            caption=None,
            document=None,
            photos=[],
            request_workflow_id="request-7044-retry",
            user_id="777",
            callback=callback,
        )
    )

    assert first.skipped_reason == "QUEUED"
    assert second.skipped_reason == "DUPLICATE_UPDATE_IN_PROGRESS"
    assert len(queue_client.enqueued_jobs) == 1
    job = queue_client.enqueued_jobs[0]
    assert job.timeout_seconds == 7200
    assert job.retry_policy is not None
    assert job.retry_policy.max_retries == 2
    assert job.retry_policy.retry_intervals == (5, 30)


def test_review_reject_callback_keeps_ordinary_timeout() -> None:
    import asyncio

    session_factory = _build_session_factory()
    queue_client = FakeQueueClient()
    session_store = InMemoryTelegramSessionStore()
    callback_token = session_store.create_callback(
        session_id="proposal-45",
        chat_id="555",
        user_id="777",
        action="reject",
        change_request_id=45,
    )
    gateway = TelegramGatewayOrchestrator(
        tool_registry=ToolRegistry(),
        workflow_run_service=WorkflowRunService(session_factory),
        telegram_session_store=session_store,
        queue_client=queue_client,
        telegram_job_timeout_seconds=180,
        telegram_review_job_timeout_seconds=7200,
    )

    asyncio.run(
        gateway.enqueue_webhook(
            update_id=7045,
            chat_id="555",
            text=None,
            caption=None,
            document=None,
            photos=[],
            request_workflow_id="request-7045",
            user_id="777",
            callback=TelegramCallbackAttachment(
                callback_query_id="review-reject-45",
                callback_data=f"ll:{callback_token}",
            ),
        )
    )

    assert len(queue_client.enqueued_jobs) == 1
    assert queue_client.enqueued_jobs[0].timeout_seconds == 180


def test_webhook_returns_fast_ack_when_queue_is_configured() -> None:
    session_factory = _build_session_factory()
    queue_client = FakeQueueClient()
    telegram_client = InMemoryTelegramBotClient()
    registry = ToolRegistry()
    registry.register_tool(TelegramBotTool(telegram_client))

    def db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_tool_registry] = lambda: registry
    app.dependency_overrides[get_queue_client] = lambda: queue_client

    try:
        response = TestClient(app).post(
            "/api/telegram/webhook",
            json={
                "update_id": 7002,
                "message": {
                    "message_id": 1,
                    "chat": {"id": 555},
                    "text": "/health",
                },
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202
    assert response.json()["status"] == "running"
    assert response.json()["skipped_reason"] == "QUEUED"
    assert len(queue_client.enqueued_jobs) == 1
    assert telegram_client.list_sent_messages() == []


def test_scheduled_telegram_work_uses_bounded_timeout_and_keeps_retry_policy() -> None:
    import asyncio

    session_factory = _build_session_factory()
    queue_client = FakeQueueClient()
    gateway = TelegramGatewayOrchestrator(
        tool_registry=ToolRegistry(),
        workflow_run_service=WorkflowRunService(session_factory),
        queue_client=queue_client,
    )

    gateway._schedule_upload_settle(
        session_id="upload-session",
        chat_id="555",
        user_id="777",
        request_workflow_id="request-settle",
    )

    assert len(queue_client.enqueued_jobs) == 1
    job = queue_client.enqueued_jobs[0]
    assert job.timeout_seconds == 180
    assert job.retry_policy is not None
    assert job.retry_policy.max_retries == 2
    assert job.retry_policy.retry_intervals == (1, 3)


def test_confirmed_full_index_enqueues_one_long_running_job_without_retry() -> None:
    import asyncio

    class _FullIndexStub:
        def start_full_index_workflow(self, *, request_workflow_id: str) -> int:
            assert request_workflow_id == "request-index"
            return 9001

    queue_client = FakeQueueClient()
    session_store = InMemoryTelegramIndexSessionStore()
    session_store.create_full_index_session(
        session_id="index-session",
        chat_id="555",
        user_id="777",
    )
    orchestrator = TelegramIndexOrchestrator(
        full_index_orchestrator=_FullIndexStub(),
        index_session_store=session_store,
        workflow_run_service=object(),
        workflow_observability_service=object(),
        queue_client=queue_client,
        indexing_job_timeout_seconds=10800,
    )

    result = asyncio.run(
        orchestrator.confirm_full_index(
            session_id="index-session",
            chat_id="555",
            user_id="777",
            request_workflow_id="request-index",
        )
    )

    assert result.status == "running"
    assert result.workflow_run_id == 9001
    assert len(queue_client.enqueued_jobs) == 1
    job = queue_client.enqueued_jobs[0]
    assert job.function_name == TELEGRAM_FULL_INDEX_JOB_PATH
    assert job.args == (9001, "request-index")
    assert job.timeout_seconds == 10800
    assert job.retry_policy is None

    duplicate = asyncio.run(
        orchestrator.confirm_full_index(
            session_id="index-session",
            chat_id="555",
            user_id="777",
            request_workflow_id="duplicate-request",
        )
    )
    assert duplicate.workflow_run_id == 9001
    assert len(queue_client.enqueued_jobs) == 1


def test_full_index_queue_failure_marks_workflow_safely() -> None:
    import asyncio
    from unittest.mock import Mock

    class _FailingQueueClient(FakeQueueClient):
        def enqueue(self, **kwargs):
            raise RuntimeError("queue unavailable with private details")

    class _FullIndexStub:
        def start_full_index_workflow(self, *, request_workflow_id: str) -> int:
            return 9002

    session_store = InMemoryTelegramIndexSessionStore()
    session_store.create_full_index_session(
        session_id="index-session",
        chat_id="555",
        user_id="777",
    )
    workflow_service = Mock()
    orchestrator = TelegramIndexOrchestrator(
        full_index_orchestrator=_FullIndexStub(),
        index_session_store=session_store,
        workflow_run_service=workflow_service,
        workflow_observability_service=object(),
        queue_client=_FailingQueueClient(),
    )

    try:
        asyncio.run(
            orchestrator.confirm_full_index(
                session_id="index-session",
                chat_id="555",
                user_id="777",
                request_workflow_id="request-index",
            )
        )
    except TelegramIndexError as exc:
        assert exc.error_code == "TELEGRAM_QUEUE_UNAVAILABLE"
        assert exc.failure_reason == "TELEGRAM_QUEUE_UNAVAILABLE"
        assert exc.metadata == {
            "index_workflow_run_id": 9002,
            "index_status": "failed",
        }
    else:
        raise AssertionError("queue failure must be surfaced")

    workflow_service.mark_workflow_failed.assert_called_once()
    assert (
        workflow_service.mark_workflow_failed.call_args.kwargs["failure_reason"]
        == "TELEGRAM_QUEUE_UNAVAILABLE"
    )
    session = session_store.get_full_index_session(
        session_id="index-session",
        chat_id="555",
        user_id="777",
    )
    assert session is not None
    assert session.state == "failed"
    assert session.workflow_run_id == 9002
