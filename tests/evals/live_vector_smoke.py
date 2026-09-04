from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.app.config import get_settings  # noqa: E402
from src.db.models import KnowledgeChunk, NotionBlock, NotionPage, WorkflowRun  # noqa: E402
from src.db.unit_of_work import SqlAlchemyUnitOfWork  # noqa: E402
from src.orchestrators import NotionPageIndexOrchestrator, QAOrchestrator  # noqa: E402
from src.orchestrators.qa_orchestrator import INSUFFICIENT_INFO_ANSWER  # noqa: E402
from src.providers import (  # noqa: E402
    EmbeddingRequest,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    OpenAIEmbeddingClient,
    ProviderRouter,
)
from src.rag import (  # noqa: E402
    ProductionChunkRetriever,
    RETRIEVAL_MODE_PGVECTOR_EXACT_COSINE,
)
from src.repositories import (  # noqa: E402
    ChunkRepository,
)
from src.services import CostTracker, PromptTemplateLoader, WorkflowRunService  # noqa: E402
from src.tools import (  # noqa: E402
    InMemoryNotionReaderClient,
    NotionBlockNode,
    NotionPageTree,
    NotionReaderTool,
    ToolRegistry,
)

RUN_FLAG_ENV = "LEARNLOOP_RUN_LIVE_VECTOR_SMOKE"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
ADMIN_DATABASE_URL_ENV = "LEARNLOOP_PGVECTOR_ADMIN_DATABASE_URL"
DEFAULT_ADMIN_DATABASE_URL = (
    "postgresql+psycopg://learnloop:learnloop@localhost:5432/postgres"
)
VECTOR_DIMENSIONS = 1536

MAIN_PAGE_ID = "page-live-vector-smoke-main"
SECONDARY_PAGE_ID = "page-live-vector-smoke-secondary"
MAIN_PAGE_PATH = "Knowledge/Eval/Live Vector Smoke"
SECONDARY_PAGE_PATH = "Knowledge/Eval/Live Vector Distractor"
EXACT_SECTION_TITLE = "Vector Retrieval"
EXACT_SECTION_PATH = f"{MAIN_PAGE_PATH}/{EXACT_SECTION_TITLE}"
EXACT_RETRIEVAL_QUERY = "live smoke vector exact retrieval anchor canonical note"
DUPLICATE_SECTION_TITLE = "Duplicate Citation Section"
DUPLICATE_SECTION_PATH = f"{MAIN_PAGE_PATH}/{DUPLICATE_SECTION_TITLE}"
DUPLICATE_CITATION_QUERY = "live smoke duplicate citation anchor"
MISSING_SECTION_PATH = f"{MAIN_PAGE_PATH}/Missing Section"
EXPECTED_MAIN_PAGE_CHUNK_COUNT = 3
EXPECTED_TOTAL_CHUNK_COUNT = 4


class LiveVectorSmokePrereqError(Exception):
    pass


@dataclass(frozen=True)
class LiveVectorSmokeConfig:
    openai_api_key: str
    admin_database_url: str
    keep_database_on_failure: bool = False


@dataclass(frozen=True)
class LiveVectorSmokeCheckResult:
    check_id: str
    passed: bool
    message: str


@dataclass(frozen=True)
class LiveVectorSmokeResult:
    total_checks: int
    passed_count: int
    passed: bool
    check_results: List[LiveVectorSmokeCheckResult]
    kept_database_name: Optional[str] = None


@dataclass(frozen=True)
class _StorageSnapshot:
    total_chunks: int
    live_vector_count: int
    serialized_vector_count: int
    embedding_dimensions: List[int]
    page_chunk_counts: Dict[str, int]
    notion_page_count: int


class _StaticLLMProvider(LLMProvider):
    @property
    def name(self) -> str:
        return "openai"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        _ = request
        return LLMResponse(
            provider="openai",
            model="gpt-4o-mini",
            output_text=(
                "Grounded answer for live vector smoke. Check the returned citations."
            ),
            token_input=24,
            token_output=14,
        )


