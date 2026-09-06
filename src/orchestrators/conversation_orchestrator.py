from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from typing import List, Optional

from src.db.unit_of_work import UnitOfWorkFactory
from src.conversation_recall import classify_conversation_recall
from src.conversation_citations import ConversationCitation
from src.orchestrators.qa_orchestrator import QAOrchestrator, QAResult
from src.repositories.conversation_repository import (
    ConversationRepository,
    ConversationSessionSnapshot,
)
from src.conversation_context import (
    DEFAULT_CONVERSATION_MESSAGE_LIMIT,
    DEFAULT_CONVERSATION_TOKEN_BUDGET,
    ConversationContextMessage,
    assemble_conversation_context,
)


@dataclass(frozen=True)
class ConversationTurnResult:
    session: ConversationSessionSnapshot
    qa_result: QAResult


class ConversationOrchestratorError(Exception):
    def __init__(
        self,
        *,
        error_code: str,
        message: str,
        http_status_code: int,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.http_status_code = http_status_code


class ConversationOrchestrator:
    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        qa_orchestrator: Optional[QAOrchestrator],
        message_limit: int = DEFAULT_CONVERSATION_MESSAGE_LIMIT,
        token_budget: int = DEFAULT_CONVERSATION_TOKEN_BUDGET,
    ) -> None:
        if message_limit <= 0:
            raise ValueError("message_limit must be positive")
        if token_budget <= 0:
            raise ValueError("token_budget must be positive")
        self._unit_of_work_factory = unit_of_work_factory
        self._qa_orchestrator = qa_orchestrator
        self._message_limit = message_limit
        self._token_budget = token_budget

    def create_session(self, *, owner_id: str) -> ConversationSessionSnapshot:
        with self._unit_of_work_factory() as unit_of_work:
            session = unit_of_work.conversations.create_session(owner_id=owner_id)
            return self._snapshot_session(
                session_id=int(session.id),
                owner_id=owner_id,
                repository=unit_of_work.conversations,
            )

    def list_sessions(self, *, owner_id: str) -> List[ConversationSessionSnapshot]:
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.conversations.list_sessions(owner_id=owner_id)

    def get_session(
        self,
        *,
        session_id: int,
        owner_id: str,
    ) -> ConversationSessionSnapshot:
        with self._unit_of_work_factory() as unit_of_work:
            session = unit_of_work.conversations.get_session(
                session_id=session_id,
                owner_id=owner_id,
            )
            if session is None:
                raise self._unavailable_error()
            return session

    async def send_message(
        self,
        *,
        session_id: int,
        owner_id: str,
        query: str,
        top_k: int,
        page_ids: Optional[List[str]],
        section_paths: Optional[List[str]],
        source_kinds: Optional[List[str]],
        provider_name: str,
        model: str,
        request_workflow_id: str,
    ) -> ConversationTurnResult:
        normalized_query = query.strip()
        if not normalized_query:
            raise ConversationOrchestratorError(
                error_code="INVALID_ARGUMENT",
                message="query must not be empty",
                http_status_code=HTTPStatus.BAD_REQUEST,
            )

        with self._unit_of_work_factory() as unit_of_work:
            repository = unit_of_work.conversations
            if repository.get_session(
                session_id=session_id,
                owner_id=owner_id,
                include_messages=False,
            ) is None:
                raise self._unavailable_error()
            repository.append_message(
                session_id=session_id,
                owner_id=owner_id,
                role="user",
                content=normalized_query,
            )
            recent_messages = repository.list_messages(
                session_id=session_id,
                owner_id=owner_id,
                limit=self._message_limit,
            )

        prior_messages = recent_messages[:-1]
        context = assemble_conversation_context(
            history=[
                ConversationContextMessage(role=message.role, content=message.content)
                for message in prior_messages
            ],
            current_question=normalized_query,
            max_messages=self._message_limit,
            token_budget=self._token_budget,
        )

        recall_kind = classify_conversation_recall(normalized_query)
        if recall_kind is not None:
            conversation_context = "\n\n".join(
                f"[{message.role}] {message.content}"
                for message in context.messages[:-1]
            )
            if not prior_messages:
                conversation_context = None
        else:
            conversation_context = context.rendered_text

        if self._qa_orchestrator is None:
            raise RuntimeError("QA orchestrator is required for message submission")
        qa_result = await self._qa_orchestrator.answer_question(
            query=normalized_query,
            top_k=top_k,
            page_ids=page_ids,
            section_paths=section_paths,
            source_kinds=source_kinds,
            provider_name=provider_name,
            model=model,
            request_workflow_id=request_workflow_id,
            conversation_context=conversation_context,
            conversation_only=recall_kind is not None,
            owner_scope=owner_id,
        )

        with self._unit_of_work_factory() as unit_of_work:
            repository = unit_of_work.conversations
            if repository.get_session(
                session_id=session_id,
                owner_id=owner_id,
                include_messages=False,
            ) is None:
                raise self._unavailable_error()
            repository.append_message(
                session_id=session_id,
                owner_id=owner_id,
                role="assistant",
                content=qa_result.answer,
                citations=[
                    ConversationCitation(
                        notion_path=citation.notion_path,
                        page_id=citation.page_id,
                        score=citation.score,
                        source_kind=citation.source_kind,
                        source_display_name=citation.source_display_name,
                        locator=citation.locator,
                        source_url=citation.source_url,
                        image_index=citation.image_index,
                        sequence_index=citation.sequence_index,
                        original_filename=citation.original_filename,
                    )
                    for citation in qa_result.citations
                ],
            )
            session = repository.get_session(
                session_id=session_id,
                owner_id=owner_id,
            )
            if session is None:
                raise self._unavailable_error()
            return ConversationTurnResult(session=session, qa_result=qa_result)

    def _snapshot_session(
        self,
        *,
        session_id: int,
        owner_id: str,
        repository: ConversationRepository,
    ) -> ConversationSessionSnapshot:
        snapshot = repository.get_session(
            session_id=session_id,
            owner_id=owner_id,
        )
        if snapshot is None:
            raise self._unavailable_error()
        return snapshot

    @staticmethod
    def _unavailable_error() -> ConversationOrchestratorError:
        return ConversationOrchestratorError(
            error_code="CONVERSATION_UNAVAILABLE",
            message="Conversation is unavailable.",
            http_status_code=HTTPStatus.NOT_FOUND,
        )
