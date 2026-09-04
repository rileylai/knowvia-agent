from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from src.db.session import SessionFactory
from src.repositories import KnowledgeStatsRepository


@dataclass(frozen=True)
class KnowledgeStatsResult:
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


class KnowledgeStatsService:
    """Expose repository-backed aggregates with normalized safe timestamps."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def snapshot(self) -> KnowledgeStatsResult:
        session = self._session_factory()
        try:
            snapshot = KnowledgeStatsRepository(session).snapshot()
        finally:
            session.close()
        return KnowledgeStatsResult(
            page_count=snapshot.page_count,
            block_count=snapshot.block_count,
            chunk_count=snapshot.chunk_count,
            vector_count=snapshot.vector_count,
            proposal_count=snapshot.proposal_count,
            pending_proposal_count=snapshot.pending_proposal_count,
            accepted_proposal_count=snapshot.accepted_proposal_count,
            rejected_proposal_count=snapshot.rejected_proposal_count,
            latest_successful_full_index_at=_as_utc(
                snapshot.latest_successful_full_index_at
            ),
            latest_successful_incremental_sync_at=_as_utc(
                snapshot.latest_successful_incremental_sync_at
            ),
        )


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