class _TemporaryPgvectorDatabase:
    def __init__(self, *, admin_database_url: str) -> None:
        self._admin_database_url = admin_database_url
        self.database_name = f"learnloop_step55_{uuid.uuid4().hex[:8]}"
        self._database_url = self._build_database_url(database_name=self.database_name)
        self._engine = None
        self._session: Optional[Session] = None
        self._database_created = False

    def create_session(self) -> Session:
        admin_engine = create_engine(
            self._admin_database_url,
            isolation_level="AUTOCOMMIT",
        )
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{self.database_name}"'))
        admin_engine.dispose()
        self._database_created = True

        _apply_schema_with_alembic(
            database_url=self._database_url,
        )

        self._engine = create_engine(self._database_url)
        local_session = sessionmaker(
            bind=self._engine,
            autoflush=False,
            autocommit=False,
        )
        self._session = local_session()
        return self._session

    def close(self, *, keep_database: bool) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None

        if keep_database or not self._database_created:
            return

        cleanup_engine = create_engine(
            self._admin_database_url,
            isolation_level="AUTOCOMMIT",
        )
        with cleanup_engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{self.database_name}" WITH (FORCE)'))
        cleanup_engine.dispose()
        self._database_created = False

    def _build_database_url(self, *, database_name: str) -> str:
        admin_url = make_url(self._admin_database_url)
        return admin_url.set(database=database_name).render_as_string(
            hide_password=False
        )


def _apply_schema_with_alembic(*, database_url: str) -> None:
    original_database_url = os.getenv("DATABASE_URL")
    try:
        os.environ["DATABASE_URL"] = database_url
        get_settings.cache_clear()
        config = Config(str((_REPO_ROOT / "alembic.ini").resolve()))
        config.set_main_option(
            "script_location",
            str((_REPO_ROOT / "alembic").resolve()),
        )
        # Reuse the real migration path so the smoke schema stays aligned with
        # the project contract and all FK dependencies are created together.
        command.upgrade(config, "head")
    finally:
        if original_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = original_database_url
        get_settings.cache_clear()


def build_live_vector_smoke_config(
    *,
    run_flag: Optional[str] = None,
    openai_api_key: Optional[str] = None,
    admin_database_url: Optional[str] = None,
    keep_database_on_failure: bool = False,
) -> LiveVectorSmokeConfig:
    effective_run_flag = _resolve_value(run_flag, os.getenv(RUN_FLAG_ENV))
    if effective_run_flag != "1":
        raise LiveVectorSmokePrereqError(
            f"Set {RUN_FLAG_ENV}=1 to confirm the live embedding spend."
        )

    effective_openai_api_key = _resolve_value(
        openai_api_key,
        os.getenv(OPENAI_API_KEY_ENV),
    )
    if not effective_openai_api_key:
        raise LiveVectorSmokePrereqError(
            f"{OPENAI_API_KEY_ENV} is required for the live embedding smoke run."
        )

    effective_admin_database_url = _resolve_value(
        admin_database_url,
        os.getenv(ADMIN_DATABASE_URL_ENV),
    ) or DEFAULT_ADMIN_DATABASE_URL

    return LiveVectorSmokeConfig(
        openai_api_key=effective_openai_api_key,
        admin_database_url=effective_admin_database_url,
        keep_database_on_failure=keep_database_on_failure,
    )


