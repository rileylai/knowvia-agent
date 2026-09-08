from __future__ import annotations

import hashlib
import inspect
import json
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.app.config import get_settings  # noqa: E402
from src.db.unit_of_work import SqlAlchemyUnitOfWork  # noqa: E402
from src.providers.embedding import OpenAIEmbeddingClient  # noqa: E402
from src.rag.retriever import (  # noqa: E402
    KNOWLEDGE_RELEVANCE_FLOOR,
    KNOWLEDGE_VECTOR_CANDIDATE_POOL_MAX,
    ProductionChunkRetriever,
    RETRIEVAL_MODE_PGVECTOR_EXACT_COSINE,
    RetrievalResult,
)
from src.rag.chunker import chunk_text_document  # noqa: E402
from src.repositories.chunk_repository import ChunkRepository  # noqa: E402
from src.services.embedding_batch_service import EmbeddingBatchService  # noqa: E402
from src.services.knowledge_indexing_service import KnowledgeIndexingService  # noqa: E402
from src.tools.pdf_parser_tool import ParsedPDFDocument, PyPDFParserClient  # noqa: E402

from .metrics import (
    CaseEvaluation,
    RetrievalObservation,
    build_bounded_report,
    evaluate_case,
    normalize_for_match,
    page_from_locator,
)
from .schema import EvidenceLocation, RetrievalBenchmark


DEFAULT_ADMIN_DATABASE_URL = (
    "postgresql+psycopg://knowvia:knowvia@localhost:5433/postgres"
)
VECTOR_MODEL = "text-embedding-3-small"
VECTOR_DIMENSIONS = 1536


class RetrievalBenchmarkError(RuntimeError):
    pass


class RetrievalAnnotationError(RetrievalBenchmarkError):
    pass


@dataclass(frozen=True)
class VerifiedCorpus:
    manifest: list[dict[str, object]]
    parsed_documents: Mapping[str, ParsedPDFDocument]


def verify_corpus(
    benchmark: RetrievalBenchmark,
    *,
    corpus_root: Path,
    parser: Optional[PyPDFParserClient] = None,
) -> VerifiedCorpus:
    """Verify the frozen corpus identity and parse each PDF with production code."""
    selected_parser = parser or PyPDFParserClient()
    manifest: list[dict[str, object]] = []
    parsed_documents: dict[str, ParsedPDFDocument] = {}
    for source in benchmark.corpus:
        pdf_path = corpus_root / source.filename
        if not pdf_path.is_file():
            raise RetrievalBenchmarkError(
                f"INGESTION_PARSER_FAILURE: missing corpus file {source.filename}"
            )
        file_bytes = pdf_path.read_bytes()
        actual_sha256 = hashlib.sha256(file_bytes).hexdigest()
        if actual_sha256 != source.sha256:
            raise RetrievalBenchmarkError(
                f"corpus SHA256 mismatch for {source.source_id}: expected {source.sha256}"
            )
        try:
            parsed = selected_parser.parse_document(
                file_name=source.filename,
                file_bytes=file_bytes,
            )
        except Exception as exc:
            raise RetrievalBenchmarkError(
                f"INGESTION_PARSER_FAILURE: {source.source_id}: {exc}"
            ) from exc
        if parsed.page_count != source.page_count:
            raise RetrievalBenchmarkError(
                f"page count mismatch for {source.source_id}: "
                f"expected {source.page_count}, got {parsed.page_count}"
            )
        parsed_documents[source.source_id] = parsed
        manifest.append(
            {
                "source_id": source.source_id,
                "filename": source.filename,
                "sha256": actual_sha256,
                "page_count": parsed.page_count,
            }
        )
    validate_gold_annotations(benchmark, parsed_documents)
    return VerifiedCorpus(manifest=manifest, parsed_documents=parsed_documents)


def validate_gold_annotations(
    benchmark: RetrievalBenchmark,
    parsed_documents: Mapping[str, ParsedPDFDocument],
) -> None:
    """Fail closed when an answerable gold anchor is absent from its page."""
    for case in benchmark.cases:
        if not case.answerable:
            continue
        for group in case.evidence_groups:
            location_results: list[bool] = []
            for location in group.locations:
                parsed = parsed_documents.get(location.source_id)
                if parsed is None:
                    raise RetrievalAnnotationError(
                        f"{case.case_id}/{group.group_id}: unknown source_id "
                        f"{location.source_id}"
                    )
                found = False
                for page in location.pages:
                    page_text = normalize_for_match(parsed.pages[page - 1])
                    if any(
                        normalize_for_match(anchor) in page_text
                        for anchor in location.anchors
                    ):
                        found = True
                        break
                location_results.append(found)
                if group.match == "all" and not found:
                    raise RetrievalAnnotationError(
                        _annotation_error_message(case.case_id, group.group_id, location)
                    )
            if group.match == "any" and not any(location_results):
                location = group.locations[0]
                raise RetrievalAnnotationError(
                    _annotation_error_message(case.case_id, group.group_id, location)
                )


