from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List, Optional

from sqlalchemy import Float, Text, bindparam, cast, func, or_
from sqlalchemy.orm import Session

from src.db.models import KnowledgeChunk, NotionBlock, NotionPage
from src.policies.synthetic_data import SYNTHETIC_NOTION_PAGE_IDS


class ChunkRepositoryError(Exception):
    pass


class ChunkBlockMappingError(ChunkRepositoryError):
    pass


class ChunkVectorQueryError(ChunkRepositoryError):
    pass


@dataclass
class NotionChunkUpsert:
    chunk_index: int
    chunk_text: str
    notion_path: str
    notion_block_ids: List[str] = field(default_factory=list)
    source_kind: str = "notion"
    embedding: Optional[List[float]] = None


@dataclass
class RetrievalChunkCandidate:
    chunk_id: int
    chunk_index: int
    chunk_text: str
    notion_path: str
    source_kind: str
    notion_page_id: Optional[str]
    embedding_text: Optional[str]


@dataclass
class SemanticChunkMatch:
    chunk_id: int
    chunk_index: int
    chunk_text: str
    notion_path: str
    source_kind: str
    notion_page_id: Optional[str]
    score: float


class ChunkRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _allocate_chunk_id_for_sqlite(self) -> int:
        max_id_in_db = int(self._session.query(func.max(KnowledgeChunk.id)).scalar() or 0)
        max_id_in_identity_map = 0
        for instance in self._session.identity_map.values():
            if isinstance(instance, KnowledgeChunk) and instance.id is not None:
                max_id_in_identity_map = max(max_id_in_identity_map, int(instance.id))
        return max(max_id_in_db, max_id_in_identity_map) + 1

    def upsert_chunks(
        self,
        *,
        notion_page_db_id: int,
        chunks: List[NotionChunkUpsert],
    ) -> List[KnowledgeChunk]:
        page_blocks = (
            self._session.query(NotionBlock.id, NotionBlock.notion_block_id)
            .filter(NotionBlock.notion_page_id == notion_page_db_id)
            .all()
        )
        block_db_ids = [row.id for row in page_blocks]
        block_id_map = {
            row.notion_block_id: row.id
            for row in page_blocks
        }

        if block_db_ids:
            self._session.query(KnowledgeChunk).filter(
                KnowledgeChunk.source_kind == "notion",
                KnowledgeChunk.notion_block_id.in_(block_db_ids),
            ).delete(synchronize_session=False)
            self._session.flush()

        inserted: List[KnowledgeChunk] = []
        for chunk in sorted(chunks, key=lambda item: item.chunk_index):
            if chunk.source_kind != "notion":
                raise ChunkRepositoryError(
                    f"Unsupported source_kind for upsert_chunks: {chunk.source_kind}"
                )
            notion_block_db_id = self._select_chunk_block_id(
                notion_block_ids=chunk.notion_block_ids,
                block_id_map=block_id_map,
            )

            knowledge_chunk = KnowledgeChunk(
                source_document_id=None,
                notion_block_id=notion_block_db_id,
                chunk_index=chunk.chunk_index,
                chunk_text=chunk.chunk_text,
                notion_path=chunk.notion_path,
                embedding=chunk.embedding,
                embedding_text=self._serialize_embedding(chunk.embedding),
                source_kind="notion",
            )
            if self._session.bind is not None and self._session.bind.dialect.name == "sqlite":
                knowledge_chunk.id = self._allocate_chunk_id_for_sqlite()
            self._session.add(knowledge_chunk)
            self._session.flush()
            inserted.append(knowledge_chunk)

        self._session.flush()
        for chunk in inserted:
            self._session.refresh(chunk)
        return inserted

    def delete_page_chunks(self, *, notion_page_db_id: int) -> int:
        page_block_ids = [
            row.id
            for row in self._session.query(NotionBlock.id)
            .filter(NotionBlock.notion_page_id == notion_page_db_id)
            .all()
        ]
        if not page_block_ids:
            return 0

        deleted_count = (
            self._session.query(KnowledgeChunk)
            .filter(
                KnowledgeChunk.source_kind == "notion",
                KnowledgeChunk.notion_block_id.in_(page_block_ids),
            )
            .delete(synchronize_session=False)
        )
        self._session.flush()
        return int(deleted_count or 0)

    def list_production_chunks(
        self,
        *,
        page_ids: Optional[List[str]] = None,
        section_paths: Optional[List[str]] = None,
        source_kinds: Optional[List[str]] = None,
    ) -> List[RetrievalChunkCandidate]:
        normalized_page_ids = self._normalize_text_list(page_ids)
        normalized_section_paths = [
            self._normalize_path(path) for path in self._normalize_text_list(section_paths)
        ]
        normalized_source_kinds = self._normalize_source_kinds(source_kinds)
        effective_source_kinds = self._effective_production_source_kinds(
            normalized_source_kinds
        )
        if not effective_source_kinds:
            return []

        query = (
            self._session.query(
                KnowledgeChunk.id,
                KnowledgeChunk.chunk_index,
                KnowledgeChunk.chunk_text,
                KnowledgeChunk.notion_path,
                KnowledgeChunk.source_kind,
                KnowledgeChunk.embedding_text,
                NotionPage.notion_page_id,
            )
            .outerjoin(NotionBlock, KnowledgeChunk.notion_block_id == NotionBlock.id)
            .outerjoin(NotionPage, NotionBlock.notion_page_id == NotionPage.id)
            .filter(KnowledgeChunk.source_kind.in_(effective_source_kinds))
        )
        query = self._exclude_known_synthetic_pages(query)
        query = self._apply_page_filter(
            query=query,
            normalized_page_ids=normalized_page_ids,
        )
        query = self._apply_section_filter(
            query=query,
            normalized_section_paths=normalized_section_paths,
        )

        rows = query.order_by(KnowledgeChunk.id.asc()).all()
        return [
            RetrievalChunkCandidate(
                chunk_id=row.id,
                chunk_index=row.chunk_index,
                chunk_text=row.chunk_text,
                notion_path=row.notion_path or "",
                source_kind=row.source_kind,
                notion_page_id=row.notion_page_id,
                embedding_text=row.embedding_text,
            )
            for row in rows
        ]

    def list_production_chunks_by_vector(
        self,
        *,
        query_embedding: List[float],
        top_k: int,
        page_ids: Optional[List[str]] = None,
        section_paths: Optional[List[str]] = None,
        source_kinds: Optional[List[str]] = None,
    ) -> List[SemanticChunkMatch]:
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        if not self.supports_vector_query():
            raise ChunkVectorQueryError(
                "Semantic vector query requires PostgreSQL + pgvector"
            )

        normalized_query_embedding = self._normalize_embedding(query_embedding)
        if normalized_query_embedding is None:
            raise ChunkVectorQueryError("query_embedding must contain numeric values")

        normalized_page_ids = self._normalize_text_list(page_ids)
        normalized_section_paths = [
            self._normalize_path(path) for path in self._normalize_text_list(section_paths)
        ]
        normalized_source_kinds = self._normalize_source_kinds(source_kinds)
        effective_source_kinds = self._effective_production_source_kinds(
            normalized_source_kinds
        )
        if not effective_source_kinds:
            return []

        query_embedding_param = cast(
            bindparam(
                "query_embedding",
                value=self._serialize_embedding(normalized_query_embedding),
                type_=Text(),
            ),
            KnowledgeChunk.__table__.c.embedding.type,
        )
        vector_distance = cast(
            KnowledgeChunk.embedding.op("<=>")(query_embedding_param),
            Float,
        )
        query = (
            self._session.query(
                KnowledgeChunk.id,
                KnowledgeChunk.chunk_index,
                KnowledgeChunk.chunk_text,
                KnowledgeChunk.notion_path,
                KnowledgeChunk.source_kind,
                NotionPage.notion_page_id,
                vector_distance.label("vector_distance"),
            )
            .outerjoin(NotionBlock, KnowledgeChunk.notion_block_id == NotionBlock.id)
            .outerjoin(NotionPage, NotionBlock.notion_page_id == NotionPage.id)
            .filter(
                KnowledgeChunk.source_kind.in_(effective_source_kinds),
                KnowledgeChunk.embedding.is_not(None),
            )
        )
        query = self._exclude_known_synthetic_pages(query)
        query = self._apply_page_filter(
            query=query,
            normalized_page_ids=normalized_page_ids,
        )
        query = self._apply_section_filter(
            query=query,
            normalized_section_paths=normalized_section_paths,
        )

        try:
            rows = (
                query.order_by(vector_distance.asc(), KnowledgeChunk.id.asc())
                .limit(top_k)
                .all()
            )
        except Exception as exc:
            raise ChunkVectorQueryError(
                f"Semantic vector query failed: {exc}"
            ) from exc

        return [
            SemanticChunkMatch(
                chunk_id=row.id,
                chunk_index=row.chunk_index,
                chunk_text=row.chunk_text,
                notion_path=row.notion_path or "",
                source_kind=row.source_kind,
                notion_page_id=row.notion_page_id,
                score=max(0.0, min(1.0, 1.0 - float(row.vector_distance))),
            )
            for row in rows
        ]

    def supports_vector_query(self) -> bool:
        return (
            self._session.bind is not None
            and self._session.bind.dialect.name == "postgresql"
        )

    def _select_chunk_block_id(
        self,
        *,
        notion_block_ids: List[str],
        block_id_map: dict[str, int],
    ) -> int:
        for notion_block_id in notion_block_ids:
            block_db_id = block_id_map.get(notion_block_id)
            if block_db_id is not None:
                return block_db_id
        raise ChunkBlockMappingError(
            "Cannot map chunk to notion block in current page"
        )

    def _serialize_embedding(self, embedding: Optional[List[float]]) -> Optional[str]:
        if embedding is None:
            return None
        return json.dumps(embedding)

    def _effective_production_source_kinds(
        self,
        normalized_source_kinds: List[str],
    ) -> List[str]:
        # Current production RAG in MVP reads from indexed Notion chunks only.
        return [
            source_kind for source_kind in normalized_source_kinds if source_kind == "notion"
        ]

    def _apply_page_filter(
        self,
        *,
        query,
        normalized_page_ids: List[str],
    ):
        if normalized_page_ids:
            query = query.filter(NotionPage.notion_page_id.in_(normalized_page_ids))
        return query

    def _exclude_known_synthetic_pages(self, query):
        bind = self._session.get_bind()
        if bind is None or bind.dialect.name != "postgresql":
            return query
        return query.filter(
            or_(
                NotionPage.notion_page_id.is_(None),
                NotionPage.notion_page_id.notin_(SYNTHETIC_NOTION_PAGE_IDS),
            )
        )

    def _apply_section_filter(
        self,
        *,
        query,
        normalized_section_paths: List[str],
    ):
        if normalized_section_paths:
            section_conditions = []
            for section_path in normalized_section_paths:
                section_conditions.append(
                    or_(
                        KnowledgeChunk.notion_path == section_path,
                        KnowledgeChunk.notion_path.like(f"{section_path}/%"),
                    )
                )
            query = query.filter(or_(*section_conditions))
        return query

    def _normalize_embedding(
        self,
        query_embedding: List[float],
    ) -> Optional[List[float]]:
        normalized = []
        for value in query_embedding:
            try:
                normalized.append(float(value))
            except (TypeError, ValueError):
                return None
        if not normalized:
            return None
        return normalized

    def _normalize_source_kinds(self, source_kinds: Optional[List[str]]) -> List[str]:
        if source_kinds is None:
            return ["notion"]
        normalized = []
        seen = set()
        for source_kind in source_kinds:
            candidate = str(source_kind).strip().lower()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)
        return normalized

    def _normalize_text_list(self, values: Optional[List[str]]) -> List[str]:
        if values is None:
            return []
        normalized = []
        seen = set()
        for value in values:
            candidate = str(value).strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)
        return normalized

    def _normalize_path(self, path: str) -> str:
        segments = [segment.strip() for segment in path.split("/") if segment.strip()]
        return "/".join(segments)