def build_live_smoke_pages() -> Dict[str, NotionPageTree]:
    return {
        MAIN_PAGE_ID: NotionPageTree(
            page_id=MAIN_PAGE_ID,
            title="Live Vector Smoke Main",
            notion_path=MAIN_PAGE_PATH,
            blocks=[
                NotionBlockNode(
                    block_id="blk-live-exact-heading",
                    block_type="heading_2",
                    content_text=EXACT_SECTION_TITLE,
                    block_path=EXACT_SECTION_PATH,
                    children=[
                        NotionBlockNode(
                            block_id="blk-live-exact-note",
                            block_type="paragraph",
                            content_text=EXACT_RETRIEVAL_QUERY,
                            block_path=f"{EXACT_SECTION_PATH}/{EXACT_RETRIEVAL_QUERY}",
                        )
                    ],
                ),
                NotionBlockNode(
                    block_id="blk-live-duplicate-heading",
                    block_type="heading_2",
                    content_text=DUPLICATE_SECTION_TITLE,
                    block_path=DUPLICATE_SECTION_PATH,
                    children=[
                        NotionBlockNode(
                            block_id="blk-live-duplicate-a",
                            block_type="paragraph",
                            content_text=_build_duplicate_paragraph("first"),
                            block_path=f"{DUPLICATE_SECTION_PATH}/first",
                        ),
                        NotionBlockNode(
                            block_id="blk-live-duplicate-b",
                            block_type="paragraph",
                            content_text=_build_duplicate_paragraph("second"),
                            block_path=f"{DUPLICATE_SECTION_PATH}/second",
                        ),
                    ],
                ),
            ],
        ),
        SECONDARY_PAGE_ID: NotionPageTree(
            page_id=SECONDARY_PAGE_ID,
            title="Live Vector Smoke Distractor",
            notion_path=SECONDARY_PAGE_PATH,
            blocks=[
                NotionBlockNode(
                    block_id="blk-live-distractor-heading",
                    block_type="heading_2",
                    content_text="Distractor Notes",
                    block_path=f"{SECONDARY_PAGE_PATH}/Distractor Notes",
                    children=[
                        NotionBlockNode(
                            block_id="blk-live-distractor-note",
                            block_type="paragraph",
                            content_text=(
                                "distractor baseline note unrelated to the live vector smoke checks"
                            ),
                            block_path=(
                                f"{SECONDARY_PAGE_PATH}/Distractor Notes/"
                                "distractor baseline note unrelated"
                            ),
                        )
                    ],
                )
            ],
        ),
    }


