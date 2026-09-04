from __future__ import annotations

from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.db.models import ChangeRequest


class ChangeRequestRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _allocate_change_request_id_for_sqlite(self) -> int:
        max_id_in_db = int(self._session.query(func.max(ChangeRequest.id)).scalar() or 0)
        max_id_in_identity_map = 0
        for instance in self._session.identity_map.values():
            if isinstance(instance, ChangeRequest) and instance.id is not None:
                max_id_in_identity_map = max(max_id_in_identity_map, int(instance.id))
        return max(max_id_in_db, max_id_in_identity_map) + 1

    def create_change_request(
        self,
        *,
        source_document_id: Optional[int],
        target_notion_page_id: Optional[int],
        status: str,
        proposal_json: str,
        failure_reason: Optional[str] = None,
    ) -> ChangeRequest:
        change_request = ChangeRequest(
            source_document_id=source_document_id,
            target_notion_page_id=target_notion_page_id,
            status=status,
            proposal_json=proposal_json,
            failure_reason=failure_reason,
        )

        if self._session.bind is not None and self._session.bind.dialect.name == "sqlite":
            change_request.id = self._allocate_change_request_id_for_sqlite()

        self._session.add(change_request)
        self._session.flush()
        self._session.refresh(change_request)
        return change_request

    def get_change_request_by_id(self, change_request_id: int) -> Optional[ChangeRequest]:
        return self._session.get(ChangeRequest, change_request_id)

    def list_change_requests(
        self,
        *,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[ChangeRequest]:
        if limit <= 0:
            raise ValueError("limit must be positive")

        query = self._session.query(ChangeRequest)
        if status is not None:
            query = query.filter(ChangeRequest.status == status)
        return list(
            query.order_by(ChangeRequest.created_at.desc(), ChangeRequest.id.desc())
            .limit(limit)
            .all()
        )

    def get_change_request_by_id_for_update(
        self,
        change_request_id: int,
    ) -> Optional[ChangeRequest]:
        return (
            self._session.query(ChangeRequest)
            .filter(ChangeRequest.id == change_request_id)
            .with_for_update()
            .one_or_none()
        )

    def update_change_request_status(
        self,
        change_request_id: int,
        *,
        status: str,
        failure_reason: Optional[str] = None,
    ) -> Optional[ChangeRequest]:
        change_request = self.get_change_request_by_id(change_request_id)
        if change_request is None:
            return None

        change_request.status = status
        change_request.failure_reason = failure_reason
        self._session.flush()
        self._session.refresh(change_request)
        return change_request

    def update_pending_target(
        self,
        change_request_id: int,
        *,
        target_notion_page_id: int,
    ) -> Optional[ChangeRequest]:
        change_request = self.get_change_request_by_id_for_update(change_request_id)
        if change_request is None:
            return None
        if change_request.status.strip().lower() != "pending":
            return change_request
        change_request.target_notion_page_id = target_notion_page_id
        self._session.flush()
        self._session.refresh(change_request)
        return change_request
