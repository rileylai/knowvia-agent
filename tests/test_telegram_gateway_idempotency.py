from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.base import Base
from src.db.models import TelegramUpdateLedger, WorkflowRun
from src.orchestrators import TelegramGatewayError, TelegramGatewayOrchestrator
from src.services import (
    TelegramUpdateIdempotencyService,
    WorkflowRunService,
)
from src.tools import (
    DisabledTelegramBotClient,
    InMemoryTelegramBotClient,
    TelegramBotTool,
    ToolRegistry,
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


def _run_gateway(gateway: TelegramGatewayOrchestrator, update_id: int):
    return asyncio.run(
        gateway.handle_webhook(
            update_id=update_id,
            chat_id="555",
            text="/help",
            caption=None,
            document=None,
            photos=[],
            request_workflow_id=f"request-{update_id}",
        )
    )


def test_gateway_replays_success_without_sending_twice() -> None:
    session_factory = _build_session_factory()
    telegram_client = InMemoryTelegramBotClient()
    registry = ToolRegistry()
    registry.register_tool(TelegramBotTool(telegram_client))
    gateway = TelegramGatewayOrchestrator(
        tool_registry=registry,
        workflow_run_service=WorkflowRunService(session_factory),
        update_idempotency_service=TelegramUpdateIdempotencyService(
            session_factory
        ),
    )

    first = _run_gateway(gateway, 3001)
    second = _run_gateway(gateway, 3001)

    assert first.status == "succeeded"
    assert second == first
    assert len(telegram_client.list_sent_messages()) == 1


def test_gateway_replays_failed_update_without_rerunning() -> None:
    session_factory = _build_session_factory()
    registry = ToolRegistry()
    registry.register_tool(TelegramBotTool(DisabledTelegramBotClient()))
    gateway = TelegramGatewayOrchestrator(
        tool_registry=registry,
        workflow_run_service=WorkflowRunService(session_factory),
        update_idempotency_service=TelegramUpdateIdempotencyService(
            session_factory
        ),
    )

    with pytest.raises(TelegramGatewayError) as first_error:
        _run_gateway(gateway, 3002)
    with pytest.raises(TelegramGatewayError) as replay_error:
        _run_gateway(gateway, 3002)

    assert first_error.value.error_code == "TELEGRAM_NOT_CONFIGURED"
    assert replay_error.value.error_code == first_error.value.error_code
    assert replay_error.value.workflow_run_id == first_error.value.workflow_run_id
