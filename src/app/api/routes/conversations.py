from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from src.agent import BoundedAgentRuntime, build_agent_tool_registry
from src.app.api.routes.qa import _build_qa_orchestrator
from src.app.config import get_settings
from src.app.dependencies import (
    get_business_unit_of_work_factory,
    get_cost_tracker,
    get_current_owner_id,
    get_embedding_client,
    get_memory_service,
    get_prompt_template_loader,
    get_provider_router,
)
from src.app.schemas import (
    ConversationMessageRequest,
    ConversationMessageResponse,
    ConversationSessionResponse,
    ConversationSessionSummaryResponse,
    ConversationTurnResponse,
)
from src.db.session import SessionFactory, get_db_session, get_db_session_factory
from src.db.unit_of_work import UnitOfWorkFactory
from src.orchestrators import (
    ConversationOrchestrator,
    ConversationOrchestratorError,
    QAOrchestratorError,
)
from src.providers import EmbeddingClient, ProviderRouter
from src.rag import ProductionChunkRetriever
from src.repositories import ChunkRepository
from src.services import CostTracker, PromptTemplateLoader
from src.services import MemoryService, WorkflowRunService

router = APIRouter()


def _build_conversation_orchestrator(
    *,
    db_session: Session,
    db_session_factory: SessionFactory,
    unit_of_work_factory: UnitOfWorkFactory,
    embedding_client: Optional[EmbeddingClient],
    provider_router: ProviderRouter,
    cost_tracker: CostTracker,
    prompt_template_loader: PromptTemplateLoader,
    memory_service: Optional[MemoryService] = None,
) -> ConversationOrchestrator:
    settings = get_settings()
    agent_runtime = BoundedAgentRuntime(
        provider_router=provider_router,
        tool_registry=build_agent_tool_registry(
            retriever=ProductionChunkRetriever(
                chunk_repository=ChunkRepository(db_session),
            ),
            embedding_client=embedding_client,
            memory_service=memory_service,
        ),
        max_tool_calls=settings.agent_max_tool_calls,
        max_iterations=settings.agent_max_iterations,
        tool_timeout_seconds=settings.agent_tool_timeout_seconds,
        context_char_budget=settings.agent_context_char_budget,
        workflow_run_service=WorkflowRunService(db_session_factory),
    ) if memory_service is not None else None
    return ConversationOrchestrator(
        unit_of_work_factory=unit_of_work_factory,
        qa_orchestrator=_build_qa_orchestrator(
            db_session=db_session,
            db_session_factory=db_session_factory,
            embedding_client=embedding_client,
            provider_router=provider_router,
            cost_tracker=cost_tracker,
            prompt_template_loader=prompt_template_loader,
            memory_service=memory_service,
        ),
        message_limit=settings.conversation_context_message_limit,
        token_budget=settings.conversation_context_token_budget,
        memory_service=memory_service,
        agent_runtime=agent_runtime,
    )


def _title(value: Optional[str]) -> str:
    return value or "New conversation"


def _message_response(message) -> ConversationMessageResponse:
    return ConversationMessageResponse(
        id=message.id,
        session_id=message.session_id,
        role=message.role,
        content=message.content,
        sequence_number=message.sequence_number,
        created_at=message.created_at,
        citations=[citation.to_payload() for citation in message.citations],
        used_saved_memory=message.used_saved_memory,
    )