def _annotation_error_message(
    case_id: str,
    group_id: str,
    location: EvidenceLocation,
) -> str:
    return (
        f"{case_id}/{group_id}: gold anchor not found in parsed page; "
        f"source_id={location.source_id} pages={list(location.pages)} "
        f"anchors={list(location.anchors)}"
    )


def run_retrieval_cases(
    benchmark: RetrievalBenchmark,
    *,
    query_embeddings: Sequence[Sequence[float]],
    retriever: ProductionChunkRetriever,
    source_id_by_document_id: Mapping[int, str],
) -> list[CaseEvaluation]:
    """Run retrieval-only cases at top_k=5 and derive @1/@3/@5 metrics."""
    if len(query_embeddings) != len(benchmark.cases):
        raise RetrievalBenchmarkError("query embedding count does not match case count")

    evaluations: list[CaseEvaluation] = []
    for case, query_embedding in zip(benchmark.cases, query_embeddings):
        result = retriever.retrieve_with_metadata(
            query_text=case.query,
            top_k=5,
            query_embedding=list(query_embedding),
            allow_legacy_embedding_scoring=False,
            owner_scope="local",
        )
        observations = _observations_from_result(result, source_id_by_document_id)
        evaluations.append(evaluate_case(case, observations))
    return evaluations


def _observations_from_result(
    result: RetrievalResult,
    source_id_by_document_id: Mapping[int, str],
) -> tuple[RetrievalObservation, ...]:
    observations: list[RetrievalObservation] = []
    for rank, chunk in enumerate(result.chunks, start=1):
        page = page_from_locator(chunk.locator)
        source_id = (
            source_id_by_document_id.get(chunk.source_document_id)
            if chunk.source_document_id is not None
            else None
        )
        observations.append(
            RetrievalObservation(
                rank=rank,
                source_id=source_id,
                locator=chunk.locator or "",
                chunk_text=chunk.chunk_text,
                score=chunk.score,
                accepted=True,
                retrieval_mode=result.retrieval_mode,
                source_document_id=chunk.source_document_id,
                page=page,
                candidate_count=result.candidate_count,
            )
        )
    return tuple(observations)


async def run_live_benchmark(
    benchmark: RetrievalBenchmark,
    *,
    corpus_root: Path,
    openai_api_key: str,
    admin_database_url: str = DEFAULT_ADMIN_DATABASE_URL,
    keep_database: bool = False,
) -> dict[str, object]:
    """Run the pilot in a disposable migrated PostgreSQL+pgvector database."""
    verified = verify_corpus(benchmark, corpus_root=corpus_root)
    _verify_production_contract(benchmark)
    temporary_database = _TemporaryEvaluationDatabase(
        admin_database_url=admin_database_url,
    )
    engine = None
    try:
        database_url = temporary_database.create_database()
        engine = create_engine(database_url)
        session_factory = sessionmaker(
            bind=engine,
            autoflush=False,
            autocommit=False,
        )
        unit_of_work_factory = lambda: SqlAlchemyUnitOfWork(session_factory)
        embedding_client = OpenAIEmbeddingClient(
            api_key=openai_api_key,
            default_model=VECTOR_MODEL,
        )
        embedding_service = EmbeddingBatchService(
            embedding_client=embedding_client,
            model=VECTOR_MODEL,
            dimensions=VECTOR_DIMENSIONS,
        )
        indexing_service = KnowledgeIndexingService(
            unit_of_work_factory=unit_of_work_factory,
            embedding_batch_service=embedding_service,
        )

        source_id_by_document_id: dict[int, str] = {}
        for source in benchmark.corpus:
            parsed = verified.parsed_documents[source.source_id]
            pdf_path = corpus_root / source.filename
            file_bytes = pdf_path.read_bytes()
            with unit_of_work_factory() as unit_of_work:
                document = unit_of_work.source_documents.create_source_document(
                    source_type="pdf",
                    source_display_name=source.filename,
                    original_filename=source.filename,
                    raw_text=parsed.raw_text,
                    content_hash=hashlib.sha256(
                        parsed.raw_text.encode("utf-8")
                    ).hexdigest(),
                    file_hash=source.sha256,
                    owner_scope="local",
                    status="indexing",
                )
                source_document_id = int(document.id)
            await indexing_service.index_source_document(
                source_document_id=source_document_id,
                source_kind="pdf",
                source_display_name=source.filename,
                raw_text=parsed.raw_text,
                request_workflow_id=f"retrieval-pilot-{source.source_id}",
                pages=parsed.pages,
                owner_scope="local",
            )
            source_id_by_document_id[source_document_id] = source.source_id

        query_embedding_result = await embedding_service.embed(
            [case.query for case in benchmark.cases],
            metadata={
                "operation": "retrieval_quality_benchmark_query",
                "benchmark_id": benchmark.benchmark_id,
            },
        )
        with session_factory() as session:
            retriever = ProductionChunkRetriever(
                chunk_repository=ChunkRepository(session),
            )
            evaluations = run_retrieval_cases(
                benchmark,
                query_embeddings=query_embedding_result.embeddings,
                retriever=retriever,
                source_id_by_document_id=source_id_by_document_id,
            )
        return build_bounded_report(
            benchmark_id=benchmark.benchmark_id,
            benchmark_version=benchmark.version,
            contract={
                "chunk_max_chars": benchmark.contract.chunk_max_chars,
                "chunk_overlap_chars": benchmark.contract.chunk_overlap_chars,
                "page_aware": benchmark.contract.page_aware,
                "embedding_model": benchmark.contract.embedding_model,
                "embedding_dimensions": benchmark.contract.embedding_dimensions,
                "similarity": benchmark.contract.similarity,
                "candidate_pool_max": benchmark.contract.candidate_pool_max,
                "relevance_floor": benchmark.contract.relevance_floor,
                "top_k": list(benchmark.contract.top_k),
            },
            corpus_manifest=verified.manifest,
            evaluations=evaluations,
        )
    finally:
        if engine is not None:
            engine.dispose()
        temporary_database.close(
            keep_database=keep_database,
        )


