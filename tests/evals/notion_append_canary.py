"""Opt-in Notion append/re-index canary for a dedicated sandbox page.

This canary exercises the existing human-review accept path with ephemeral
SQLite state and a real Notion REST reader/writer. It requires two explicit
operator gates: ``--live`` and ``--approve``. The transport allowlist permits
only page/block reads and append-only block-child PATCH requests.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from sqlalchemy import create_engine, func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.db.base import Base  # noqa: E402
from src.db.models import ChangeRequest, KnowledgeChunk, NotionBlock, NotionPage  # noqa: E402
from src.db.unit_of_work import SqlAlchemyUnitOfWork  # noqa: E402
from src.orchestrators import (  # noqa: E402
    NotionPageIndexOrchestrator,
    QAOrchestrator,
    SupplementReviewOrchestrator,
)
from src.providers import (  # noqa: E402
    EmbeddingClient,
    EmbeddingCapabilities,
    EmbeddingRequest,
    EmbeddingResponse,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ProviderRouter,
)
from src.rag import ProductionChunkRetriever  # noqa: E402
from src.repositories import (  # noqa: E402
    ChangeRequestRepository,
    ChunkRepository,
    NotionPageRepository,
)
from src.services import CostTracker, PromptTemplateLoader, WorkflowRunService  # noqa: E402
from src.tools import (  # noqa: E402
    NotionAPIReaderClient,
    NotionAPIWriterClient,
    NotionHTTPResponse,
    NotionHTTPTransport,
    NotionHTTPTransportError,
    NotionReaderTool,
    NotionWriterTool,
    ToolRegistry,
    normalize_notion_page_id,
)

RUN_FLAG_ENV = "LEARNLOOP_RUN_NOTION_APPEND_CANARY"
NOTION_TOKEN_ENV = "NOTION_TOKEN"
CANARY_PAGE_ID_ENV = "LEARNLOOP_NOTION_CANARY_PAGE_ID"
DEFAULT_QUERY = "LearnLoop Step 83 append canary"
CANARY_PROVIDER = "notion-append-canary"
CANARY_MODEL = "deterministic-local"
EMBEDDING_DIMENSIONS = 1536
APPROVAL_FAILURE_CODE = "HUMAN_APPROVAL_REQUIRED"


@dataclass(frozen=True)
class NotionHTTPAuditEntry:
    method: str
    path: str


class NotionAppendCanaryWriteBlocked(NotionHTTPTransportError):
    """Raised when the canary sees a non-allowlisted Notion operation."""


class NotionAppendAudit:
    """Record only operation classes; never expose page ids or payloads."""

    def __init__(self) -> None:
        self.entries: List[NotionHTTPAuditEntry] = []

    def record(self, *, method: str, path: str) -> None:
        self.entries.append(
            NotionHTTPAuditEntry(method=method.upper(), path=path.split("?", 1)[0])
        )

    @property
    def unexpected_operations(self) -> List[NotionHTTPAuditEntry]:
        return [entry for entry in self.entries if not _is_allowed_operation(entry)]


class RecordingAppendCanaryTransport(NotionHTTPTransport):
    """Allow reads plus append-only block-child PATCH requests."""

    def __init__(
        self,
        *,
        audit: NotionAppendAudit,
        delegate: Optional[NotionHTTPTransport] = None,
    ) -> None:
        self._audit = audit
        self._delegate = delegate

    def get_json(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
    ) -> NotionHTTPResponse:
        self._audit.record(method="GET", path=path)
        entry = NotionHTTPAuditEntry(method="GET", path=path)
        if not _is_allowed_operation(entry):
            raise NotionAppendCanaryWriteBlocked(
                "Notion append canary blocked unexpected GET"
            )
        if self._delegate is None:
            raise NotionHTTPTransportError("Notion append canary transport is not configured")
        return self._delegate.get_json(path=path, query=query, headers=headers)

    def post_json(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
    ) -> NotionHTTPResponse:
        self._audit.record(method="POST", path=path)
        raise NotionAppendCanaryWriteBlocked(
            "Notion append canary blocks all POST requests"
        )

    def patch_json(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
    ) -> NotionHTTPResponse:
        self._audit.record(method="PATCH", path=path)
        entry = NotionHTTPAuditEntry(method="PATCH", path=path)
        if not _is_allowed_operation(entry):
            raise NotionAppendCanaryWriteBlocked(
                "Notion append canary blocked non-append PATCH"
            )
        if self._delegate is None:
            raise NotionHTTPTransportError("Notion append canary transport is not configured")
        return self._delegate.patch_json(
            path=path,
            query=query,
            headers=headers,
            payload=payload,
        )


def _is_allowed_operation(entry: NotionHTTPAuditEntry) -> bool:
    if entry.method == "GET":
        return entry.path.startswith("/v1/pages/") or (
            entry.path.startswith("/v1/blocks/")
            and entry.path.endswith("/children")
        )
    return entry.method == "PATCH" and entry.path.startswith("/v1/blocks/") and entry.path.endswith(
        "/children"
    )


@dataclass
class _DeterministicEmbeddingClient(EmbeddingClient):
    @property
    def name(self) -> str:
        return CANARY_PROVIDER

    def get_capabilities(
        self,
        *,
        model: str,
        dimensions: int,
    ) -> Optional[EmbeddingCapabilities]:
        if dimensions != EMBEDDING_DIMENSIONS:
            return None
        return EmbeddingCapabilities(
            provider=CANARY_PROVIDER,
            model=model,
            dimensions=dimensions,
            max_input_count=2048,
            max_single_input_tokens=8192,
            max_aggregate_tokens=300000,
            tokenizer_model=model,
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        dimensions = request.dimensions or EMBEDDING_DIMENSIONS
        embeddings: List[List[float]] = []
        for value in request.inputs:
            checksum = sum(value.encode("utf-8")) % 997
            vector = [0.0] * dimensions
            vector[checksum % dimensions] = 1.0
            embeddings.append(vector)
        return EmbeddingResponse(
            provider=CANARY_PROVIDER,
            model=request.model or CANARY_MODEL,
            embeddings=embeddings,
            indices=list(range(len(request.inputs))),
            token_input=sum(len(value.split()) for value in request.inputs),
        )


class _DeterministicLLMProvider(LLMProvider):
    @property
    def name(self) -> str:
        return CANARY_PROVIDER

    async def generate(self, request: LLMRequest) -> LLMResponse:
        _ = request
        return LLMResponse(
            provider=CANARY_PROVIDER,
            model=CANARY_MODEL,
            output_text="Deterministic canary answer grounded in the appended note.",
            token_input=12,
            token_output=10,
        )


@dataclass(frozen=True)
class NotionAppendCanaryReport:
    status: str
    message: str
    failed_stage: Optional[str] = None
    failure_reason: Optional[str] = None
    failure_code: Optional[str] = None
    change_request_status: Optional[str] = None
    indexed_block_count: int = 0
    indexed_chunk_count: int = 0
    citation_count: int = 0
    append_block_count: int = 0
    identity_visible: bool = False
    idempotent_replay: bool = False
    notion_request_count: int = 0
    notion_unexpected_operation_count: int = 0
    notion_operations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


async def run_append_canary_workflow(
    *,
    reader_client: Any,
    writer_client: Any,
    target_page_id: str,
    audit: Optional[NotionAppendAudit] = None,
) -> NotionAppendCanaryReport:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    workflow_service = WorkflowRunService(session_factory)
    tool_registry = ToolRegistry()
    tool_registry.register_tool(NotionReaderTool(reader_client))
    tool_registry.register_tool(NotionWriterTool(writer_client))
    embedding_client = _DeterministicEmbeddingClient()
    page_index_orchestrator = NotionPageIndexOrchestrator(
        tool_registry=tool_registry,
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        workflow_run_service=workflow_service,
        embedding_client=embedding_client,
        cost_tracker=CostTracker(),
    )

    normalized_page_id = normalize_notion_page_id(target_page_id)
    try:
        await page_index_orchestrator.index_page(
            page_id=normalized_page_id,
            request_workflow_id="step-83-notion-append-initial-index",
        )

        with SqlAlchemyUnitOfWork(session_factory) as unit_of_work:
            page = unit_of_work.notion_pages.get_by_notion_page_id(normalized_page_id)
            if page is None:
                return _failed_report(
                    audit,
                    message="Notion append canary failed at initial index",
                    failed_stage="initial_index",
                    failure_reason="NOTION_PAGE_NOT_FOUND",
                    failure_code="NOTION_PAGE_NOT_FOUND",
                )
            source_text = "Synthetic Step 83 append canary source."
            source = unit_of_work.source_documents.create_source_document(
                source_type="chat_text",
                source_display_name="LearnLoop Step 83 sandbox proposal",
                raw_text=source_text,
                content_hash=hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
            )
            proposal_json = json.dumps(
                {
                    "title": "Step 83 Append Canary Supplement",
                    "target_path": f"{page.notion_path}/AI Supplement Zone",
                    "source": {
                        "source_type": "chat_text",
                        "source_display_name": "LearnLoop Step 83 sandbox proposal",
                    },
                    "summary": "Synthetic append canary content for release verification.",
                    "concepts": ["append-only", "durable identity", "re-index"],
                    "notes": ["Created only for the explicitly approved sandbox canary."],
                    "citations": [
                        {
                            "source_type": "chat_text",
                            "source_display_name": "LearnLoop Step 83 sandbox proposal",
                        }
                    ],
                },
                sort_keys=True,
            )
            change_request = unit_of_work.change_requests.create_change_request(
                source_document_id=source.id,
                target_notion_page_id=page.id,
                status="pending",
                proposal_json=proposal_json,
            )
            change_request_id = int(change_request.id)

        # The review repository objects share one request session. They are
        # only used for the read-before-accept lookup; mutation is owned by the
        # fresh UoW inside the orchestrator.
        review_session = session_factory()
        review_orchestrator = SupplementReviewOrchestrator(
            change_request_repository=ChangeRequestRepository(review_session),
            notion_page_repository=NotionPageRepository(review_session),
            unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
            tool_registry=tool_registry,
            page_index_orchestrator=page_index_orchestrator,
            workflow_run_service=workflow_service,
        )
        try:
            review_result = await review_orchestrator.accept_change_request(
                change_request_id=change_request_id,
                reviewer="step-83-human-approved",
                request_workflow_id="step-83-notion-append-accept",
            )
        finally:
            review_session.close()

        verification_result = writer_client.find_ai_supplement_by_identity(
            page_id=normalized_page_id,
            idempotency_key=f"change-request-{change_request_id}",
            change_request_id=change_request_id,
        )
        identity_visible = verification_result is not None
        indexed_block_count, indexed_chunk_count, stored_status = _read_db_state(
            session_factory=session_factory,
            change_request_id=change_request_id,
            page_id=normalized_page_id,
        )

        qa_session = session_factory()
        try:
            provider_router = ProviderRouter()
            provider_router.register_provider(_DeterministicLLMProvider())
            qa_result = await QAOrchestrator(
                retriever=ProductionChunkRetriever(
                    chunk_repository=ChunkRepository(qa_session),
                ),
                embedding_client=embedding_client,
                provider_router=provider_router,
                cost_tracker=CostTracker(),
                prompt_template_loader=PromptTemplateLoader(),
                workflow_run_service=workflow_service,
            ).answer_question(
                query=DEFAULT_QUERY,
                top_k=5,
                page_ids=[normalized_page_id],
                section_paths=None,
                source_kinds=["notion"],
                provider_name=CANARY_PROVIDER,
                model=CANARY_MODEL,
                request_workflow_id="step-83-notion-append-qa",
            )
        finally:
            qa_session.close()

        citations_ok = bool(qa_result.citations) and all(
            citation.page_id == normalized_page_id for citation in qa_result.citations
        )
        if (
            review_result.change_request_status != "accepted"
            or stored_status != "accepted"
            or not identity_visible
            or verification_result is None
            or not citations_ok
        ):
            return _failed_report(
                audit,
                message="Notion append canary failed durable state reconciliation",
                failed_stage="reconciliation",
                failure_reason="WRITE_POLICY_VIOLATION",
                failure_code="CANARY_STATE_MISMATCH",
                change_request_status=stored_status,
                indexed_block_count=indexed_block_count,
                indexed_chunk_count=indexed_chunk_count,
                citation_count=len(qa_result.citations),
                append_block_count=(verification_result.appended_block_count if verification_result else 0),
                identity_visible=identity_visible,
                idempotent_replay=(verification_result.idempotent_replay if verification_result else False),
            )

        if audit is not None and audit.unexpected_operations:
            return _failed_report(
                audit,
                message="Notion append canary failed operation audit",
                failed_stage="write_audit",
                failure_reason="WRITE_POLICY_VIOLATION",
                failure_code="WRITE_POLICY_VIOLATION",
                change_request_status=stored_status,
                indexed_block_count=indexed_block_count,
                indexed_chunk_count=indexed_chunk_count,
                citation_count=len(qa_result.citations),
                append_block_count=verification_result.appended_block_count,
                identity_visible=identity_visible,
                idempotent_replay=verification_result.idempotent_replay,
            )

        return NotionAppendCanaryReport(
            status="passed",
            message="Notion append/re-index canary passed",
            change_request_status=stored_status,
            indexed_block_count=indexed_block_count,
            indexed_chunk_count=indexed_chunk_count,
            citation_count=len(qa_result.citations),
            append_block_count=verification_result.appended_block_count,
            identity_visible=True,
            idempotent_replay=verification_result.idempotent_replay,
            notion_request_count=len(audit.entries) if audit is not None else 0,
            notion_unexpected_operation_count=0,
            notion_operations=_render_operations(audit),
        )
    except Exception as exc:
        return _failed_report(
            audit,
            message="Notion append canary failed",
            failed_stage="workflow",
            failure_reason=_failure_reason(exc),
            failure_code=getattr(exc, "error_code", None) or type(exc).__name__,
        )
    finally:
        engine.dispose()


def _read_db_state(*, session_factory: Any, change_request_id: int, page_id: str) -> tuple[int, int, str]:
    session = session_factory()
    try:
        page = session.query(NotionPage).filter(NotionPage.notion_page_id == page_id).one()
        return (
            int(session.query(func.count(NotionBlock.id)).filter(NotionBlock.notion_page_id == page.id).scalar() or 0),
            int(
                session.query(func.count(KnowledgeChunk.id))
                .join(NotionBlock, KnowledgeChunk.notion_block_id == NotionBlock.id)
                .filter(NotionBlock.notion_page_id == page.id)
                .scalar()
                or 0
            ),
            session.query(ChangeRequest.status)
            .filter(ChangeRequest.id == change_request_id)
            .scalar()
            or "",
        )
    finally:
        session.close()


def _failed_report(
    audit: Optional[NotionAppendAudit],
    *,
    message: str,
    failed_stage: str,
    failure_reason: str,
    failure_code: Optional[str] = None,
    change_request_status: Optional[str] = None,
    indexed_block_count: int = 0,
    indexed_chunk_count: int = 0,
    citation_count: int = 0,
    append_block_count: int = 0,
    identity_visible: bool = False,
    idempotent_replay: bool = False,
) -> NotionAppendCanaryReport:
    return NotionAppendCanaryReport(
        status="failed",
        message=message,
        failed_stage=failed_stage,
        failure_reason=failure_reason,
        failure_code=failure_code,
        change_request_status=change_request_status,
        indexed_block_count=indexed_block_count,
        indexed_chunk_count=indexed_chunk_count,
        citation_count=citation_count,
        append_block_count=append_block_count,
        identity_visible=identity_visible,
        idempotent_replay=idempotent_replay,
        notion_request_count=len(audit.entries) if audit is not None else 0,
        notion_unexpected_operation_count=(len(audit.unexpected_operations) if audit is not None else 0),
        notion_operations=_render_operations(audit),
    )


def _failure_reason(exception: BaseException) -> str:
    candidate = getattr(exception, "failure_reason", None)
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip().upper()
    if isinstance(exception, SQLAlchemyError):
        return "VECTOR_UPSERT_FAILED"
    if isinstance(exception, NotionHTTPTransportError):
        return "NOTION_BLOCK_FETCH_FAILED"
    return "UNKNOWN_ERROR"


def _render_operations(audit: Optional[NotionAppendAudit]) -> List[str]:
    if audit is None:
        return []
    rendered: List[str] = []
    for entry in audit.entries:
        if entry.method == "GET" and entry.path.startswith("/v1/pages/"):
            rendered.append("GET /v1/pages/{id}")
        elif entry.method == "GET" and entry.path.startswith("/v1/blocks/"):
            rendered.append("GET /v1/blocks/{id}/children")
        elif entry.method == "PATCH" and entry.path.startswith("/v1/blocks/"):
            rendered.append("PATCH /v1/blocks/{id}/children")
        else:
            rendered.append("unexpected operation")
    return rendered


def run_notion_append_canary(
    *,
    include_live: bool = False,
    approval_confirmed: bool = False,
    environment: Optional[Mapping[str, str]] = None,
) -> NotionAppendCanaryReport:
    if not include_live:
        return NotionAppendCanaryReport(
            status="skipped",
            message="live Notion append canary is disabled",
        )

    env = os.environ if environment is None else environment
    if not approval_confirmed:
        return _failed_report(
            None,
            message="live append canary requires explicit human approval",
            failed_stage="configuration",
            failure_reason="WRITE_POLICY_VIOLATION",
            failure_code=APPROVAL_FAILURE_CODE,
        )
    token = env.get(NOTION_TOKEN_ENV, "").strip()
    target_page_id = normalize_notion_page_id(env.get(CANARY_PAGE_ID_ENV, ""))
    if not token or not target_page_id:
        return _failed_report(
            None,
            message="live append canary requires token and sandbox page id",
            failed_stage="configuration",
            failure_reason="NOTION_AUTH_FAILED",
            failure_code="NOTION_AUTH_FAILED",
        )

    audit = NotionAppendAudit()
    from src.tools import UrllibNotionHTTPTransport

    transport = RecordingAppendCanaryTransport(
        audit=audit,
        delegate=UrllibNotionHTTPTransport(),
    )
    reader_client = NotionAPIReaderClient(token=token, transport=transport)
    writer_client = NotionAPIWriterClient(token=token, transport=transport)
    return asyncio.run(
        run_append_canary_workflow(
            reader_client=reader_client,
            writer_client=writer_client,
            target_page_id=target_page_id,
            audit=audit,
        )
    )


def render_report(report: NotionAppendCanaryReport, *, as_json: bool = False) -> str:
    if as_json:
        return json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True)
    return (
        f"notion append/re-index canary: {report.status} - {report.message}\n"
        f"- failed stage: {report.failed_stage or '-'}\n"
        f"- failure reason: {report.failure_reason or '-'}\n"
        f"- change request status: {report.change_request_status or '-'}\n"
        f"- indexed blocks/chunks: {report.indexed_block_count}/{report.indexed_chunk_count}\n"
        f"- citations: {report.citation_count}\n"
        f"- appended blocks: {report.append_block_count}\n"
        f"- identity visible: {report.identity_visible}\n"
        f"- idempotent replay: {report.idempotent_replay}\n"
        f"- Notion requests: {report.notion_request_count}\n"
        f"- unexpected operations: {report.notion_unexpected_operation_count}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the explicitly approved, append-only Notion canary."
    )
    parser.add_argument("--live", action="store_true", help="Opt in to live Notion access.")
    parser.add_argument(
        "--approve",
        action="store_true",
        help="Confirm that this run may append to the configured sandbox page.",
    )
    parser.add_argument("--json", action="store_true", help="Render a redacted JSON report.")
    args = parser.parse_args()
    include_live = args.live or os.getenv(RUN_FLAG_ENV) == "1"
    report = run_notion_append_canary(
        include_live=include_live,
        approval_confirmed=args.approve,
    )
    print(render_report(report, as_json=args.json))
    if report.status == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
