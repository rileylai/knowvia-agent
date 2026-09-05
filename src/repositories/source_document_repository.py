from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.db.models import KnowledgeChunk, SourceDocument


@dataclass(frozen=True)
class IndexedSourceSummary:
    id: int
    display_name: str
    original_filename: Optional[str]
    source_preview: Optional[str]
    image_count: Optional[int]
    source_kind: str
    status: str
    chunk_count: int
    updated_at: Optional[datetime]
    content_hash: str
    file_hash: Optional[str]
    source_url: Optional[str] = None


# Backward-compatible name for callers written during the PDF-only inventory slice.
IndexedPDFSourceSummary = IndexedSourceSummary


class SourceDocumentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _allocate_source_document_id_for_sqlite(self) -> int:
        max_id_in_db = int(self._session.query(func.max(SourceDocument.id)).scalar() or 0)
        max_id_in_identity_map = 0
        for instance in self._session.identity_map.values():
            if isinstance(instance, SourceDocument) and instance.id is not None:
                max_id_in_identity_map = max(max_id_in_identity_map, int(instance.id))
        return max(max_id_in_db, max_id_in_identity_map) + 1

    def create_source_document(
        self,
        *,
        source_type: str,
        source_display_name: str,
        original_filename: Optional[str] = None,
        source_preview: Optional[str] = None,
        source_metadata: Optional[str] = None,
        image_count: Optional[int] = None,
        raw_text: str,
        content_hash: str,
        file_hash: Optional[str] = None,
        requested_url: Optional[str] = None,
        final_url: Optional[str] = None,
        owner_scope: str = "local",
        status: str = "parsed",
    ) -> SourceDocument:
        source_document = SourceDocument(
            source_type=source_type,
            source_display_name=source_display_name,
            original_filename=original_filename,
            source_preview=source_preview,
            source_metadata=source_metadata,
            image_count=image_count,
            raw_text=raw_text,
            content_hash=content_hash,
            file_hash=file_hash,
            requested_url=requested_url,
            final_url=final_url,
            owner_scope=owner_scope,
            status=status,
        )

        if self._session.bind is not None and self._session.bind.dialect.name == "sqlite":
            source_document.id = self._allocate_source_document_id_for_sqlite()

        self._session.add(source_document)
        self._session.flush()
        self._session.refresh(source_document)
        return source_document

    def find_indexed_url_source(
        self,
        *,
        final_url: str,
        content_hash: str,
        owner_scope: str = "local",
    ) -> Optional[IndexedSourceSummary]:
        row = self._indexed_source_query(source_types=["url"], owner_scope=owner_scope).filter(
            SourceDocument.final_url == final_url,
            SourceDocument.content_hash == content_hash,
        ).first()
        return self._to_summary(row) if row is not None else None

    def get_source_document_by_id(self, source_document_id: int) -> Optional[SourceDocument]:
        return self._session.get(SourceDocument, source_document_id)

    def find_indexed_pdf_source_by_file_hash(
        self,
        *,
        file_hash: str,
        owner_scope: str = "local",
    ) -> Optional[IndexedSourceSummary]:
        row = self._indexed_source_query(
            source_types=["pdf"],
            owner_scope=owner_scope,
        ).filter(
            SourceDocument.file_hash == file_hash
        ).first()
        return self._to_summary(row) if row is not None else None

    def find_indexed_image_source_by_file_hash(
        self,
        *,
        file_hash: str,
        owner_scope: str = "local",
    ) -> Optional[IndexedSourceSummary]:
        row = self._indexed_source_query(
            source_types=["image"],
            owner_scope=owner_scope,
        ).filter(SourceDocument.file_hash == file_hash).first()
        return self._to_summary(row) if row is not None else None

    def list_indexed_pdf_sources(
        self,
        *,
        owner_scope: str = "local",
    ) -> list[IndexedSourceSummary]:
        return self.list_indexed_sources(
            owner_scope=owner_scope,
            source_types=["pdf"],
        )

    def list_indexed_sources(
        self,
        *,
        owner_scope: str = "local",
        source_types: Optional[list[str]] = None,
    ) -> list[IndexedSourceSummary]:
        return [
            self._to_summary(row)
            for row in self._indexed_source_query(
                owner_scope=owner_scope,
                source_types=source_types,
            ).all()
        ]

    def _indexed_source_query(
        self,
        *,
        owner_scope: str,
        source_types: Optional[list[str]] = None,
    ):
        eligible_chunk_counts = (
            self._session.query(
                KnowledgeChunk.source_document_id.label("source_document_id"),
                func.count(KnowledgeChunk.id).label("chunk_count"),
            )
            .filter(
                KnowledgeChunk.source_kind.in_(["pdf", "url", "image"]),
                KnowledgeChunk.eligibility_status == "eligible",
                KnowledgeChunk.owner_scope == owner_scope,
            )
            .group_by(KnowledgeChunk.source_document_id)
            .subquery()
        )
        query = self._session.query(
            SourceDocument.id.label("id"),
            SourceDocument.source_display_name.label("display_name"),
            SourceDocument.original_filename.label("original_filename"),
            SourceDocument.source_preview.label("source_preview"),
            SourceDocument.image_count.label("image_count"),
            SourceDocument.source_type.label("source_kind"),
            SourceDocument.status.label("status"),
            func.coalesce(eligible_chunk_counts.c.chunk_count, 0).label(
                "chunk_count"
            ),
            SourceDocument.updated_at.label("updated_at"),
            SourceDocument.content_hash.label("content_hash"),
            SourceDocument.file_hash.label("file_hash"),
            func.coalesce(
                SourceDocument.final_url,
                SourceDocument.requested_url,
            ).label("source_url"),
        ).outerjoin(
            eligible_chunk_counts,
            eligible_chunk_counts.c.source_document_id == SourceDocument.id,
        ).filter(
            SourceDocument.owner_scope == owner_scope,
            SourceDocument.status == "indexed",
        )
        if source_types:
            query = query.filter(SourceDocument.source_type.in_(source_types))
        return query.order_by(SourceDocument.updated_at.desc(), SourceDocument.id.desc())

    def _to_summary(self, row) -> IndexedSourceSummary:
        return IndexedSourceSummary(
            id=int(row.id),
            display_name=str(row.display_name),
            original_filename=row.original_filename,
            source_preview=row.source_preview,
            image_count=row.image_count,
            source_kind=str(row.source_kind),
            status=str(row.status),
            chunk_count=int(row.chunk_count or 0),
            updated_at=row.updated_at,
            content_hash=str(row.content_hash),
            file_hash=row.file_hash,
            source_url=row.source_url,
        )

    def update_status(self, *, source_document_id: int, status: str) -> SourceDocument:
        source_document = self.get_source_document_by_id(source_document_id)
        if source_document is None:
            raise ValueError(f"SourceDocument not found: {source_document_id}")
        normalized_status = status.strip().lower()
        if not normalized_status:
            raise ValueError("status must not be empty")
        source_document.status = normalized_status
        self._session.flush()
        self._session.refresh(source_document)
        return source_document
