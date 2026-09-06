from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from src.app.dependencies import (
    get_business_unit_of_work_factory,
    get_current_owner_id,
    get_embedding_client,
)
from src.app.schemas import (
    MemoryCreateRequest,
    MemoryResponse,
    MemorySaveResponse,
)
from src.db.session import SessionFactory
from src.db.unit_of_work import UnitOfWorkFactory
from src.providers import EmbeddingClient
from src.services import (
    MemoryEmbeddingError,
    MemoryService,
    MemoryServiceError,
    MemoryValidationError,
)

router = APIRouter()


def _build_memory_service(
    *,
    unit_of_work_factory: UnitOfWorkFactory,
    embedding_client: Optional[EmbeddingClient],
) -> MemoryService:
    return MemoryService(
        unit_of_work_factory=unit_of_work_factory,
        embedding_client=embedding_client,
    )


def _memory_response(memory) -> MemoryResponse:
    return MemoryResponse(
        id=memory.id,
        memory_type=memory.memory_type,
        content=memory.content,
        status=memory.status,
        created_at=memory.created_at,
    )


def _raise_memory_error(exc: MemoryServiceError) -> None:
    status_code = 502 if isinstance(exc, MemoryEmbeddingError) else 422
    raise HTTPException(
        status_code=status_code,
        detail={
            "error_code": "MEMORY_EMBEDDING_FAILED"
            if isinstance(exc, MemoryEmbeddingError)
            else "INVALID_MEMORY",
            "message": str(exc),
        },
    ) from exc


@router.get("/api/memories", response_model=List[MemoryResponse])
def list_memories(
    owner_id: str = Depends(get_current_owner_id),
    unit_of_work_factory: UnitOfWorkFactory = Depends(get_business_unit_of_work_factory),
    embedding_client: Optional[EmbeddingClient] = Depends(get_embedding_client),
) -> List[MemoryResponse]:
    _ = embedding_client
    service = _build_memory_service(
        unit_of_work_factory=unit_of_work_factory,
        embedding_client=embedding_client,
    )
    return [_memory_response(memory) for memory in service.list_memories(owner_id=owner_id)]


@router.post("/api/memories", response_model=MemorySaveResponse, status_code=201)
async def save_memory(
    payload: MemoryCreateRequest,
    owner_id: str = Depends(get_current_owner_id),
    unit_of_work_factory: UnitOfWorkFactory = Depends(get_business_unit_of_work_factory),
    embedding_client: Optional[EmbeddingClient] = Depends(get_embedding_client),
) -> MemorySaveResponse:
    service = _build_memory_service(
        unit_of_work_factory=unit_of_work_factory,
        embedding_client=embedding_client,
    )
    try:
        result = await service.save_memory(
            owner_id=owner_id,
            content=payload.content,
            memory_type=payload.memory_type,
            source_session_id=payload.source_session_id,
            source_message_id=payload.source_message_id,
        )
    except MemoryServiceError as exc:
        _raise_memory_error(exc)
    return MemorySaveResponse(status=result.status, memory=_memory_response(result.memory))


@router.delete("/api/memories/{memory_id}", status_code=204)
def delete_memory(
    memory_id: int,
    owner_id: str = Depends(get_current_owner_id),
    unit_of_work_factory: UnitOfWorkFactory = Depends(get_business_unit_of_work_factory),
) -> Response:
    service = _build_memory_service(
        unit_of_work_factory=unit_of_work_factory,
        embedding_client=None,
    )
    try:
        deleted = service.delete_memory(owner_id=owner_id, memory_id=memory_id)
    except MemoryServiceError as exc:
        _raise_memory_error(exc)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "MEMORY_UNAVAILABLE", "message": "Memory is unavailable."},
        )
    return Response(status_code=204)
