from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.db.base import Base  # noqa: E402
from src.db.models import KnowledgeChunk, NotionBlock, NotionPage, WorkflowRun  # noqa: E402
from src.db.unit_of_work import SqlAlchemyUnitOfWork  # noqa: E402
from src.orchestrators import (  # noqa: E402
    NotionIncrementalIndexOrchestrator,
    NotionPageIndexOrchestrator,
)
from src.providers import (  # noqa: E402
    EmbeddingClient,
    EmbeddingRequest,
    EmbeddingResponse,
    get_openai_embedding_capabilities,
)
from src.rag import ProductionChunkRetriever  # noqa: E402
from src.repositories import (  # noqa: E402
    ChunkRepository,
)
from src.services import WorkflowRunService  # noqa: E402
from src.tools import (  # noqa: E402
    InMemoryNotionReaderClient,
    NotionBlockNode,
    NotionPageTree,
    NotionReaderTool,
    ToolRegistry,
)

PAGE_ID = "page-manual-sync-eval"
PAGE_PATH = "Knowledge/Eval/Manual Sync"
MANUAL_SECTION_PATH = f"{PAGE_PATH}/Manual Concepts"
DELETED_AI_SECTION_PATH = f"{PAGE_PATH}/AI Supplement Zone/Deleted AI Supplement"
DELETED_AI_QUERY = "orphaned vector stale deletion marker"
MANUAL_NOTE_QUERY = "manual source survives sync canonical"


class _FakeEmbeddingClient(EmbeddingClient):
    @property
    def name(self) -> str:
        return "openai"

    def get_capabilities(self, *, model: str, dimensions: int):
        return get_openai_embedding_capabilities(
            model=model,
            dimensions=dimensions,
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        embeddings = [
            [float(index + 1)] * 1536
            for index, _ in enumerate(request.inputs)
        ]
        return EmbeddingResponse(
            provider="openai",
            model="text-embedding-3-small",
            embeddings=embeddings,
            indices=list(range(len(request.inputs))),
            token_input=len(request.inputs) * 10,
        )


@dataclass(frozen=True)
class ManualSyncCheckResult:
    check_id: str
    passed: bool
    message: str


@dataclass(frozen=True)
class ManualSyncEvalResult:
    total_checks: int
    passed_count: int
    passed: bool
    check_results: List[ManualSyncCheckResult]


async def evaluate_manual_sync_reconciliation() -> ManualSyncEvalResult:
    session_factory = build_manual_sync_eval_session_factory()
    session = session_factory()
    pages = {PAGE_ID: _page_before_manual_delete()}

    try:
        index_orchestrator = _build_index_orchestrator(
            session_factory=session_factory,
            pages=pages,
        )
        incremental_orchestrator = _build_incremental_orchestrator(
            session=session,
            session_factory=session_factory,
            page_index_orchestrator=index_orchestrator,
        )

        await index_orchestrator.index_page(
            page_id=PAGE_ID,
            request_workflow_id="eval-manual-sync-initial",
        )
        initial_deleted_ai_paths = _retrieve_paths(
            session=session,
            query_text=DELETED_AI_QUERY,
        )

        pages[PAGE_ID] = _page_after_manual_delete()
        sync_result = await incremental_orchestrator.sync_pages(
            page_ids=[PAGE_ID],
            request_workflow_id="eval-manual-sync-manual",
        )
        final_deleted_ai_paths = _retrieve_paths(
            session=session,
            query_text=DELETED_AI_QUERY,
        )
        final_manual_paths = _retrieve_paths(
            session=session,
            query_text=MANUAL_NOTE_QUERY,
        )
        remaining_stale_chunks = _list_chunk_texts_containing(
            session=session,
            text=DELETED_AI_QUERY,
        )

        check_results = [
            _check_initial_ai_chunk_indexed(initial_deleted_ai_paths),
            _check_deleted_ai_chunk_removed(
                final_deleted_ai_paths=final_deleted_ai_paths,
                remaining_stale_chunks=remaining_stale_chunks,
            ),
            _check_manual_note_chunk_retained(final_manual_paths),
            _check_manual_sync_metadata(sync_result),
        ]
    finally:
        session.close()

    passed_count = sum(1 for result in check_results if result.passed)
    return ManualSyncEvalResult(
        total_checks=len(check_results),
        passed_count=passed_count,
        passed=passed_count == len(check_results),
        check_results=check_results,
    )


def build_manual_sync_eval_session_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            NotionPage.__table__,
            NotionBlock.__table__,
            KnowledgeChunk.__table__,
            WorkflowRun.__table__,
        ],
    )
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def format_manual_sync_eval_result(result: ManualSyncEvalResult) -> str:
    status = "pass" if result.passed else "fail"
    lines = [
        f"manual_sync_reconciliation: {status} ({result.passed_count}/{result.total_checks})",
        "check_results:",
    ]
    for check_result in result.check_results:
        check_status = "pass" if check_result.passed else "fail"
        lines.append(
            f"- {check_result.check_id}: {check_status}; {check_result.message}"
        )
    return "\n".join(lines)


def _build_index_orchestrator(
    *,
    session_factory,
    pages: Dict[str, NotionPageTree],
) -> NotionPageIndexOrchestrator:
    registry = ToolRegistry()
    registry.register_tool(NotionReaderTool(InMemoryNotionReaderClient(pages)))
    return NotionPageIndexOrchestrator(
        tool_registry=registry,
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        workflow_run_service=WorkflowRunService(session_factory),
        embedding_client=_FakeEmbeddingClient(),
    )