async def evaluate_live_vector_smoke(
    config: LiveVectorSmokeConfig,
) -> LiveVectorSmokeResult:
    database = _TemporaryPgvectorDatabase(
        admin_database_url=config.admin_database_url
    )
    keep_database = False
    kept_database_name: Optional[str] = None

    try:
        session = database.create_session()
        session_factory = sessionmaker(
            bind=session.get_bind(),
            autoflush=False,
            autocommit=False,
        )
        pages = build_live_smoke_pages()
        embedding_client = OpenAIEmbeddingClient(api_key=config.openai_api_key)
        page_index_orchestrator = _build_page_index_orchestrator(
            session_factory=session_factory,
            pages=pages,
            embedding_client=embedding_client,
        )
        qa_orchestrator = _build_qa_orchestrator(
            session=session,
            session_factory=session_factory,
            embedding_client=embedding_client,
        )
        retriever = ProductionChunkRetriever(
            chunk_repository=ChunkRepository(session)
        )

        await page_index_orchestrator.index_page(
            page_id=MAIN_PAGE_ID,
            request_workflow_id="live-smoke-index-main-initial",
        )
        await page_index_orchestrator.index_page(
            page_id=SECONDARY_PAGE_ID,
            request_workflow_id="live-smoke-index-secondary-initial",
        )
        initial_snapshot = _collect_storage_snapshot(session)

        await page_index_orchestrator.index_page(
            page_id=MAIN_PAGE_ID,
            request_workflow_id="live-smoke-index-main-reindex",
        )
        final_snapshot = _collect_storage_snapshot(session)

        exact_query_embedding = await _embed_query(
            embedding_client=embedding_client,
            query=EXACT_RETRIEVAL_QUERY,
            workflow_id="live-smoke-query-exact",
        )
        exact_retrieval = retriever.retrieve_with_metadata(
            query_text=EXACT_RETRIEVAL_QUERY,
            query_embedding=exact_query_embedding,
            top_k=1,
            page_ids=[MAIN_PAGE_ID],
            source_kinds=["notion"],
            allow_legacy_embedding_scoring=False,
        )

        duplicate_query_embedding = await _embed_query(
            embedding_client=embedding_client,
            query=DUPLICATE_CITATION_QUERY,
            workflow_id="live-smoke-query-duplicate",
        )
        duplicate_retrieval = retriever.retrieve_with_metadata(
            query_text=DUPLICATE_CITATION_QUERY,
            query_embedding=duplicate_query_embedding,
            top_k=3,
            page_ids=[MAIN_PAGE_ID],
            section_paths=[DUPLICATE_SECTION_PATH],
            source_kinds=["notion"],
            allow_legacy_embedding_scoring=False,
        )
        duplicate_qa_result = await qa_orchestrator.answer_question(
            query=DUPLICATE_CITATION_QUERY,
            top_k=3,
            page_ids=[MAIN_PAGE_ID],
            section_paths=[DUPLICATE_SECTION_PATH],
            source_kinds=["notion"],
            provider_name="openai",
            model="gpt-4o-mini",
            request_workflow_id="live-smoke-qa-duplicate",
        )
        insufficient_result = await qa_orchestrator.answer_question(
            query="no matching chunks should be returned in this scoped request",
            top_k=3,
            page_ids=[MAIN_PAGE_ID],
            section_paths=[MISSING_SECTION_PATH],
            source_kinds=["notion"],
            provider_name="openai",
            model="gpt-4o-mini",
            request_workflow_id="live-smoke-qa-insufficient",
        )

        check_results = [
            _check_stored_vectors(final_snapshot),
            _check_db_side_retrieval(exact_retrieval),
            _check_citation_deduplication(
                duplicate_retrieval_paths=[
                    chunk.notion_path for chunk in duplicate_retrieval.chunks
                ],
                duplicate_qa_result=duplicate_qa_result,
            ),
            _check_insufficient_info(insufficient_result),
            _check_duplicate_row_safety(
                initial_snapshot=initial_snapshot,
                final_snapshot=final_snapshot,
            ),
        ]
    except Exception as exc:
        if config.keep_database_on_failure:
            keep_database = True
            kept_database_name = database.database_name
        check_results = [
            LiveVectorSmokeCheckResult(
                check_id="runtime_error",
                passed=False,
                message=str(exc),
            )
        ]
    finally:
        database.close(keep_database=keep_database)

    passed_count = sum(1 for result in check_results if result.passed)
    return LiveVectorSmokeResult(
        total_checks=len(check_results),
        passed_count=passed_count,
        passed=passed_count == len(check_results),
        check_results=check_results,
        kept_database_name=kept_database_name,
    )


def format_live_vector_smoke_result(result: LiveVectorSmokeResult) -> str:
    status = "pass" if result.passed else "fail"
    lines = [
        f"live_vector_smoke: {status} ({result.passed_count}/{result.total_checks})",
        "check_results:",
    ]
    for check_result in result.check_results:
        check_status = "pass" if check_result.passed else "fail"
        lines.append(
            f"- {check_result.check_id}: {check_status}; {check_result.message}"
        )
    if result.kept_database_name:
        lines.append(
            "kept_database: "
            f"{result.kept_database_name} (preserved because --keep-database-on-failure was set)"
        )
    return "\n".join(lines)


def _build_duplicate_paragraph(label: str) -> str:
    repeated_phrase = f"{DUPLICATE_CITATION_QUERY} {label} chunk signal"
    return " ".join(repeated_phrase for _ in range(12))


def _build_page_index_orchestrator(
    *,
    session_factory,
    pages: Dict[str, NotionPageTree],
    embedding_client: OpenAIEmbeddingClient,
) -> NotionPageIndexOrchestrator:
    registry = ToolRegistry()
    registry.register_tool(NotionReaderTool(InMemoryNotionReaderClient(pages)))
    return NotionPageIndexOrchestrator(
        tool_registry=registry,
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        workflow_run_service=WorkflowRunService(session_factory),
        embedding_client=embedding_client,
        cost_tracker=CostTracker(),
        allow_synthetic_postgres_persistence=True,
    )


