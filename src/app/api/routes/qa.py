from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from src.app.dependencies import (
    get_cost_tracker,
    get_embedding_client,
    get_prompt_template_loader,
    get_provider_router,
)
from src.app.schemas import QACitation, QARequest, QAResponse
from src.db.session import SessionFactory, get_db_session, get_db_session_factory
from src.orchestrators import QAOrchestrator, QAOrchestratorError
from src.providers import EmbeddingClient, ProviderRouter
from src.rag import ProductionChunkRetriever
from src.repositories import ChunkRepository
from src.services import CostTracker, PromptTemplateLoader, WorkflowRunService

router = APIRouter()


def _build_qa_orchestrator(
    *,
    db_session: Session,
    db_session_factory: SessionFactory,
    embedding_client: Optional[EmbeddingClient],
    provider_router: ProviderRouter,
    cost_tracker: CostTracker,
    prompt_template_loader: PromptTemplateLoader,
) -> QAOrchestrator:
    return QAOrchestrator(
        retriever=ProductionChunkRetriever(
            chunk_repository=ChunkRepository(db_session),
        ),
        embedding_client=embedding_client,
        provider_router=provider_router,
        cost_tracker=cost_tracker,
        prompt_template_loader=prompt_template_loader,
        workflow_run_service=WorkflowRunService(db_session_factory),
    )


@router.post("/api/qa", response_model=QAResponse)
async def run_qa(
    payload: QARequest,
    request: Request,
    db_session: Session = Depends(get_db_session),
    db_session_factory: SessionFactory = Depends(get_db_session_factory),
    embedding_client: Optional[EmbeddingClient] = Depends(get_embedding_client),
    provider_router: ProviderRouter = Depends(get_provider_router),
    cost_tracker: CostTracker = Depends(get_cost_tracker),
    prompt_template_loader: PromptTemplateLoader = Depends(get_prompt_template_loader),
) -> QAResponse:
    orchestrator = _build_qa_orchestrator(
        db_session=db_session,
        db_session_factory=db_session_factory,
        embedding_client=embedding_client,
        provider_router=provider_router,
        cost_tracker=cost_tracker,
        prompt_template_loader=prompt_template_loader,
    )
    request_workflow_id = str(getattr(request.state, "workflow_id", ""))

    try:
        result = await orchestrator.answer_question(
            query=payload.query,
            top_k=payload.top_k,
            page_ids=payload.page_ids,
            section_paths=payload.section_paths,
            source_kinds=payload.source_kinds,
            provider_name=payload.provider_name,
            model=payload.model,
            request_workflow_id=request_workflow_id,
        )
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

    return QAResponse(
        workflow_run_id=result.workflow_run_id,
        status=result.status,
        answer=result.answer,
        insufficient_info=result.insufficient_info,
        retrieved_chunk_count=result.retrieved_chunk_count,
        citations=[
            QACitation(
                notion_path=citation.notion_path,
                page_id=citation.page_id,
                score=citation.score,
            )
            for citation in result.citations
        ],
        provider=result.provider,
        model=result.model,
        token_input=result.token_input,
        token_output=result.token_output,
    )
