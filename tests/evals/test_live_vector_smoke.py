from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from src.rag import ChunkerBlock, ChunkerPage, chunk_notion_page
from tests.evals.live_vector_smoke import (
    ADMIN_DATABASE_URL_ENV,
    DEFAULT_ADMIN_DATABASE_URL,
    DUPLICATE_SECTION_PATH,
    LiveVectorSmokeCheckResult,
    LiveVectorSmokePrereqError,
    LiveVectorSmokeResult,
    OPENAI_API_KEY_ENV,
    RUN_FLAG_ENV,
    _apply_schema_with_alembic,
    build_live_smoke_pages,
    build_live_vector_smoke_config,
    format_live_vector_smoke_result,
)


def test_build_live_vector_smoke_config_requires_opt_in_and_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(RUN_FLAG_ENV, raising=False)
    monkeypatch.delenv(OPENAI_API_KEY_ENV, raising=False)
    with pytest.raises(LiveVectorSmokePrereqError, match=RUN_FLAG_ENV):
        build_live_vector_smoke_config()

    monkeypatch.setenv(RUN_FLAG_ENV, "1")
    with pytest.raises(LiveVectorSmokePrereqError, match=OPENAI_API_KEY_ENV):
        build_live_vector_smoke_config()

    monkeypatch.setenv(OPENAI_API_KEY_ENV, "placeholder-openai-key")
    monkeypatch.delenv(ADMIN_DATABASE_URL_ENV, raising=False)
    config = build_live_vector_smoke_config()
    assert config.openai_api_key == "placeholder-openai-key"
    assert config.admin_database_url == DEFAULT_ADMIN_DATABASE_URL


def test_live_smoke_fixture_creates_duplicate_path_chunks() -> None:
    pages = build_live_smoke_pages()
    main_page = pages["page-live-vector-smoke-main"]
    chunk_drafts = chunk_notion_page(_to_chunker_page(main_page))

    duplicate_chunks = [
        chunk for chunk in chunk_drafts if chunk.notion_path == DUPLICATE_SECTION_PATH
    ]
    assert len(chunk_drafts) == 3
    assert len(duplicate_chunks) == 2
    assert {chunk.notion_path for chunk in duplicate_chunks} == {
        DUPLICATE_SECTION_PATH
    }


def test_format_live_vector_smoke_result_reports_kept_database() -> None:
    result = LiveVectorSmokeResult(
        total_checks=2,
        passed_count=1,
        passed=False,
        check_results=[
            LiveVectorSmokeCheckResult(
                check_id="stored_vectors",
                passed=True,
                message="stored vectors okay",
            ),
            LiveVectorSmokeCheckResult(
                check_id="runtime_error",
                passed=False,
                message="network timeout",
            ),
        ],
        kept_database_name="learnloop_step55_deadbeef",
    )

    rendered = format_live_vector_smoke_result(result)
    assert "live_vector_smoke: fail (1/2)" in rendered
    assert "- stored_vectors: pass; stored vectors okay" in rendered
    assert "- runtime_error: fail; network timeout" in rendered
    assert "learnloop_step55_deadbeef" in rendered


def test_apply_schema_with_alembic_creates_chunk_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'live-smoke-schema.db'}"
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///preserved.db")

    _apply_schema_with_alembic(database_url=database_url)

    engine = create_engine(database_url)
    inspector = inspect(engine)

    table_names = set(inspector.get_table_names())
    assert {
        "notion_pages",
        "notion_blocks",
        "source_documents",
        "knowledge_chunks",
        "workflow_runs",
    }.issubset(table_names)

    referred_tables = {
        foreign_key["referred_table"]
        for foreign_key in inspector.get_foreign_keys("knowledge_chunks")
    }
    assert "source_documents" in referred_tables
    assert "notion_blocks" in referred_tables
    assert os.getenv("DATABASE_URL") == "sqlite+pysqlite:///preserved.db"


def _to_chunker_page(page_tree) -> ChunkerPage:
    return ChunkerPage(
        notion_page_id=page_tree.page_id,
        title=page_tree.title,
        notion_path=page_tree.notion_path,
        blocks=[_to_chunker_block(block) for block in page_tree.blocks],
    )


def _to_chunker_block(block) -> ChunkerBlock:
    return ChunkerBlock(
        notion_block_id=block.block_id,
        block_type=block.block_type,
        content_text=block.content_text,
        block_path=block.block_path,
        children=[_to_chunker_block(child) for child in block.children],
    )