def _build_qa_orchestrator(
    *,
    session: Session,
    session_factory,
    embedding_client: OpenAIEmbeddingClient,
) -> QAOrchestrator:
    provider_router = ProviderRouter()
    provider_router.register_provider(_StaticLLMProvider())
    return QAOrchestrator(
        retriever=ProductionChunkRetriever(
            chunk_repository=ChunkRepository(session)
        ),
        embedding_client=embedding_client,
        provider_router=provider_router,
        cost_tracker=CostTracker(),
        prompt_template_loader=PromptTemplateLoader(),
        workflow_run_service=WorkflowRunService(session_factory),
    )


async def _embed_query(
    *,
    embedding_client: OpenAIEmbeddingClient,
    query: str,
    workflow_id: str,
) -> List[float]:
    response = await embedding_client.embed(
        EmbeddingRequest(
            inputs=[query],
            dimensions=VECTOR_DIMENSIONS,
            metadata={
                "workflow_id": workflow_id,
                "operation": "live_vector_smoke_query",
            },
        )
    )
    if len(response.embeddings) != 1:
        raise RuntimeError("Expected exactly one query embedding response")

    query_embedding = [float(value) for value in response.embeddings[0]]
    if len(query_embedding) != VECTOR_DIMENSIONS:
        raise RuntimeError(
            "Expected live query embedding dimensions to equal "
            f"{VECTOR_DIMENSIONS}, got {len(query_embedding)}"
        )
    return query_embedding


def _collect_storage_snapshot(session: Session) -> _StorageSnapshot:
    chunk_rows = (
        session.query(KnowledgeChunk, NotionPage.notion_page_id)
        .outerjoin(NotionBlock, KnowledgeChunk.notion_block_id == NotionBlock.id)
        .outerjoin(NotionPage, NotionBlock.notion_page_id == NotionPage.id)
        .order_by(KnowledgeChunk.id.asc())
        .all()
    )
    page_chunk_counts: Dict[str, int] = {}
    embedding_dimensions: List[int] = []
    live_vector_count = 0
    serialized_vector_count = 0

    for chunk, notion_page_id in chunk_rows:
        if notion_page_id:
            page_chunk_counts[notion_page_id] = (
                page_chunk_counts.get(notion_page_id, 0) + 1
            )
        if chunk.embedding is not None:
            live_vector_count += 1
            embedding_dimensions.append(len(chunk.embedding))
        if chunk.embedding_text:
            serialized_vector_count += 1

    notion_page_count = int(
        session.query(func.count(NotionPage.id)).scalar() or 0
    )
    return _StorageSnapshot(
        total_chunks=len(chunk_rows),
        live_vector_count=live_vector_count,
        serialized_vector_count=serialized_vector_count,
        embedding_dimensions=embedding_dimensions,
        page_chunk_counts=page_chunk_counts,
        notion_page_count=notion_page_count,
    )


def _check_stored_vectors(snapshot: _StorageSnapshot) -> LiveVectorSmokeCheckResult:
    if snapshot.total_chunks != EXPECTED_TOTAL_CHUNK_COUNT:
        return LiveVectorSmokeCheckResult(
            check_id="stored_vectors",
            passed=False,
            message=(
                "Expected "
                f"{EXPECTED_TOTAL_CHUNK_COUNT} chunks after live indexing, "
                f"found {snapshot.total_chunks}."
            ),
        )
    if snapshot.live_vector_count != snapshot.total_chunks:
        return LiveVectorSmokeCheckResult(
            check_id="stored_vectors",
            passed=False,
            message=(
                "Expected every chunk to have a live pgvector embedding, "
                f"found {snapshot.live_vector_count}/{snapshot.total_chunks}."
            ),
        )
    if snapshot.serialized_vector_count != snapshot.total_chunks:
        return LiveVectorSmokeCheckResult(
            check_id="stored_vectors",
            passed=False,
            message=(
                "Expected every chunk to keep transitional embedding_text, "
                f"found {snapshot.serialized_vector_count}/{snapshot.total_chunks}."
            ),
        )
    if not snapshot.embedding_dimensions or any(
        dimension != VECTOR_DIMENSIONS for dimension in snapshot.embedding_dimensions
    ):
        return LiveVectorSmokeCheckResult(
            check_id="stored_vectors",
            passed=False,
            message=(
                "Expected every stored embedding to round-trip as "
                f"{VECTOR_DIMENSIONS} dimensions."
            ),
        )
    return LiveVectorSmokeCheckResult(
        check_id="stored_vectors",
        passed=True,
        message=(
            f"Stored {snapshot.total_chunks} notion chunks with live "
            f"{VECTOR_DIMENSIONS}-dim vectors and transitional embedding_text."
        ),
    )


