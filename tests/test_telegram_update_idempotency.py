from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.base import Base
from src.db.models import TelegramUpdateLedger, WorkflowRun
from src.services import (
    TELEGRAM_UPDATE_FAILED,
    TELEGRAM_UPDATE_RUNNING,
    TELEGRAM_UPDATE_SUCCEEDED,
    TelegramUpdateIdempotencyService,
)


def _build_session_factory(database_url: str = "sqlite+pysqlite:///:memory:"):
    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool if ":memory:" in database_url else None,
    )
    Base.metadata.create_all(
        engine,
        tables=[WorkflowRun.__table__, TelegramUpdateLedger.__table__],
    )
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def test_update_ledger_replays_succeeded_and_failed_states() -> None:
    session_factory = _build_session_factory()
    service = TelegramUpdateIdempotencyService(session_factory)

    owner = service.claim(1001)
    assert owner is not None
    assert owner.owner is True
    assert owner.status == TELEGRAM_UPDATE_RUNNING

    running_duplicate = service.claim(1001)
    assert running_duplicate is not None
    assert running_duplicate.owner is False
    assert running_duplicate.status == TELEGRAM_UPDATE_RUNNING

    service.mark_succeeded(
        1001,
        workflow_run_id=12,
        result={"status": "succeeded", "reply_text": "ok"},
    )
    succeeded_duplicate = service.claim(1001)
    assert succeeded_duplicate is not None
    assert succeeded_duplicate.status == TELEGRAM_UPDATE_SUCCEEDED
    assert succeeded_duplicate.workflow_run_id == 12
    assert succeeded_duplicate.result_json is not None

    failed_owner = service.claim(1002)
    assert failed_owner is not None and failed_owner.owner is True
    service.mark_failed(
        1002,
        workflow_run_id=13,
        failure={
            "error_code": "TELEGRAM_SEND_FAILED",
            "failure_reason": "TELEGRAM_SEND_FAILED",
            "http_status_code": 502,
            "message": "send failed",
        },
    )
    failed_duplicate = service.claim(1002)
    assert failed_duplicate is not None
    assert failed_duplicate.status == TELEGRAM_UPDATE_FAILED
    assert failed_duplicate.workflow_run_id == 13
    assert failed_duplicate.failure_json is not None


def test_concurrent_duplicate_claims_have_one_owner(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'telegram-ledger.db'}"
    session_factory = _build_session_factory(database_url)
    barrier = Barrier(2)

    def synchronized_factory():
        barrier.wait()
        return session_factory()

    service = TelegramUpdateIdempotencyService(synchronized_factory)
    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(lambda _: service.claim(2001), range(2)))

    assert sum(bool(claim and claim.owner) for claim in claims) == 1
    assert all(claim is not None for claim in claims)
    assert {claim.status for claim in claims if claim is not None} == {
        TELEGRAM_UPDATE_RUNNING
    }

    session = session_factory()
    try:
        assert session.query(func.count(TelegramUpdateLedger.update_id)).scalar() == 1
    finally:
        session.close()
