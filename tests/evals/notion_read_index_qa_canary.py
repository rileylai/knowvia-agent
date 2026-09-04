"""Opt-in Notion read/index/QA canary for a dedicated synthetic workspace.

The canary uses the real Notion read adapter but deliberately blocks every
Notion write-shaped request. Database state is ephemeral SQLite state, and
embedding/answer providers are deterministic local adapters so the canary
does not require OpenAI credentials or create production data.
"""

from __future__ import annotations

import argparse
import asyncio
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
from src.db.models import KnowledgeChunk, NotionBlock, NotionPage  # noqa: E402
from src.db.unit_of_work import SqlAlchemyUnitOfWork  # noqa: E402
from src.orchestrators import (  # noqa: E402
    NotionFullIndexOrchestrator,
    NotionIncrementalIndexOrchestrator,
    NotionPageIndexOrchestrator,
    QAOrchestrator,
)
from src.providers import (  # noqa: E402
    EmbeddingClient,
    EmbeddingClientError,
    EmbeddingCapabilities,
    EmbeddingRequest,
    EmbeddingResponse,
    LLMMessage,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ProviderRouter,
)
from src.rag import ProductionChunkRetriever  # noqa: E402
from src.repositories import ChunkRepository, ChunkRepositoryError  # noqa: E402
from src.services import (  # noqa: E402
    STANDARD_FAILURE_REASONS,
    CostTracker,
    PromptTemplateLoader,
    WorkflowRunService,
)
from src.tools import (  # noqa: E402
    NotionAPIReaderClient,
    NotionHTTPResponse,
    NotionHTTPTransport,
    NotionHTTPTransportError,
    NotionReaderClient,
    NotionReaderTool,
    ToolRegistry,
    UrllibNotionHTTPTransport,
    normalize_notion_page_id,
)

RUN_FLAG_ENV = "LEARNLOOP_RUN_NOTION_READ_CANARY"
NOTION_TOKEN_ENV = "NOTION_TOKEN"
CANARY_PAGE_ID_ENV = "LEARNLOOP_NOTION_CANARY_PAGE_ID"
CANARY_QUERY_ENV = "LEARNLOOP_NOTION_CANARY_QUERY"
DEFAULT_CANARY_QUERY = "LearnLoop Step 82 canary anchor"
CANARY_PROVIDER = "notion-canary"
CANARY_MODEL = "deterministic-local"
EMBEDDING_DIMENSIONS = 1536


@dataclass(frozen=True)
class NotionHTTPAuditEntry:
    method: str
    path: str


class NotionCanaryWriteBlocked(NotionHTTPTransportError):
    """Raised if a live canary attempts a write-shaped Notion request."""


class NotionReadAudit:
    """Record safe request metadata without retaining credentials or content."""

    def __init__(self) -> None:
        self.entries: List[NotionHTTPAuditEntry] = []

    def record(self, *, method: str, path: str) -> None:
        self.entries.append(
            NotionHTTPAuditEntry(method=method.upper(), path=path.split("?", 1)[0])
        )

    @property
    def write_attempts(self) -> List[NotionHTTPAuditEntry]:
        return [
            entry
            for entry in self.entries
            if not _is_allowed_read_operation(entry)
        ]


class RecordingReadOnlyNotionHTTPTransport(NotionHTTPTransport):
    """Delegate reads while blocking all non-reader operations."""

    def __init__(
        self,
        *,
        audit: NotionReadAudit,
        delegate: Optional[NotionHTTPTransport] = None,
    ) -> None:
        self._audit = audit
        self._delegate = delegate or UrllibNotionHTTPTransport()

    def get_json(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
    ) -> NotionHTTPResponse:
        self._audit.record(method="GET", path=path)
        if not _is_allowed_read_operation(
            NotionHTTPAuditEntry(method="GET", path=path)
        ):
            raise NotionCanaryWriteBlocked("Notion canary blocked unexpected operation")
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
        if path.rstrip("/") != "/v1/search":
            raise NotionCanaryWriteBlocked("Notion canary blocked unexpected operation")
        return self._delegate.post_json(
            path=path,
            query=query,
            headers=headers,
            payload=payload,
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
        raise NotionCanaryWriteBlocked("Notion canary blocks all Notion writes")


def _is_allowed_read_operation(entry: NotionHTTPAuditEntry) -> bool:
    if entry.method == "POST":
        return entry.path.rstrip("/") == "/v1/search"
    if entry.method != "GET":
        return False
    return entry.path.startswith("/v1/pages/") or (
        entry.path.startswith("/v1/blocks/")
        and entry.path.endswith("/children")
    )


class _DeterministicEmbeddingClient(EmbeddingClient):
    def __init__(self, *, stage_tracker: "_CanaryStageTracker") -> None:
        self._stage_tracker = stage_tracker

    @property
    def name(self) -> str:
        return CANARY_PROVIDER

    def get_capabilities(self, *, model: str, dimensions: int):
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
        self._stage_tracker.failed_stage = "embedding"
        dimensions = request.dimensions or EMBEDDING_DIMENSIONS
        embeddings = []
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
            output_text="Deterministic canary answer grounded in indexed notes.",
            token_input=12,
            token_output=9,
        )


