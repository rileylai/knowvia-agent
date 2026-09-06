from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from sqlalchemy import Float, Text, bindparam, cast, func
from sqlalchemy.orm import Session

from src.db.models import LongTermMemory


@dataclass(frozen=True)
class LongTermMemorySnapshot:
    id: int
    owner_id: str
    memory_type: str
    content: str
    embedding_model: str
    embedding_dimensions: int
    status: str
    created_at: datetime
    updated_at: datetime
    score: Optional[float] = None


class MemoryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_exact(
        self,
        *,
        owner_id: str,
        memory_type: str,
        content_normalized: str,
    ) -> Optional[LongTermMemorySnapshot]:
        memory = (
            self._session.query(LongTermMemory)
            .filter(
                LongTermMemory.owner_id == owner_id,
                LongTermMemory.memory_type == memory_type,
                LongTermMemory.content_normalized == content_normalized,
                LongTermMemory.status == "active",
            )
            .one_or_none()
        )
        return self._snapshot(memory) if memory is not None else None

    def create(
        self,
        *,
        owner_id: str,
        memory_type: str,
        content: str,
        content_normalized: str,
        embedding: List[float],
        embedding_model: str,
        embedding_dimensions: int,
        source_session_id: Optional[int] = None,
        source_message_id: Optional[int] = None,
    ) -> LongTermMemorySnapshot:
        memory = LongTermMemory(
            owner_id=owner_id,
            memory_type=memory_type,
            content=content,
            content_normalized=content_normalized,
            embedding=embedding,
            embedding_model=embedding_model,
            embedding_dimensions=embedding_dimensions,
            source_session_id=source_session_id,
            source_message_id=source_message_id,
            status="active",
        )
        if self._is_sqlite:
            memory.id = self._allocate_id()
        self._session.add(memory)
        self._session.flush()
        self._session.refresh(memory)
        return self._snapshot(memory)

    def list_active(self, *, owner_id: str) -> List[LongTermMemorySnapshot]:
        rows = (
            self._session.query(LongTermMemory)
            .filter(
                LongTermMemory.owner_id == owner_id,
                LongTermMemory.status == "active",
            )
            .order_by(LongTermMemory.created_at.desc(), LongTermMemory.id.desc())
            .all()
        )
        return [self._snapshot(row) for row in rows]

    def delete(self, *, owner_id: str, memory_id: int) -> bool:
        deleted = (
            self._session.query(LongTermMemory)
            .filter(
                LongTermMemory.id == memory_id,
                LongTermMemory.owner_id == owner_id,
                LongTermMemory.status == "active",
            )
            .delete(synchronize_session=False)
        )
        self._session.flush()
        return bool(deleted)

    def search_by_vector(
        self,
        *,
        owner_id: str,
        query_embedding: List[float],
        top_k: int,
        memory_type: Optional[str] = None,
    ) -> List[LongTermMemorySnapshot]:
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        if self._supports_vector_query:
            query_embedding_param = cast(
                bindparam(
                    "query_embedding",
                    value=str(query_embedding),
                    type_=Text(),
                ),
                LongTermMemory.__table__.c.embedding.type,
            )
            vector_distance = cast(
                LongTermMemory.embedding.op("<=>")(query_embedding_param),
                Float,
            )
            query = self._session.query(
                LongTermMemory,
                vector_distance.label("vector_distance"),
            ).filter(
                LongTermMemory.owner_id == owner_id,
                LongTermMemory.status == "active",
                LongTermMemory.embedding.is_not(None),
            )
            if memory_type is not None:
                query = query.filter(LongTermMemory.memory_type == memory_type)
            rows = (
                query
                .order_by(vector_distance.asc(), LongTermMemory.id.asc())
                .limit(top_k)
                .all()
            )
            return [
                self._snapshot(memory, score=max(0.0, min(1.0, 1.0 - float(distance))))
                for memory, distance in rows
            ]

        normalized_query = self._normalize_vector(query_embedding)
        query = self._session.query(LongTermMemory).filter(
            LongTermMemory.owner_id == owner_id,
            LongTermMemory.status == "active",
        )
        if memory_type is not None:
            query = query.filter(LongTermMemory.memory_type == memory_type)
        candidates = query.all()
        ranked = []
        for memory in candidates:
            score = self._cosine_similarity(normalized_query, memory.embedding)
            if score is not None:
                ranked.append((score, memory))
        ranked.sort(key=lambda item: (-item[0], int(item[1].id)))
        return [self._snapshot(memory, score=score) for score, memory in ranked[:top_k]]

    @property
    def _supports_vector_query(self) -> bool:
        return self._session.bind is not None and self._session.bind.dialect.name == "postgresql"

    @property
    def _is_sqlite(self) -> bool:
        return self._session.bind is not None and self._session.bind.dialect.name == "sqlite"

    def _allocate_id(self) -> int:
        max_id = int(self._session.query(func.max(LongTermMemory.id)).scalar() or 0)
        for instance in self._session.identity_map.values():
            if isinstance(instance, LongTermMemory) and instance.id is not None:
                max_id = max(max_id, int(instance.id))
        return max_id + 1

    def _snapshot(
        self,
        memory: LongTermMemory,
        *,
        score: Optional[float] = None,
    ) -> LongTermMemorySnapshot:
        return LongTermMemorySnapshot(
            id=int(memory.id),
            owner_id=memory.owner_id,
            memory_type=memory.memory_type,
            content=memory.content,
            embedding_model=memory.embedding_model,
            embedding_dimensions=int(memory.embedding_dimensions),
            status=memory.status,
            created_at=memory.created_at,
            updated_at=memory.updated_at,
            score=score,
        )

    def _normalize_vector(self, values: List[float]) -> List[float]:
        normalized = []
        for value in values:
            normalized.append(float(value))
        if not normalized:
            raise ValueError("embedding must contain numeric values")
        return normalized

    def _cosine_similarity(
        self,
        left: List[float],
        right: Optional[List[float]],
    ) -> Optional[float]:
        if right is None:
            return None
        try:
            normalized_right = self._normalize_vector(right)
        except (TypeError, ValueError):
            return None
        if len(left) != len(normalized_right):
            return None
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in normalized_right))
        if left_norm == 0 or right_norm == 0:
            return None
        return max(
            0.0,
            min(
                1.0,
                sum(a * b for a, b in zip(left, normalized_right))
                / (left_norm * right_norm),
            ),
        )