def _check_db_side_retrieval(retrieval_result) -> LiveVectorSmokeCheckResult:
    if retrieval_result.retrieval_mode != RETRIEVAL_MODE_PGVECTOR_EXACT_COSINE:
        return LiveVectorSmokeCheckResult(
            check_id="db_side_retrieval",
            passed=False,
            message=(
                "Expected pgvector DB-side retrieval mode, got "
                f"{retrieval_result.retrieval_mode}."
            ),
        )
    if retrieval_result.retrieval_fallback_reason is not None:
        return LiveVectorSmokeCheckResult(
            check_id="db_side_retrieval",
            passed=False,
            message=(
                "Expected no fallback reason for exact vector retrieval, got "
                f"{retrieval_result.retrieval_fallback_reason}."
            ),
        )
    if not retrieval_result.chunks:
        return LiveVectorSmokeCheckResult(
            check_id="db_side_retrieval",
            passed=False,
            message="Expected at least one live vector retrieval result.",
        )
    top_chunk = retrieval_result.chunks[0]
    if top_chunk.notion_path != EXACT_SECTION_PATH:
        return LiveVectorSmokeCheckResult(
            check_id="db_side_retrieval",
            passed=False,
            message=(
                "Expected the exact live vector query to return "
                f"{EXACT_SECTION_PATH}, got {top_chunk.notion_path}."
            ),
        )
    return LiveVectorSmokeCheckResult(
        check_id="db_side_retrieval",
        passed=True,
        message=(
            f"Top pgvector result matched {top_chunk.notion_path} "
            f"with score={top_chunk.score:.6f}."
        ),
    )


def _check_citation_deduplication(
    *,
    duplicate_retrieval_paths: List[str],
    duplicate_qa_result,
) -> LiveVectorSmokeCheckResult:
    if len(duplicate_retrieval_paths) < 2:
        return LiveVectorSmokeCheckResult(
            check_id="citation_deduplication",
            passed=False,
            message=(
                "Expected multiple raw retrieved chunks from the duplicate citation "
                "section."
            ),
        )
    unique_retrieval_paths = set(duplicate_retrieval_paths)
    if unique_retrieval_paths != {DUPLICATE_SECTION_PATH}:
        return LiveVectorSmokeCheckResult(
            check_id="citation_deduplication",
            passed=False,
            message=(
                "Expected raw duplicate retrieval paths to stay within "
                f"{DUPLICATE_SECTION_PATH}, got {sorted(unique_retrieval_paths)}."
            ),
        )
    citation_paths = [
        citation.notion_path for citation in duplicate_qa_result.citations
    ]
    if duplicate_qa_result.insufficient_info:
        return LiveVectorSmokeCheckResult(
            check_id="citation_deduplication",
            passed=False,
            message="Expected duplicate citation QA query to return a grounded answer.",
        )
    if citation_paths != [DUPLICATE_SECTION_PATH]:
        return LiveVectorSmokeCheckResult(
            check_id="citation_deduplication",
            passed=False,
            message=(
                "Expected duplicate raw chunks to collapse into one citation path, "
                f"got {citation_paths}."
            ),
        )
    return LiveVectorSmokeCheckResult(
        check_id="citation_deduplication",
        passed=True,
        message=(
            f"Collapsed {len(duplicate_retrieval_paths)} raw chunk hits into one "
            f"citation path: {DUPLICATE_SECTION_PATH}."
        ),
    )