@dataclass
class _CanaryStageTracker:
    failed_stage: str = "setup"


class _StageTrackingPageIndexOrchestrator(NotionPageIndexOrchestrator):
    def __init__(self, *, stage_tracker: _CanaryStageTracker, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._stage_tracker = stage_tracker

    async def prepare_page_snapshot(self, **kwargs: Any):
        self._stage_tracker.failed_stage = "page_preparation"
        return await super().prepare_page_snapshot(**kwargs)

    def persist_prepared_page_snapshot(self, **kwargs: Any):
        self._stage_tracker.failed_stage = "db_persistence"
        return super().persist_prepared_page_snapshot(**kwargs)


@dataclass(frozen=True)
class NotionReadIndexQACanaryReport:
    status: str
    message: str
    failed_stage: Optional[str] = None
    failure_reason: Optional[str] = None
    failure_code: Optional[str] = None
    indexed_page_count: int = 0
    indexed_block_count: int = 0
    indexed_chunk_count: int = 0
    incremental_page_count: int = 0
    citation_count: int = 0
    notion_request_count: int = 0
    notion_write_attempt_count: int = 0
    notion_operations: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _skipped(message: str) -> NotionReadIndexQACanaryReport:
    return NotionReadIndexQACanaryReport(status="skipped", message=message)


async def run_canary_workflow(
    *,
    reader_client: NotionReaderClient,
    target_page_id: str,
    query: str,
    audit: Optional[NotionReadAudit] = None,
) -> NotionReadIndexQACanaryReport:
    stage_tracker = _CanaryStageTracker()
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
    embedding_client = _DeterministicEmbeddingClient(stage_tracker=stage_tracker)
    page_index_orchestrator = _StageTrackingPageIndexOrchestrator(
        stage_tracker=stage_tracker,
        tool_registry=tool_registry,
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        workflow_run_service=workflow_service,
        embedding_client=embedding_client,
        cost_tracker=CostTracker(),
    )
    full_index_orchestrator = NotionFullIndexOrchestrator(
        tool_registry=tool_registry,
        page_index_orchestrator=page_index_orchestrator,
        workflow_run_service=workflow_service,
    )
    incremental_orchestrator = NotionIncrementalIndexOrchestrator(
        page_index_orchestrator=page_index_orchestrator,
        workflow_run_service=workflow_service,
    )

    try:
        stage_tracker.failed_stage = "full_discovery"
        full_result = await full_index_orchestrator.index_all(
            request_workflow_id="step-82-notion-canary",
        )
        indexed_page_ids = {page.page_id for page in full_result.indexed_pages}
        if target_page_id not in indexed_page_ids:
            return _failed_report(
                audit,
                message="Notion read/index/QA canary failed at full discovery",
                failed_stage="full_discovery",
                failure_reason="NOTION_PAGE_NOT_FOUND",
                failure_code="NOTION_PAGE_NOT_FOUND",
            )

        stage_tracker.failed_stage = "incremental_index"
        incremental_result = await incremental_orchestrator.sync_pages(
            page_ids=[target_page_id],
            request_workflow_id="step-82-notion-canary-incremental",
        )

        qa_session = session_factory()
        try:
            stage_tracker.failed_stage = "qa"
            provider_router = ProviderRouter()
            provider_router.register_provider(_DeterministicLLMProvider())
            qa_orchestrator = QAOrchestrator(
                retriever=ProductionChunkRetriever(
                    chunk_repository=ChunkRepository(qa_session),
                ),
                embedding_client=embedding_client,
                provider_router=provider_router,
                cost_tracker=CostTracker(),
                prompt_template_loader=PromptTemplateLoader(),
                workflow_run_service=workflow_service,
            )
            qa_result = await qa_orchestrator.answer_question(
                query=query,
                top_k=5,
                page_ids=[target_page_id],
                section_paths=None,
                source_kinds=["notion"],
                provider_name=CANARY_PROVIDER,
                model=CANARY_MODEL,
                request_workflow_id="step-82-notion-canary-qa",
            )
        finally:
            qa_session.close()

        if qa_result.insufficient_info or not qa_result.citations:
            return _failed_report(
                audit,
                message="Notion read/index/QA canary failed at QA citation validation",
                failed_stage="qa",
                failure_reason="UNKNOWN_ERROR",
                failure_code="QA_CITATION_MISSING",
            )
        if any(citation.page_id != target_page_id for citation in qa_result.citations):
            return _failed_report(
                audit,
                message="Notion read/index/QA canary failed at QA citation validation",
                failed_stage="qa",
                failure_reason="WRITE_POLICY_VIOLATION",
                failure_code="QA_SCOPE_VIOLATION",
            )

        verification_session = session_factory()
        try:
            block_count = int(verification_session.query(func.count(NotionBlock.id)).scalar() or 0)
            chunk_count = int(verification_session.query(func.count(KnowledgeChunk.id)).scalar() or 0)
            page_count = int(verification_session.query(func.count(NotionPage.id)).scalar() or 0)
        finally:
            verification_session.close()

        operations = _render_operations(audit)
        if audit is not None and audit.write_attempts:
            return NotionReadIndexQACanaryReport(
                status="failed",
                message="Notion read/index/QA canary failed at write audit",
                failed_stage="write_audit",
                failure_reason="WRITE_POLICY_VIOLATION",
                failure_code="WRITE_POLICY_VIOLATION",
                notion_request_count=len(audit.entries),
                notion_write_attempt_count=len(audit.write_attempts),
                notion_operations=operations,
            )
        if audit is not None and not audit.entries:
            return _failed_report(
                audit,
                message="Notion read/index/QA canary failed at write audit",
                failed_stage="write_audit",
                failure_reason="UNKNOWN_ERROR",
                failure_code="NOTION_READ_AUDIT_EMPTY",
            )

        return NotionReadIndexQACanaryReport(
            status="passed",
            message="read/index/QA canary passed with no Notion writes",
            indexed_page_count=page_count,
            indexed_block_count=block_count,
            indexed_chunk_count=chunk_count,
            incremental_page_count=incremental_result.processed_page_count,
            citation_count=len(qa_result.citations),
            notion_request_count=len(audit.entries) if audit is not None else 0,
            notion_write_attempt_count=0,
            notion_operations=operations,
        )
    except Exception as exc:
        failure_reason, failure_code = _failure_details(exc)
        return _failed_report(
            audit,
            message=(
                "Notion read/index/QA canary failed at "
                f"{stage_tracker.failed_stage}"
            ),
            failed_stage=stage_tracker.failed_stage,
            failure_reason=failure_reason,
            failure_code=failure_code,
        )
    finally:
        engine.dispose()


def _failed_report(
    audit: Optional[NotionReadAudit],
    *,
    message: str,
    failed_stage: str,
    failure_reason: str,
    failure_code: Optional[str] = None,
) -> NotionReadIndexQACanaryReport:
    return NotionReadIndexQACanaryReport(
        status="failed",
        message=message,
        failed_stage=failed_stage,
        failure_reason=failure_reason,
        failure_code=failure_code,
        notion_request_count=len(audit.entries) if audit is not None else 0,
        notion_write_attempt_count=len(audit.write_attempts) if audit is not None else 0,
        notion_operations=_render_operations(audit),
    )


def _failure_details(exception: Optional[BaseException]) -> tuple[str, str]:
    if exception is None:
        return "UNKNOWN_ERROR", "UNKNOWN_ERROR"

    error_code: Optional[str] = None
    current: Optional[BaseException] = exception
    while current is not None:
        candidate_code = getattr(current, "error_code", None)
        if isinstance(candidate_code, str) and candidate_code.strip():
            error_code = candidate_code.strip().upper()
        candidate_reason = getattr(current, "failure_reason", None)
        if (
            isinstance(candidate_reason, str)
            and candidate_reason.upper() in STANDARD_FAILURE_REASONS
            and candidate_reason.upper() != "UNKNOWN_ERROR"
        ):
            return candidate_reason.upper(), error_code or candidate_reason.upper()
        if isinstance(current, NotionCanaryWriteBlocked):
            return "WRITE_POLICY_VIOLATION", "WRITE_POLICY_VIOLATION"
        if isinstance(current, EmbeddingClientError):
            return "EMBEDDING_PROVIDER_ERROR", "EMBEDDING_PROVIDER_ERROR"
        if isinstance(current, ChunkRepositoryError):
            return "VECTOR_UPSERT_FAILED", "VECTOR_UPSERT_FAILED"
        if isinstance(current, SQLAlchemyError):
            database_error_codes = {
                "CompileError": "DATABASE_COMPILE_ERROR",
                "DataError": "DATABASE_DATA_ERROR",
                "IntegrityError": "DATABASE_INTEGRITY_ERROR",
                "OperationalError": "DATABASE_OPERATIONAL_ERROR",
                "ProgrammingError": "DATABASE_PROGRAMMING_ERROR",
                "StatementError": "DATABASE_STATEMENT_ERROR",
            }
            error_type = type(current).__name__
            return "VECTOR_UPSERT_FAILED", database_error_codes.get(
                error_type,
                "DATABASE_ERROR",
            )
        if isinstance(current, NotionHTTPTransportError):
            return "NOTION_BLOCK_FETCH_FAILED", "NOTION_BLOCK_FETCH_FAILED"
        current = current.__cause__ or current.__context__
    return "UNKNOWN_ERROR", error_code or "UNKNOWN_ERROR"


def _render_operations(audit: Optional[NotionReadAudit]) -> List[str]:
    if audit is None:
        return []
    operations: List[str] = []
    for entry in audit.entries:
        if entry.method == "POST" and entry.path.rstrip("/") == "/v1/search":
            operations.append("POST /v1/search")
        elif entry.method == "GET" and entry.path.startswith("/v1/pages/"):
            operations.append("GET /v1/pages/{id}")
        elif entry.method == "GET" and entry.path.startswith("/v1/blocks/"):
            operations.append("GET /v1/blocks/{id}/children")
        else:
            operations.append("unexpected operation")
    return operations


def run_notion_read_index_qa_canary(
    *,
    include_live: bool = False,
    environment: Optional[Mapping[str, str]] = None,
) -> NotionReadIndexQACanaryReport:
    env = os.environ if environment is None else environment
    if not include_live:
        return _skipped("live Notion canary is disabled")

    token = env.get(NOTION_TOKEN_ENV, "").strip()
    target_page_id = normalize_notion_page_id(env.get(CANARY_PAGE_ID_ENV, ""))
    query = env.get(CANARY_QUERY_ENV, DEFAULT_CANARY_QUERY).strip()
    if not token or not target_page_id or not query:
        return _failed_report(
            None,
            message="live canary requires token, page id, and query",
            failed_stage="configuration",
            failure_reason="NOTION_AUTH_FAILED",
            failure_code="NOTION_AUTH_FAILED",
        )

    audit = NotionReadAudit()
    reader_client = NotionAPIReaderClient(
        token=token,
        transport=RecordingReadOnlyNotionHTTPTransport(audit=audit),
    )
    return asyncio.run(
        run_canary_workflow(
            reader_client=reader_client,
            target_page_id=target_page_id,
            query=query,
            audit=audit,
        )
    )


def render_report(
    report: NotionReadIndexQACanaryReport,
    *,
    as_json: bool = False,
) -> str:
    if as_json:
        return json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True)
    return (
        f"notion read/index/QA canary: {report.status} - {report.message}\n"
        f"- failed stage: {report.failed_stage or '-'}\n"
        f"- failure reason: {report.failure_reason or '-'}\n"
        f"- failure code: {report.failure_code or '-'}\n"
        f"- indexed pages: {report.indexed_page_count}\n"
        f"- indexed blocks: {report.indexed_block_count}\n"
        f"- indexed chunks: {report.indexed_chunk_count}\n"
        f"- incremental pages: {report.incremental_page_count}\n"
        f"- citations: {report.citation_count}\n"
        f"- Notion requests: {report.notion_request_count}\n"
        f"- Notion write attempts: {report.notion_write_attempt_count}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the guarded, read-only Notion indexing and QA canary."
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Opt in to a dedicated synthetic Notion workspace canary.",
    )
    parser.add_argument("--json", action="store_true", help="Render a redacted JSON report.")
    args = parser.parse_args()
    include_live = args.live or os.getenv(RUN_FLAG_ENV) == "1"
    report = run_notion_read_index_qa_canary(include_live=include_live)
    print(render_report(report, as_json=args.json))
    if report.status == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
