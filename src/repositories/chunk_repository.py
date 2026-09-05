from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy import Float, Text, and_, bindparam, cast, func, or_
from sqlalchemy.orm import Session

from src.db.models import KnowledgeChunk, NotionBlock, NotionPage, SourceDocument
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
class KnowledgeChunkUpsert:
    source_document_id: int
    source_kind: str
    chunk_index: int
    chunk_text: str
    source_display_name: str
    locator: str
    embedding: List[float]
    embedding_model: str
    embedding_dimensions: int
    citation_metadata: Dict[str, Any] = field(default_factory=dict)
    owner_scope: str = "local"
    eligibility_status: str = "eligible"


@dataclass
class RetrievalChunkCandidate:
    chunk_id: int
    chunk_index: int
    chunk_text: str
    notion_path: str
    source_kind: str
    notion_page_id: Optional[str]
    embedding_text: Optional[str]
    source_document_id: Optional[int] = None
    source_display_name: Optional[str] = None
    locator: Optional[str] = None
    citation_metadata: Optional[str] = None
    source_url: Optional[str] = None


@dataclass
class SemanticChunkMatch:
    chunk_id: int
    chunk_index: int
    chunk_text: str
    notion_path: str
    source_kind: str
    notion_page_id: Optional[str]
    score: float
    source_document_id: Optional[int] = None
    source_display_name: Optional[str] = None
    locator: Optional[str] = None
    citation_metadata: Optional[str] = None
    source_url: Optional[str] = None


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

    def upsert_source_document_chunks(
        self,
        *,
        source_document_id: int,
        chunks: List[KnowledgeChunkUpsert],
    ) -> List[KnowledgeChunk]:
        source_document = self._session.get(SourceDocument, source_document_id)
        if source_document is None:
            raise ChunkRepositoryError(
                f"SourceDocument not found: {source_document_id}"
            )

        ordered_chunks = sorted(chunks, key=lambda item: item.chunk_index)
        for chunk in ordered_chunks:
            if chunk.source_kind not in {"pdf", "notion", "url", "image"}:
                raise ChunkRepositoryError(
                    f"Unsupported source_kind for source document: {chunk.source_kind}"
                )
            if chunk.source_document_id != source_document_id:
                raise ChunkRepositoryError(
                    "Knowledge chunk source_document_id does not match the target"
                )
            if chunk.eligibility_status != "eligible":
                raise ChunkRepositoryError(
                    "Source document chunks must be eligible only after complete indexing"
                )
            if len(chunk.embedding) != chunk.embedding_dimensions:
                raise ChunkRepositoryError("Embedding dimensions do not match vector")

        existing_chunks = self._session.query(KnowledgeChunk).filter(
            KnowledgeChunk.source_document_id == source_document_id
        )
        existing_chunks.delete(synchronize_session=False)
        self._session.flush()

        inserted: List[KnowledgeChunk] = []
        for chunk in ordered_chunks:
            knowledge_chunk = KnowledgeChunk(
                source_document_id=source_document_id,
                notion_block_id=None,
                chunk_index=chunk.chunk_index,
                chunk_text=chunk.chunk_text,
                notion_path=None,
                embedding=chunk.embedding,
                embedding_text=self._serialize_embedding(chunk.embedding),
                source_kind=chunk.source_kind,
                source_display_name=chunk.source_display_name,
                locator=chunk.locator,
                citation_metadata=json.dumps(chunk.citation_metadata, sort_keys=True),
                embedding_model=chunk.embedding_model,
                embedding_dimensions=chunk.embedding_dimensions,
                owner_scope=chunk.owner_scope,
                eligibility_status=chunk.eligibility_status,
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
                KnowledgeChunk.source_document_id,
                KnowledgeChunk.source_display_name,
                KnowledgeChunk.locator,
                KnowledgeChunk.citation_metadata,
                SourceDocument.source_display_name.label("document_display_name"),
            )
            .outerjoin(NotionBlock, KnowledgeChunk.notion_block_id == NotionBlock.id)
            .outerjoin(NotionPage, NotionBlock.notion_page_id == NotionPage.id)
            .outerjoin(SourceDocument, KnowledgeChunk.source_document_id == SourceDocument.id)
            .filter(KnowledgeChunk.source_kind.in_(effective_source_kinds))
        )
        query = self._apply_eligibility_filter(query)
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
                source_document_id=row.source_document_id,
                source_display_name=(
                    row.source_display_name
                    or row.document_display_name
                    or row.notion_path
                ),
                locator=row.locator or row.notion_path,
                citation_metadata=row.citation_metadata,
                source_url=self._source_url_from_citation_metadata(
                    row.citation_metadata
                ),
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
                KnowledgeChunk.source_document_id,
                KnowledgeChunk.source_display_name,
                KnowledgeChunk.locator,
                KnowledgeChunk.citation_metadata,
                SourceDocument.source_display_name.label("document_display_name"),
            )
            .outerjoin(NotionBlock, KnowledgeChunk.notion_block_id == NotionBlock.id)
            .outerjoin(NotionPage, NotionBlock.notion_page_id == NotionPage.id)
            .outerjoin(SourceDocument, KnowledgeChunk.source_document_id == SourceDocument.id)
            .filter(
                KnowledgeChunk.source_kind.in_(effective_source_kinds),
                KnowledgeChunk.embedding.is_not(None),
            )
        )
        query = self._apply_eligibility_filter(query)
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
                source_document_id=row.source_document_id,
                source_display_name=(
                    row.source_display_name
                    or row.document_display_name
                    or row.notion_path
                ),
                locator=row.locator or row.notion_path,
                citation_metadata=row.citation_metadata,
                source_url=self._source_url_from_citation_metadata(
                    row.citation_metadata
                ),
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
        return [
            source_kind
            for source_kind in normalized_source_kinds
            if source_kind in {"notion", "pdf", "url", "image"}
        ]

    def _apply_eligibility_filter(self, query):
        return query.filter(
            KnowledgeChunk.eligibility_status == "eligible",
            or_(
                KnowledgeChunk.source_kind == "notion",
                and_(
                    KnowledgeChunk.source_kind == "pdf",
                    SourceDocument.source_type == "pdf",
                    SourceDocument.status == "indexed",
                ),
                and_(
                    KnowledgeChunk.source_kind == "url",
                    SourceDocument.source_type == "url",
                    SourceDocument.status == "indexed",
                ),
                and_(
                    KnowledgeChunk.source_kind == "image",
                    SourceDocument.source_type == "image",
                    SourceDocument.status == "indexed",
                ),
            ),
        )

    def _source_url_from_citation_metadata(
        self,
        citation_metadata: Optional[str],
    ) -> Optional[str]:
        if not citation_metadata:
            return None
        try:
            metadata = json.loads(citation_metadata)
        except json.JSONDecodeError:
            return None
        if not isinstance(metadata, dict):
            return None
        source_url = metadata.get("final_url") or metadata.get("source_url")
        if not isinstance(source_url, str) or not source_url.strip():
            return None
        return source_url.strip()

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
            return ["notion", "pdf", "url", "image"]
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
