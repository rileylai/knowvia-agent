from __future__ import annotations

from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.db.models import SourceDocument


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
        raw_text: str,
        content_hash: str,
    ) -> SourceDocument:
        source_document = SourceDocument(
            source_type=source_type,
            source_display_name=source_display_name,
            raw_text=raw_text,
            content_hash=content_hash,
        )

        if self._session.bind is not None and self._session.bind.dialect.name == "sqlite":
            source_document.id = self._allocate_source_document_id_for_sqlite()

        self._session.add(source_document)
        self._session.flush()
        self._session.refresh(source_document)
        return source_document

    def get_source_document_by_id(self, source_document_id: int) -> Optional[SourceDocument]:
        return self._session.get(SourceDocument, source_document_id)