def write_report(report: Mapping[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class _TemporaryEvaluationDatabase:
    def __init__(self, *, admin_database_url: str) -> None:
        self._admin_database_url = admin_database_url
        self.database_name = f"knowvia_retrieval_pilot_{uuid.uuid4().hex[:10]}"
        self._database_created = False

    def create_database(self) -> str:
        admin_engine = create_engine(
            self._admin_database_url,
            isolation_level="AUTOCOMMIT",
        )
        try:
            with admin_engine.connect() as connection:
                connection.execute(text(f'CREATE DATABASE "{self.database_name}"'))
        finally:
            admin_engine.dispose()
        self._database_created = True
        database_url = make_url(self._admin_database_url).set(
            database=self.database_name
        ).render_as_string(hide_password=False)
        _apply_schema_with_alembic(database_url=database_url)
        return database_url

    def close(self, *, keep_database: bool) -> None:
        if not self._database_created or keep_database:
            return
        cleanup_engine = create_engine(
            self._admin_database_url,
            isolation_level="AUTOCOMMIT",
        )
        try:
            with cleanup_engine.connect() as connection:
                connection.execute(
                    text(f'DROP DATABASE "{self.database_name}" WITH (FORCE)')
                )
        finally:
            cleanup_engine.dispose()
        self._database_created = False


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
        command.upgrade(config, "head")
    finally:
        if original_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = original_database_url
        get_settings.cache_clear()


def _verify_production_contract(benchmark: RetrievalBenchmark) -> None:
    contract = benchmark.contract
    chunk_signature = inspect.signature(chunk_text_document)
    current_chunk_max_chars = chunk_signature.parameters["max_chunk_chars"].default
    current_page_aware = "pages" in chunk_signature.parameters
    current_chunk_overlap_chars = 0 if "overlap" not in chunk_signature.parameters else None
    # Knowledge indexing currently has no exported model/dimension constants.
    # Keep these values as the explicit frozen 8.1 declaration and compare the
    # retriever constants directly where the repository exposes them.
    expected = {
        "chunk_max_chars": current_chunk_max_chars,
        "chunk_overlap_chars": current_chunk_overlap_chars,
        "page_aware": current_page_aware,
        "embedding_model": VECTOR_MODEL,
        "embedding_dimensions": VECTOR_DIMENSIONS,
        "similarity": (
            "cosine"
            if RETRIEVAL_MODE_PGVECTOR_EXACT_COSINE == "pgvector_exact_cosine"
            else None
        ),
        "candidate_pool_max": KNOWLEDGE_VECTOR_CANDIDATE_POOL_MAX,
        "relevance_floor": KNOWLEDGE_RELEVANCE_FLOOR,
        "top_k": (1, 3, 5),
    }
    actual = {
        "chunk_max_chars": contract.chunk_max_chars,
        "chunk_overlap_chars": contract.chunk_overlap_chars,
        "page_aware": contract.page_aware,
        "embedding_model": contract.embedding_model,
        "embedding_dimensions": contract.embedding_dimensions,
        "similarity": contract.similarity,
        "candidate_pool_max": contract.candidate_pool_max,
        "relevance_floor": contract.relevance_floor,
        "top_k": contract.top_k,
    }
    if actual != expected:
        raise RetrievalBenchmarkError(
            "frozen 8.1 baseline declaration mismatch; "
            "pilot refuses to measure changed behavior"
        )