def _build_incremental_orchestrator(
    *,
    session: Session,
    session_factory,
    page_index_orchestrator: NotionPageIndexOrchestrator,
) -> NotionIncrementalIndexOrchestrator:
    return NotionIncrementalIndexOrchestrator(
        page_index_orchestrator=page_index_orchestrator,
        workflow_run_service=WorkflowRunService(session_factory),
    )


def _page_before_manual_delete() -> NotionPageTree:
    return NotionPageTree(
        page_id=PAGE_ID,
        title="Manual Sync Eval",
        notion_path=PAGE_PATH,
        blocks=[
            _manual_section_block(),
            NotionBlockNode(
                block_id="blk-eval-ai-zone",
                block_type="heading_2",
                content_text="AI Supplement Zone",
                block_path=f"{PAGE_PATH}/AI Supplement Zone",
                children=[
                    NotionBlockNode(
                        block_id="blk-eval-ai-supplement",
                        block_type="toggle",
                        content_text="Deleted AI Supplement",
                        block_path=DELETED_AI_SECTION_PATH,
                        children=[
                            NotionBlockNode(
                                block_id="blk-eval-ai-note",
                                block_type="paragraph",
                                content_text=DELETED_AI_QUERY,
                                block_path=f"{DELETED_AI_SECTION_PATH}/{DELETED_AI_QUERY}",
                            )
                        ],
                    )
                ],
            ),
        ],
    )


def _page_after_manual_delete() -> NotionPageTree:
    return NotionPageTree(
        page_id=PAGE_ID,
        title="Manual Sync Eval",
        notion_path=PAGE_PATH,
        blocks=[_manual_section_block()],
    )


def _manual_section_block() -> NotionBlockNode:
    return NotionBlockNode(
        block_id="blk-eval-manual-heading",
        block_type="heading_2",
        content_text="Manual Concepts",
        block_path=MANUAL_SECTION_PATH,
        children=[
            NotionBlockNode(
                block_id="blk-eval-manual-note",
                block_type="paragraph",
                content_text=MANUAL_NOTE_QUERY,
                block_path=f"{MANUAL_SECTION_PATH}/{MANUAL_NOTE_QUERY}",
            )
        ],
    )


def _retrieve_paths(*, session: Session, query_text: str) -> List[str]:
    retriever = ProductionChunkRetriever(
        chunk_repository=ChunkRepository(session),
    )
    chunks = retriever.retrieve(query_text=query_text, top_k=5)
    return [chunk.notion_path for chunk in chunks]


def _list_chunk_texts_containing(*, session: Session, text: str) -> List[str]:
    return [
        str(chunk.chunk_text)
        for chunk in session.query(KnowledgeChunk)
        .filter(KnowledgeChunk.source_kind == "notion")
        .all()
        if text in str(chunk.chunk_text)
    ]


def _check_initial_ai_chunk_indexed(
    initial_deleted_ai_paths: List[str],
) -> ManualSyncCheckResult:
    passed = DELETED_AI_SECTION_PATH in initial_deleted_ai_paths
    message = (
        f"deleted AI supplement was indexed before manual sync: {DELETED_AI_SECTION_PATH}"
        if passed
        else f"deleted AI supplement was not indexed before sync: {initial_deleted_ai_paths}"
    )
    return ManualSyncCheckResult(
        check_id="initial_ai_chunk_indexed",
        passed=passed,
        message=message,
    )


def _check_deleted_ai_chunk_removed(
    *,
    final_deleted_ai_paths: List[str],
    remaining_stale_chunks: List[str],
) -> ManualSyncCheckResult:
    passed = (
        DELETED_AI_SECTION_PATH not in final_deleted_ai_paths
        and not remaining_stale_chunks
    )
    message = (
        "deleted AI supplement chunk absent from production retrieval and raw chunks"
        if passed
        else (
            "deleted AI supplement chunk still present; "
            f"retrieved={final_deleted_ai_paths}; raw_count={len(remaining_stale_chunks)}"
        )
    )
    return ManualSyncCheckResult(
        check_id="deleted_ai_chunk_removed",
        passed=passed,
        message=message,
    )


def _check_manual_note_chunk_retained(
    final_manual_paths: List[str],
) -> ManualSyncCheckResult:
    passed = MANUAL_SECTION_PATH in final_manual_paths
    message = (
        f"manual note chunk retained after sync: {MANUAL_SECTION_PATH}"
        if passed
        else f"manual note chunk missing after sync: {final_manual_paths}"
    )
    return ManualSyncCheckResult(
        check_id="manual_note_chunk_retained",
        passed=passed,
        message=message,
    )


def _check_manual_sync_metadata(sync_result: object) -> ManualSyncCheckResult:
    sync_mode = getattr(sync_result, "sync_mode", "")
    status = getattr(sync_result, "status", "")
    processed_page_count = getattr(sync_result, "processed_page_count", 0)
    indexed_pages = list(getattr(sync_result, "indexed_pages", []))
    indexed_block_count = (
        indexed_pages[0].indexed_block_count if indexed_pages else 0
    )
    passed = (
        status == "succeeded"
        and sync_mode == "manual"
        and processed_page_count == 1
        and indexed_block_count == 2
    )
    message = (
        "incremental sync completed as manual page-level replacement"
        if passed
        else (
            "unexpected incremental sync metadata: "
            f"status={status}; sync_mode={sync_mode}; "
            f"processed_page_count={processed_page_count}; "
            f"indexed_block_count={indexed_block_count}"
        )
    )
    return ManualSyncCheckResult(
        check_id="manual_sync_metadata",
        passed=passed,
        message=message,
    )


def main() -> None:
    _ = argparse.ArgumentParser(
        description="Evaluate manual Notion sync reconciliation by page replacement."
    ).parse_args()

    result = asyncio.run(evaluate_manual_sync_reconciliation())
    print(format_manual_sync_eval_result(result))
    if not result.passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
