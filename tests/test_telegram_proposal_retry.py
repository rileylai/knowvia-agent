from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.orchestrators.telegram_ingestion_orchestrator import (
    TelegramIngestionOrchestrator,
)
from src.services.telegram_session_store import (
    InMemoryTelegramSessionStore,
    TelegramUploadAttachment,
)


class _ExistingSourceProposalStub:
    def __init__(self) -> None:
        self.calls = []

    async def propose_change_request(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            source_document_id=22,
            change_request_id=15,
            source_type="screenshot",
            target_notion_page_id="page-1",
            target_notion_path="Knowledge/SQL/AI Supplement Zone",
            latency_metadata={"llm_ms": 1.0},
            reply_text="Proposal retry succeeded; it is ready for review.",
        )


def test_retry_existing_source_does_not_redownload_ocr_or_create_source() -> None:
    store = InMemoryTelegramSessionStore()
    store.upsert_upload(
        session_id="group-existing-source",
        chat_id="chat-1",
        user_id="user-1",
        media_group_id="media-1",
        attachments=[
            TelegramUploadAttachment(
                kind="photo",
                file_id="file-1",
                file_unique_id="unique-1",
                message_id=10,
            )
        ],
        command_text="/ingest",
    )
    store.mark_awaiting_target(
        session_id="group-existing-source",
        chat_id="chat-1",
        user_id="user-1",
    )
    store.claim_target(
        session_id="group-existing-source",
        chat_id="chat-1",
        user_id="user-1",
        target_notion_page_id="page-1",
        target_notion_path="Knowledge/SQL/AI Supplement Zone",
    )
    store.record_source_document(
        session_id="group-existing-source",
        chat_id="chat-1",
        user_id="user-1",
        source_document_id=22,
        source_type="screenshot",
        target_notion_page_id="page-1",
        target_notion_path="Knowledge/SQL/AI Supplement Zone",
    )
    store.fail_upload(
        session_id="group-existing-source",
        chat_id="chat-1",
        user_id="user-1",
        failure_reason="LLM_OUTPUT_INVALID",
    )

    proposal_stub = _ExistingSourceProposalStub()
    orchestrator = TelegramIngestionOrchestrator.__new__(
        TelegramIngestionOrchestrator
    )
    orchestrator._session_store = store
    orchestrator._supplement_propose_orchestrator = proposal_stub
    orchestrator._supplement_query_orchestrator = None

    result = asyncio.run(
        orchestrator.retry_existing_proposal(
            session_id="group-existing-source",
            chat_id="chat-1",
            user_id="user-1",
            request_workflow_id="retry-workflow",
        )
    )

    assert len(proposal_stub.calls) == 1
    assert proposal_stub.calls[0]["source_document_id"] == 22
    assert result.source_document_id == 22
    session = store.get_upload(
        session_id="group-existing-source",
        chat_id="chat-1",
        user_id="user-1",
    )
    assert session is not None
    assert session.state == "proposal_created"
    assert session.source_document_id == 22
    assert len(session.attachments) == 1
    assert session.change_request_id == 15

    claim_status, replay_session = store.claim_retry(
        session_id="group-existing-source",
        chat_id="chat-1",
        user_id="user-1",
    )
    assert claim_status == "already"
    assert replay_session is not None
    assert len(proposal_stub.calls) == 1


def test_old_picker_callback_retries_existing_source_without_reingestion() -> None:
    store = InMemoryTelegramSessionStore()
    store.upsert_upload(
        session_id="group-old-picker",
        chat_id="chat-1",
        user_id="user-1",
        media_group_id="media-1",
        attachments=[
            TelegramUploadAttachment(
                kind="photo",
                file_id="file-1",
                file_unique_id="unique-1",
                message_id=10,
            )
        ],
        command_text="/ingest",
    )
    store.mark_awaiting_target(
        session_id="group-old-picker",
        chat_id="chat-1",
        user_id="user-1",
    )
    store.claim_target(
        session_id="group-old-picker",
        chat_id="chat-1",
        user_id="user-1",
        target_notion_page_id="page-1",
        target_notion_path="Knowledge/SQL/AI Supplement Zone",
    )
    store.record_source_document(
        session_id="group-old-picker",
        chat_id="chat-1",
        user_id="user-1",
        source_document_id=23,
        source_type="screenshot",
        target_notion_page_id="page-1",
        target_notion_path="Knowledge/SQL/AI Supplement Zone",
    )
    store.fail_upload(
        session_id="group-old-picker",
        chat_id="chat-1",
        user_id="user-1",
        failure_reason="LLM_OUTPUT_INVALID",
    )

    proposal_stub = _ExistingSourceProposalStub()
    orchestrator = TelegramIngestionOrchestrator.__new__(
        TelegramIngestionOrchestrator
    )
    orchestrator._session_store = store
    orchestrator._supplement_propose_orchestrator = proposal_stub
    orchestrator._supplement_query_orchestrator = None

    result = asyncio.run(
        orchestrator.handle_target_selection(
            session_id="group-old-picker",
            chat_id="chat-1",
            user_id="user-1",
            target_notion_page_id="page-1",
            target_notion_path="Knowledge/SQL/AI Supplement Zone",
            request_workflow_id="old-picker-retry",
        )
    )

    assert result.source_document_id == 22
    assert len(proposal_stub.calls) == 1
    assert proposal_stub.calls[0]["source_document_id"] == 23
    session = store.get_upload(
        session_id="group-old-picker",
        chat_id="chat-1",
        user_id="user-1",
    )
    assert session is not None
    assert session.state == "proposal_created"
    assert session.source_document_id == 22
    assert len(session.attachments) == 1
