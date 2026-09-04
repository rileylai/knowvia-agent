from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.base import Base
from src.db.models import (
    ChangeRequest,
    NotionPage,
    SourceDocument,
    TelegramUpdateLedger,
    WorkflowRun,
)
from src.services import TelegramRecoveryService


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
            SourceDocument.__table__,
            ChangeRequest.__table__,
        ],
    )
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def test_recovery_dry_run_reads_existing_pending_business_outcome_only() -> None:
    session_factory = _session_factory()
    session = session_factory()
    try:
        session.add(
            NotionPage(
                id=1,
                notion_page_id="notion-parent-canonical",
                title="Parent",
                notion_path="Knowledge/Parent",
            )
        )
        session.add(
            SourceDocument(
                id=11,
                source_type="screenshot",
                source_display_name="Screenshot batch (1 images)",
                content_hash="hash-11",
                raw_text="PRIVATE_RAW_IMAGE_TEXT",
            )
        )
        session.add(
            ChangeRequest(
                id=6,
                source_document_id=11,
                target_notion_page_id=1,
                status="pending",
                proposal_json=json.dumps(
                    {
                        "title": "Existing screenshot proposal",
                        "target_path": "Knowledge/Parent/AI Supplement Zone",
                        "source": {
                            "source_type": "screenshot",
                            "source_display_name": "Screenshot batch (1 images)",
                        },
                        "summary": "Existing proposal summary.",
                        "concepts": ["recovery"],
                        "notes": ["Review this existing proposal."],
                    }
                ),
            )
        )
        session.add(
            WorkflowRun(
                id=83,
                workflow_type="telegram",
                status="failed",
                failure_reason="TELEGRAM_SEND_FAILED",
                metadata_json=json.dumps(
                    {
                        "chat_id": "123",
                        "business_status": "succeeded",
                        "raw_text": "PRIVATE_RAW_IMAGE_TEXT",
                        "telegram_payload": {"secret": "do-not-store"},
                    }
                ),
            )
        )
        session.add(
            TelegramUpdateLedger(
                update_id=190951650,
                status="failed",
                workflow_run_id=83,
                failure_json=json.dumps(
                    {
                        "failure_reason": "TELEGRAM_SEND_FAILED",
                        "message": "redacted",
                    }
                ),
            )
        )
        session.commit()
    finally:
        session.close()

    service = TelegramRecoveryService(session_factory)
    inspection = service.inspect(
        update_id=190951650,
        workflow_run_id=83,
        source_document_id=11,
        change_request_id=6,
    )
    assert inspection.eligible is True
    assert inspection.source_document_exists is True
    assert inspection.change_request_pending is True
    assert inspection.source_change_request_match is True
    assert inspection.target_page_exists is True
    assert service.get_chat_id(workflow_run_id=83) == "123"

    preview, target = service.build_preview(
        change_request_id=6,
        source_document_id=11,
    )
    assert target == "notion-parent-canonical"
    assert "Existing screenshot proposal" in preview
    assert "PRIVATE_RAW_IMAGE_TEXT" not in preview

    reconciled = service.reconcile_success(
        update_id=190951650,
        workflow_run_id=83,
        source_document_id=11,
        change_request_id=6,
        preview_delivery_status="succeeded",
        recovery_action="test_reconcile_without_telegram_send",
        telegram_message_id=None,
    )
    assert reconciled.reconciled is True
    assert reconciled.eligible is False
    assert reconciled.reason is None

    session = session_factory()
    try:
        workflow = session.get(WorkflowRun, 83)
        ledger = session.get(TelegramUpdateLedger, 190951650)
        proposal = session.get(ChangeRequest, 6)
        source = session.get(SourceDocument, 11)
        assert workflow is not None and workflow.status == "succeeded"
        assert ledger is not None and ledger.status == "succeeded"
        assert proposal is not None and proposal.status == "pending"
        assert source is not None
        metadata = json.loads(workflow.metadata_json or "{}")
        assert "raw_text" not in metadata
        assert "telegram_payload" not in metadata
        result = json.loads(ledger.result_json or "{}")
        assert result["source_document_id"] == 11
        assert result["change_request_id"] == 6
        assert result["reply_text"] == "Proposal preview delivery recovered."
    finally:
        session.close()
