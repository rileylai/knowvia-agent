from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from typing import List, Optional

from src.agent import AgentRuntimeError, BoundedAgentRuntime
from src.db.unit_of_work import UnitOfWorkFactory
from src.memory import detect_explicit_save_intent
from src.conversation_recall import (
    classify_conversation_recall,
    classify_conversation_transform,
)
from src.conversation_citations import ConversationCitation
from src.orchestrators.qa_orchestrator import QACitationResult, QAOrchestrator, QAResult
from src.services.memory import MemoryEmbeddingError, MemoryService, MemoryServiceError
from src.services.execution_events import ExecutionEventSink, emit_execution_status
from src.response_language import (
    conversation_context_unavailable_answer,
    memory_confirmation,
    resolve_response_language,
)
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
    assistant_message_id: Optional[int] = None


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
        memory_service: Optional[MemoryService] = None,
        agent_runtime: Optional[BoundedAgentRuntime] = None,
    ) -> None:
        if message_limit <= 0:
            raise ValueError("message_limit must be positive")
        if token_budget <= 0:
            raise ValueError("token_budget must be positive")
        self._unit_of_work_factory = unit_of_work_factory
        self._qa_orchestrator = qa_orchestrator
        self._message_limit = message_limit
        self._token_budget = token_budget
        self._memory_service = memory_service
        self._agent_runtime = agent_runtime

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
        event_sink: Optional[ExecutionEventSink] = None,
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
            latest_messages = repository.list_messages(
                session_id=session_id,
                owner_id=owner_id,
                limit=1,
            )
            pending_retry = (
                latest_messages[0]
                if latest_messages
                and latest_messages[0].role == "user"
                and latest_messages[0].content == normalized_query
                else None
            )
            if pending_retry is None:
                user_message = repository.append_message(
                    session_id=session_id,
                    owner_id=owner_id,
                    role="user",
                    content=normalized_query,
                )
                user_message_id = user_message.id
            else:
                user_message_id = pending_retry.id
            recent_messages = repository.list_messages(
                session_id=session_id,
                owner_id=owner_id,
                limit=self._message_limit,
            )

        try:
            save_intent = detect_explicit_save_intent(normalized_query)
        except ValueError as exc:
            raise ConversationOrchestratorError(
                error_code="INVALID_MEMORY",
                message=str(exc),
                http_status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            ) from exc
        if save_intent is not None:
            if (
                self._agent_runtime is not None
                and self._agent_runtime.supports_provider(provider_name)
            ):
                try:
                    agent_result = await self._agent_runtime.run(
                        query=normalized_query,
                        session_id=session_id,
                        owner_id=owner_id,
                        provider_name=provider_name,
                        model=model,
                        request_workflow_id=request_workflow_id,
                        explicit_save_allowed=True,
                        explicit_save_content=save_intent.content,
                        explicit_save_memory_type=save_intent.memory_type,
                        user_message_id=user_message_id,
                        event_sink=event_sink,
                    )
                except AgentRuntimeError as exc:
                    raise ConversationOrchestratorError(
                        error_code=exc.error_code,
                        message=exc.message,
                        http_status_code=exc.http_status_code,
                    ) from exc
                if agent_result.status != "succeeded":
                    raise ConversationOrchestratorError(
                        error_code="AGENT_RUNTIME_FAILED",
                        message="The bounded agent could not complete this request.",
                        http_status_code=HTTPStatus.BAD_GATEWAY,
                    )
                confirmation = agent_result.answer or (
                    memory_confirmation(
                        resolve_response_language(normalized_query),
                        agent_result.memory_status or "already_saved",
                    )
                )
                with self._unit_of_work_factory() as unit_of_work:
                    repository = unit_of_work.conversations
                    assistant_message = repository.append_message(
                        session_id=session_id,
                        owner_id=owner_id,
                        role="assistant",
                        content=confirmation,
                    )
                    assistant_message_id = int(assistant_message.id)
                    session = repository.get_session(
                        session_id=session_id,
                        owner_id=owner_id,
                    )
                    if session is None:
                        raise self._unavailable_error()
                return ConversationTurnResult(
                    session=session,
                    qa_result=QAResult(
                        workflow_run_id=agent_result.workflow_run_id,
                        status=agent_result.status,
                        answer=confirmation,
                        insufficient_info=False,
                        retrieved_chunk_count=0,
                        citations=[],
                        provider=agent_result.provider,
                        model=agent_result.model,
                        token_input=agent_result.token_input,
                        token_output=agent_result.token_output,
                        memory_status=agent_result.memory_status,
                        used_saved_memory=False,
                    ),
                    assistant_message_id=assistant_message_id,
                )
            if self._memory_service is None:
                raise ConversationOrchestratorError(
                    error_code="MEMORY_UNAVAILABLE",
                    message="Memory service is unavailable.",
                    http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            emit_execution_status(event_sink, phase="saving_memory")
            try:
                save_result = await self._memory_service.save_memory(
                    owner_id=owner_id,
                    content=save_intent.content,
                    memory_type=save_intent.memory_type,
                    source_session_id=session_id,
                    source_message_id=user_message_id,
                )
            except MemoryServiceError as exc:
                raise ConversationOrchestratorError(
                    error_code=(
                        "MEMORY_EMBEDDING_FAILED"
                        if isinstance(exc, MemoryEmbeddingError)
                        else "INVALID_MEMORY"
                    ),
                    message=str(exc),
                    http_status_code=(
                        HTTPStatus.BAD_GATEWAY
                        if isinstance(exc, MemoryEmbeddingError)
                        else HTTPStatus.UNPROCESSABLE_ENTITY
                    ),
                ) from exc

            confirmation = memory_confirmation(
                resolve_response_language(normalized_query),
                save_result.status,
            )
            with self._unit_of_work_factory() as unit_of_work:
                repository = unit_of_work.conversations
                assistant_message = repository.append_message(
                    session_id=session_id,
                    owner_id=owner_id,
                    role="assistant",
                    content=confirmation,
                )
                assistant_message_id = int(assistant_message.id)
                session = repository.get_session(
                    session_id=session_id,
                    owner_id=owner_id,
                )
                if session is None:
                    raise self._unavailable_error()
            return ConversationTurnResult(
                session=session,
                qa_result=QAResult(
                    workflow_run_id=0,
                    status="succeeded",
                    answer=confirmation,
                    insufficient_info=False,
                    retrieved_chunk_count=0,
                    citations=[],
                    provider=None,
                    model=None,
                    token_input=None,
                    token_output=None,
                    memory_status=save_result.status,
                ),
                assistant_message_id=assistant_message_id,
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
        transform_kind = classify_conversation_transform(normalized_query)
        if recall_kind is not None:
            conversation_context = "\n\n".join(
                f"[{message.role}] {message.content}"
                for message in context.messages[:-1]
            )
            if not prior_messages:
                conversation_context = None
        elif transform_kind is not None:
            conversation_context = "\n\n".join(
                f"[{message.role}] {message.content}"
                for message in context.messages[:-1]
            )
            if not any(message.role == "assistant" for message in prior_messages):
                conversation_context = None
        else:
            conversation_context = context.rendered_text

        if transform_kind is not None and conversation_context is None:
            qa_result = QAResult(
                workflow_run_id=0,
                status="succeeded",
                answer=conversation_context_unavailable_answer(
                    resolve_response_language(normalized_query)
                ),
                insufficient_info=False,
                retrieved_chunk_count=0,
                citations=[],
                provider=None,
                model=None,
                token_input=None,
                token_output=None,
            )
        elif self._qa_orchestrator is None:
            raise RuntimeError("QA orchestrator is required for message submission")
        elif (
            self._agent_runtime is not None
            and self._agent_runtime.supports_provider(provider_name)
            and (recall_kind is None or transform_kind is not None)
        ):
            try:
                agent_result = await self._agent_runtime.run(
                    query=normalized_query,
                    session_id=session_id,
                    owner_id=owner_id,
                    provider_name=provider_name,
                    model=model,
                    request_workflow_id=request_workflow_id,
                    conversation_context=conversation_context,
                    conversation_transform=transform_kind is not None,
                    user_message_id=user_message_id,
                    top_k=top_k,
                    page_ids=page_ids,
                    section_paths=section_paths,
                    source_kinds=source_kinds,
                    event_sink=event_sink,
                )
            except AgentRuntimeError as exc:
                raise ConversationOrchestratorError(
                    error_code=exc.error_code,
                    message=exc.message,
                    http_status_code=exc.http_status_code,
                ) from exc
            if agent_result.status != "succeeded":
                raise ConversationOrchestratorError(
                    error_code="AGENT_RUNTIME_FAILED",
                    message="The bounded agent could not complete this request.",
                    http_status_code=HTTPStatus.BAD_GATEWAY,
                )
            qa_result = QAResult(
                workflow_run_id=agent_result.workflow_run_id,
                status=agent_result.status,
                answer=agent_result.answer,
                insufficient_info=agent_result.insufficient_info,
                retrieved_chunk_count=agent_result.retrieved_chunk_count,
                citations=[
                    QACitationResult(
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
                    for citation in agent_result.citations
                ],
                provider=agent_result.provider,
                model=agent_result.model,
                token_input=agent_result.token_input,
                token_output=agent_result.token_output,
                memory_status=agent_result.memory_status,
                used_saved_memory=agent_result.used_saved_memory,
            )
        else:
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
                conversation_only=(
                    recall_kind is not None or transform_kind is not None
                ),
                owner_scope=owner_id,
                event_sink=event_sink,
            )

        with self._unit_of_work_factory() as unit_of_work:
            repository = unit_of_work.conversations
            if repository.get_session(
                session_id=session_id,
                owner_id=owner_id,
                include_messages=False,
            ) is None:
                raise self._unavailable_error()
            assistant_message = repository.append_message(
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
                used_saved_memory=qa_result.used_saved_memory,
            )
            assistant_message_id = int(assistant_message.id)
            session = repository.get_session(
                session_id=session_id,
                owner_id=owner_id,
            )
            if session is None:
                raise self._unavailable_error()
            return ConversationTurnResult(
                session=session,
                qa_result=qa_result,
                assistant_message_id=assistant_message_id,
            )

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