def _summary_response(session) -> ConversationSessionSummaryResponse:
    return ConversationSessionSummaryResponse(
        id=session.id,
        title=_title(session.title),
        status=session.status,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def _session_response(session) -> ConversationSessionResponse:
    summary = _summary_response(session)
    return ConversationSessionResponse(
        **summary.model_dump(),
        messages=[_message_response(message) for message in session.messages],
    )


def _raise_conversation_error(exc: ConversationOrchestratorError) -> None:
    raise HTTPException(
        status_code=exc.http_status_code,
        detail={
            "error_code": exc.error_code,
            "message": exc.message,
            "failure_reason": exc.error_code,
            "workflow_run_id": None,
        },
    ) from exc


def _build_crud_orchestrator(
    unit_of_work_factory: UnitOfWorkFactory,
) -> ConversationOrchestrator:
    return ConversationOrchestrator(
        unit_of_work_factory=unit_of_work_factory,
        qa_orchestrator=None,
    )


@router.post(
    "/api/conversations",
    response_model=ConversationSessionResponse,
    status_code=201,
)
def create_conversation(
    owner_id: str = Depends(get_current_owner_id),
    unit_of_work_factory: UnitOfWorkFactory = Depends(get_business_unit_of_work_factory),
) -> ConversationSessionResponse:
    orchestrator = _build_crud_orchestrator(unit_of_work_factory)
    return _session_response(orchestrator.create_session(owner_id=owner_id))


@router.get(
    "/api/conversations",
    response_model=List[ConversationSessionSummaryResponse],
)
def list_conversations(
    owner_id: str = Depends(get_current_owner_id),
    unit_of_work_factory: UnitOfWorkFactory = Depends(get_business_unit_of_work_factory),
) -> List[ConversationSessionSummaryResponse]:
    orchestrator = _build_crud_orchestrator(unit_of_work_factory)
    return [
        _summary_response(session)
        for session in orchestrator.list_sessions(owner_id=owner_id)
    ]


@router.get(
    "/api/conversations/{session_id}",
    response_model=ConversationSessionResponse,
)
def get_conversation(
    session_id: int,
    owner_id: str = Depends(get_current_owner_id),
    unit_of_work_factory: UnitOfWorkFactory = Depends(get_business_unit_of_work_factory),
) -> ConversationSessionResponse:
    orchestrator = _build_crud_orchestrator(unit_of_work_factory)
    try:
        session = orchestrator.get_session(session_id=session_id, owner_id=owner_id)
    except ConversationOrchestratorError as exc:
        _raise_conversation_error(exc)
    return _session_response(session)


@router.post(
    "/api/conversations/{session_id}/messages",
    response_model=ConversationTurnResponse,
)
async def send_conversation_message(
    session_id: int,
    payload: ConversationMessageRequest,
    request: Request,
    db_session: Session = Depends(get_db_session),
    db_session_factory: SessionFactory = Depends(get_db_session_factory),
    unit_of_work_factory: UnitOfWorkFactory = Depends(get_business_unit_of_work_factory),
    embedding_client: Optional[EmbeddingClient] = Depends(get_embedding_client),
    provider_router: ProviderRouter = Depends(get_provider_router),
    cost_tracker: CostTracker = Depends(get_cost_tracker),
    prompt_template_loader: PromptTemplateLoader = Depends(get_prompt_template_loader),
    memory_service: MemoryService = Depends(get_memory_service),
    owner_id: str = Depends(get_current_owner_id),
) -> ConversationTurnResponse:
    orchestrator = _build_conversation_orchestrator(
        db_session=db_session,
        db_session_factory=db_session_factory,
        unit_of_work_factory=unit_of_work_factory,
        embedding_client=embedding_client,
        provider_router=provider_router,
        cost_tracker=cost_tracker,
        prompt_template_loader=prompt_template_loader,
        memory_service=memory_service,
    )
    try:
        result = await orchestrator.send_message(
            session_id=session_id,
            owner_id=owner_id,
            query=payload.query,
            top_k=payload.top_k,
            page_ids=payload.page_ids,
            section_paths=payload.section_paths,
            source_kinds=payload.source_kinds,
            provider_name=payload.provider_name,
            model=payload.model,
            request_workflow_id=str(getattr(request.state, "workflow_id", "")),
        )
    except ConversationOrchestratorError as exc:
        _raise_conversation_error(exc)
    except QAOrchestratorError as exc:
        raise HTTPException(
            status_code=exc.http_status_code,
            detail={
                "error_code": exc.error_code,
                "message": exc.message,
                "failure_reason": exc.failure_reason,
                "workflow_run_id": exc.workflow_run_id,
            },
        ) from exc

    qa_result = result.qa_result
    return ConversationTurnResponse(
        session_id=result.session.id,
        title=_title(result.session.title),
        updated_at=result.session.updated_at,
        workflow_run_id=qa_result.workflow_run_id,
        status=qa_result.status,
        answer=qa_result.answer,
        insufficient_info=qa_result.insufficient_info,
        retrieved_chunk_count=qa_result.retrieved_chunk_count,
        citations=[
            {
                "notion_path": citation.notion_path,
                "page_id": citation.page_id,
                "score": citation.score,
                "source_kind": citation.source_kind,
                "source_display_name": citation.source_display_name,
                "locator": citation.locator,
                "source_url": citation.source_url,
                "image_index": citation.image_index,
                "sequence_index": citation.sequence_index,
                "original_filename": citation.original_filename,
            }
            for citation in qa_result.citations
        ],
        provider=qa_result.provider,
        model=qa_result.model,
        token_input=qa_result.token_input,
        token_output=qa_result.token_output,
        memory_status=qa_result.memory_status,
        used_saved_memory=qa_result.used_saved_memory,
        messages=[_message_response(message) for message in result.session.messages],
    )
