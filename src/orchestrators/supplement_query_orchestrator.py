from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from typing import List, Optional

from src.orchestrators.supplement_proposal_schema import (
    SupplementProposalCitationSchema,
    SupplementProposalSchema,
    SupplementProposalValidationError,
    parse_supplement_proposal_json,
)
from src.repositories import ChangeRequestRepository, NotionPageRepository

CHANGE_REQUEST_STATUS_PENDING = "pending"


@dataclass
class SupplementCitationResult:
    source_type: Optional[str]
    source_display_name: Optional[str]
    notion_path: Optional[str]
    page_id: Optional[str]
    quote: Optional[str]


@dataclass
class SupplementProposalContentResult:
    title: str
    target_path: str
    source_type: str
    source_display_name: str
    summary: str
    concepts: List[str]
    notes: List[str]


@dataclass
class SupplementTargetResult:
    page_id: str
    title: str
    notion_path: str


@dataclass
class SupplementReviewItemResult:
    change_request_id: int
    status: str
    source_document_id: Optional[int]
    target_notion_page_id: Optional[str]
    target_page: Optional[SupplementTargetResult]
    proposal: SupplementProposalContentResult
    citations: List[SupplementCitationResult]
    created_at: Optional[datetime]


class SupplementQueryError(Exception):
    def __init__(
        self,
        *,
        error_code: str,
        message: str,
        http_status_code: int,
        failure_reason: str = "UNKNOWN_ERROR",
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.http_status_code = http_status_code
        self.failure_reason = failure_reason


class SupplementQueryOrchestrator:
    def __init__(
        self,
        *,
        change_request_repository: ChangeRequestRepository,
        notion_page_repository: NotionPageRepository,
    ) -> None:
        self._change_request_repository = change_request_repository
        self._notion_page_repository = notion_page_repository

    def list_pending(self, *, limit: int = 50) -> List[SupplementReviewItemResult]:
        return [
            self._build_item(change_request)
            for change_request in self._change_request_repository.list_change_requests(
                status=CHANGE_REQUEST_STATUS_PENDING,
                limit=limit,
            )
        ]

    def get_detail(self, *, change_request_id: int) -> SupplementReviewItemResult:
        if change_request_id <= 0:
            raise SupplementQueryError(
                error_code="INVALID_ARGUMENT",
                message="change_request_id must be positive",
                http_status_code=HTTPStatus.BAD_REQUEST,
            )

        change_request = self._change_request_repository.get_change_request_by_id(
            change_request_id
        )
        if change_request is None:
            raise SupplementQueryError(
                error_code="CHANGE_REQUEST_NOT_FOUND",
                message=(
                    "Change request is not found: "
                    f"change_request_id={change_request_id}"
                ),
                http_status_code=HTTPStatus.NOT_FOUND,
                failure_reason="CHANGE_REQUEST_NOT_FOUND",
            )
        return self._build_item(change_request)

    def _build_item(self, change_request) -> SupplementReviewItemResult:
        try:
            proposal = parse_supplement_proposal_json(change_request.proposal_json)
        except SupplementProposalValidationError as exc:
            raise SupplementQueryError(
                error_code="INVALID_PROPOSAL_PAYLOAD",
                message=f"Stored proposal_json is invalid: {exc.message}",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            ) from exc

        target_page = None
        target_notion_page_id = None
        if change_request.target_notion_page_id is not None:
            target_page_record = self._notion_page_repository.get_by_id(
                int(change_request.target_notion_page_id)
            )
            if target_page_record is not None:
                target_notion_page_id = target_page_record.notion_page_id
                target_page = SupplementTargetResult(
                    page_id=target_page_record.notion_page_id,
                    title=target_page_record.title,
                    notion_path=target_page_record.notion_path,
                )

        citations = [
            self._citation_result(citation) for citation in proposal.citations
        ]
        if not citations:
            citations = [
                SupplementCitationResult(
                    source_type=proposal.source.source_type,
                    source_display_name=proposal.source.source_display_name,
                    notion_path=None,
                    page_id=None,
                    quote=None,
                )
            ]

        return SupplementReviewItemResult(
            change_request_id=int(change_request.id),
            status=change_request.status,
            source_document_id=(
                int(change_request.source_document_id)
                if change_request.source_document_id is not None
                else None
            ),
            target_notion_page_id=target_notion_page_id,
            target_page=target_page,
            proposal=SupplementProposalContentResult(
                title=proposal.title,
                target_path=proposal.target_path,
                source_type=proposal.source.source_type,
                source_display_name=proposal.source.source_display_name,
                summary=proposal.summary,
                concepts=list(proposal.concepts),
                notes=list(proposal.notes),
            ),
            citations=citations,
            created_at=change_request.created_at,
        )

    def _citation_result(
        self,
        citation: SupplementProposalCitationSchema,
    ) -> SupplementCitationResult:
        return SupplementCitationResult(
            source_type=citation.source_type,
            source_display_name=citation.source_display_name,
            notion_path=citation.notion_path,
            page_id=citation.page_id,
            quote=citation.quote,
        )
