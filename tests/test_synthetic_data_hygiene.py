from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.base import Base
from src.db.models import KnowledgeChunk, NotionBlock, NotionPage, SourceDocument
from src.orchestrators.notion_page_index_orchestrator import (
    NotionPageIndexOrchestrator,
    PreparedNotionPageSnapshot,
    _ToolPagePayload,
)
from src.policies.synthetic_data import SYNTHETIC_NOTION_PAGE_IDS
from src.repositories import ChunkRepository, SyntheticDataRepository


def _load_script(name: str):
    script_path = Path(__file__).resolve().parents[1] / "scripts" / name
    module_name = f"learnloop_{script_path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _build_session_factory(tmp_path: Path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'synthetic.db'}")
    Base.metadata.create_all(
        engine,
        tables=[
            NotionPage.__table__,
            NotionBlock.__table__,
            SourceDocument.__table__,
            KnowledgeChunk.__table__,
        ],
    )
    return engine, sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _seed_synthetic_and_real(session_factory) -> None:
    session = session_factory()
    try:
        synthetic_page = NotionPage(
            id=1,
            notion_page_id="page-nlp-week5",
            title="Synthetic page",
            notion_path="Synthetic/NLP",
        )
        real_page = NotionPage(
            id=2,
            notion_page_id="real-notion-page-001",
            title="Real page",
            notion_path="Knowledge/Real",
        )
        session.add_all([synthetic_page, real_page])
        session.flush()
        synthetic_block = NotionBlock(
            id=1,
            notion_block_id="synthetic-block-001",
            notion_page_id=synthetic_page.id,
            block_type="paragraph",
            content_text="synthetic fixture",
            block_path="Synthetic/NLP/Note",
        )
        real_block = NotionBlock(
            id=2,
            notion_block_id="real-block-001",
            notion_page_id=real_page.id,
            block_type="paragraph",
            content_text="real note",
            block_path="Knowledge/Real/Note",
        )
        session.add_all([synthetic_block, real_block])
        session.flush()
        session.add_all(
            [
                KnowledgeChunk(
                    id=1,
                    notion_block_id=synthetic_block.id,
                    chunk_index=0,
                    chunk_text="synthetic fixture",
                    notion_path="Synthetic/NLP/Note",
                    source_kind="notion",
                ),
                KnowledgeChunk(
                    id=2,
                    notion_block_id=real_block.id,
                    chunk_index=0,
                    chunk_text="real note",
                    notion_path="Knowledge/Real/Note",
                    source_kind="notion",
                ),
            ]
        )
        session.commit()
    finally:
        session.close()


def test_cleanup_repository_only_removes_fixed_synthetic_allowlist(tmp_path: Path) -> None:
    engine, session_factory = _build_session_factory(tmp_path)
    _seed_synthetic_and_real(session_factory)
    session = session_factory()
    try:
        repository = SyntheticDataRepository(session)
        before = repository.inspect()
        assert before.page_count == 1
        assert before.production_chunk_count == 1
        assert "page-nlp-week5" in SYNTHETIC_NOTION_PAGE_IDS

        removed = repository.delete_synthetic_data()
        session.commit()
        after = repository.inspect()

        assert removed == before
        assert after.is_clean
        assert session.query(NotionPage).filter_by(
            notion_page_id="real-notion-page-001"
        ).count() == 1
        assert session.query(KnowledgeChunk).count() == 1
    finally:
        session.close()
        engine.dispose()


def test_production_retrieval_excludes_synthetic_but_keeps_real_chunks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, session_factory = _build_session_factory(tmp_path)
    _seed_synthetic_and_real(session_factory)
    session = session_factory()
    try:
        monkeypatch.setattr(session.get_bind().dialect, "name", "postgresql")
        candidates = ChunkRepository(session).list_production_chunks()
        assert [candidate.notion_page_id for candidate in candidates] == [
            "real-notion-page-001"
        ]
    finally:
        session.close()
        engine.dispose()


def test_cleanup_cli_is_dry_run_and_requires_confirmation(tmp_path: Path, capsys) -> None:
    engine, session_factory = _build_session_factory(tmp_path)
    _seed_synthetic_and_real(session_factory)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'synthetic.db'}"
    module = _load_script("cleanup_synthetic_data.py")
    try:
        assert module.main(["--database-url", database_url, "--json"]) == 0
        dry_run = capsys.readouterr().out
        assert '"status": "dry_run"' in dry_run
        assert '"production_chunk_count": 1' in dry_run

        assert module.main(
            ["--apply", "--database-url", database_url, "--json"]
        ) == 1
        assert "SYNTHETIC_CLEANUP_CONFIRMATION_REQUIRED" in capsys.readouterr().out

        assert module.main(
            [
                "--apply",
                "--confirm",
                "CLEAN_SYNTHETIC_DATA",
                "--database-url",
                database_url,
                "--json",
            ]
        ) == 0
        applied = capsys.readouterr().out
        assert '"status": "applied"' in applied
        assert '"page_count": 0' in applied
    finally:
        engine.dispose()


def test_release_gate_fails_closed_then_passes_after_cleanup(tmp_path: Path) -> None:
    engine, session_factory = _build_session_factory(tmp_path)
    _seed_synthetic_and_real(session_factory)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'synthetic.db'}"
    gate = _load_script("release_gate.py")
    cleanup = _load_script("cleanup_synthetic_data.py")
    try:
        failed = gate.run_release_gate(database_url=database_url)
        assert failed["status"] == "failed"
        assert failed["error_code"] == "SYNTHETIC_PRODUCTION_CHUNKS_PRESENT"

        cleanup.run_cleanup(database_url=database_url, apply=True)
        passed = gate.run_release_gate(database_url=database_url)
        assert passed["status"] == "passed"
        assert passed["counts"]["production_chunk_count"] == 0
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("page_id", "source_is_synthetic"),
    [("page-nlp-week5", False), ("unlisted-mock-page", True)],
)
def test_production_postgres_persistence_guard_fails_before_repository_write(
    page_id: str,
    source_is_synthetic: bool,
) -> None:
    orchestrator = NotionPageIndexOrchestrator(
        tool_registry=SimpleNamespace(),
        unit_of_work_factory=SimpleNamespace(),
        workflow_run_service=SimpleNamespace(),
        source_is_synthetic=source_is_synthetic,
    )
    prepared = PreparedNotionPageSnapshot(
        page_payload=_ToolPagePayload(
            page_id=page_id,
            title="Synthetic page",
            notion_path="Synthetic/NLP",
        ),
        block_paths=[],
        chunk_upserts=[],
        embedding_metadata={},
    )

    with pytest.raises(Exception) as error:
        orchestrator._persist_indexed_page_in_unit_of_work(
            prepared_snapshot=prepared,
            unit_of_work=SimpleNamespace(database_dialect="postgresql"),
        )

    assert getattr(error.value, "error_code", None) == "SYNTHETIC_DATA_NOT_ALLOWED"
