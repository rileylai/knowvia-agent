from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from src.db.models import ChangeRequest, KnowledgeChunk, NotionBlock, NotionPage, WorkflowRun


@dataclass(frozen=True)
class KnowledgeStatsSnapshot:
    page_count: int
    block_count: int
    chunk_count: int
    vector_count: int
    proposal_count: int
    pending_proposal_count: int
    accepted_proposal_count: int
    rejected_proposal_count: int
    latest_successful_full_index_at: Optional[datetime]
    latest_successful_incremental_sync_at: Optional[datetime]


class KnowledgeStatsRepository:
    """Read bounded aggregate knowledge-base statistics from PostgreSQL."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def snapshot(self) -> KnowledgeStatsSnapshot:
        return KnowledgeStatsSnapshot(
            page_count=self._count(NotionPage),
            block_count=self._count(NotionBlock),
            chunk_count=self._count(KnowledgeChunk),
            vector_count=self._count(
                KnowledgeChunk,
                KnowledgeChunk.embedding_text.is_not(None),
            ),
            proposal_count=self._count(ChangeRequest),
            pending_proposal_count=self._count(
                ChangeRequest,
                ChangeRequest.status == "pending",
            ),
            accepted_proposal_count=self._count(
                ChangeRequest,
                ChangeRequest.status == "accepted",
            ),
            rejected_proposal_count=self._count(
                ChangeRequest,
                ChangeRequest.status == "rejected",
            ),
            latest_successful_full_index_at=self._latest_successful_index_at(
                operation="index_full",
            ),
            latest_successful_incremental_sync_at=self._latest_successful_index_at(
                operation="index_incremental",
            ),
        )

    def _count(self, model, *criteria) -> int:
        query = self._session.query(model)
        if criteria:
            query = query.filter(*criteria)
        return int(query.count())

    def _latest_successful_index_at(self, *, operation: str) -> Optional[datetime]:
        patterns = (
            f'%"operation":"{operation}"%',
            f'%"operation": "{operation}"%',
        )
        workflow = (
            self._session.query(WorkflowRun)
            .filter(
                WorkflowRun.workflow_type == "indexing",
                WorkflowRun.status == "succeeded",
                WorkflowRun.finished_at.is_not(None),
                or_(*[WorkflowRun.metadata_json.like(pattern) for pattern in patterns]),
            )
            .order_by(WorkflowRun.finished_at.desc(), WorkflowRun.id.desc())
            .first()
        )
        return workflow.finished_at if workflow is not None else None