def _check_insufficient_info(qa_result) -> LiveVectorSmokeCheckResult:
    if not qa_result.insufficient_info:
        return LiveVectorSmokeCheckResult(
            check_id="insufficient_info",
            passed=False,
            message="Expected scoped-empty QA request to return insufficient_info=true.",
        )
    if qa_result.citations:
        return LiveVectorSmokeCheckResult(
            check_id="insufficient_info",
            passed=False,
            message="Expected insufficient-info QA result to contain zero citations.",
        )
    if qa_result.answer != INSUFFICIENT_INFO_ANSWER:
        return LiveVectorSmokeCheckResult(
            check_id="insufficient_info",
            passed=False,
            message=(
                "Expected the deterministic insufficient-info answer, got "
                f"{qa_result.answer!r}."
            ),
        )
    return LiveVectorSmokeCheckResult(
        check_id="insufficient_info",
        passed=True,
        message="Scoped-empty QA request returned the deterministic insufficient-info answer.",
    )


def _check_duplicate_row_safety(
    *,
    initial_snapshot: _StorageSnapshot,
    final_snapshot: _StorageSnapshot,
) -> LiveVectorSmokeCheckResult:
    expected_page_counts = {
        MAIN_PAGE_ID: EXPECTED_MAIN_PAGE_CHUNK_COUNT,
        SECONDARY_PAGE_ID: 1,
    }
    if initial_snapshot.notion_page_count != 2 or final_snapshot.notion_page_count != 2:
        return LiveVectorSmokeCheckResult(
            check_id="duplicate_row_safety",
            passed=False,
            message="Expected exactly two notion_pages rows before and after re-index.",
        )
    if initial_snapshot.page_chunk_counts != expected_page_counts:
        return LiveVectorSmokeCheckResult(
            check_id="duplicate_row_safety",
            passed=False,
            message=(
                "Unexpected initial page chunk counts: "
                f"{initial_snapshot.page_chunk_counts}."
            ),
        )
    if final_snapshot.total_chunks != initial_snapshot.total_chunks:
        return LiveVectorSmokeCheckResult(
            check_id="duplicate_row_safety",
            passed=False,
            message=(
                "Expected re-index to keep chunk row count stable, "
                f"got {initial_snapshot.total_chunks} -> {final_snapshot.total_chunks}."
            ),
        )
    if final_snapshot.page_chunk_counts != expected_page_counts:
        return LiveVectorSmokeCheckResult(
            check_id="duplicate_row_safety",
            passed=False,
            message=(
                "Expected page chunk counts to stay stable after re-index, got "
                f"{final_snapshot.page_chunk_counts}."
            ),
        )
    return LiveVectorSmokeCheckResult(
        check_id="duplicate_row_safety",
        passed=True,
        message=(
            f"Re-index kept chunk rows stable at {final_snapshot.total_chunks} with "
            f"page counts {final_snapshot.page_chunk_counts}."
        ),
    )


def _resolve_value(*values: Optional[str]) -> str:
    for value in values:
        if value is None:
            continue
        normalized = value.strip()
        if normalized:
            return normalized
    return ""


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the opt-in live PostgreSQL + OpenAI embedding smoke verification "
            "for Step 55."
        )
    )
    parser.add_argument(
        "--admin-database-url",
        default=None,
        help=(
            "Override the admin PostgreSQL URL used to create a temporary pgvector "
            "database. Defaults to $LEARNLOOP_PGVECTOR_ADMIN_DATABASE_URL or the "
            "local docker-compose postgres URL."
        ),
    )
    parser.add_argument(
        "--keep-database-on-failure",
        action="store_true",
        help="Keep the temporary PostgreSQL database when the smoke run fails.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        config = build_live_vector_smoke_config(
            admin_database_url=args.admin_database_url,
            keep_database_on_failure=args.keep_database_on_failure,
        )
    except LiveVectorSmokePrereqError as exc:
        print(f"live_vector_smoke: prerequisites missing; {exc}")
        return 2

    result = asyncio.run(evaluate_live_vector_smoke(config))
    print(format_live_vector_smoke_result(result))
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
