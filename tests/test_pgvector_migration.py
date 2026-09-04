from pathlib import Path
from typing import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from src.app.config import get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_pgvector_migration_upgrades_and_downgrades_fresh_sqlite_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'fresh.db'}"
    config = _build_alembic_config(database_url=database_url, monkeypatch=monkeypatch)

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    inspector = inspect(engine)
    notion_page_columns = {
        column["name"]: column for column in inspector.get_columns("notion_pages")
    }
    source_document_columns = {
        column["name"]
        for column in inspector.get_columns("source_documents")
    }
    column_names = {column["name"] for column in inspector.get_columns("knowledge_chunks")}
    knowledge_chunk_indexes = {
        index["name"] for index in inspector.get_indexes("knowledge_chunks")
    }
    notion_block_indexes = {
        index["name"] for index in inspector.get_indexes("notion_blocks")
    }

    assert "embedding" in column_names
    assert "embedding_text" in column_names
    assert "file_hash" in source_document_columns
    assert notion_page_columns["last_edited_time"]["nullable"] is True
    assert notion_page_columns["parent_notion_page_id"]["nullable"] is True
    assert "ix_notion_pages_parent_notion_page_id" in {
        index["name"] for index in inspector.get_indexes("notion_pages")
    }
    assert "ix_knowledge_chunks_source_kind" in knowledge_chunk_indexes
    assert "ix_knowledge_chunks_notion_block_id" in knowledge_chunk_indexes
    assert "ix_knowledge_chunks_notion_path" in knowledge_chunk_indexes
    assert "ix_notion_blocks_notion_page_id" in notion_block_indexes
    api_idempotency_columns = {
        column["name"]
        for column in inspector.get_columns("api_idempotency_records")
    }
    assert {
        "request_scope",
        "idempotency_key",
        "request_fingerprint",
        "status",
        "response_status_code",
        "response_body",
        "response_headers_json",
    } <= api_idempotency_columns
    assert "uq_api_idempotency_scope_key" in {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("api_idempotency_records")
    }

    command.downgrade(config, "989de3f24186")

    inspector = inspect(engine)
    downgraded_column_names = {
        column["name"] for column in inspector.get_columns("knowledge_chunks")
    }
    downgraded_indexes = {
        index["name"] for index in inspector.get_indexes("knowledge_chunks")
    }
    downgraded_notion_page_columns = {
        column["name"] for column in inspector.get_columns("notion_pages")
    }
    downgraded_notion_page_indexes = {
        index["name"] for index in inspector.get_indexes("notion_pages")
    }

    assert "embedding" not in downgraded_column_names
    assert "embedding_text" in downgraded_column_names
    assert "parent_notion_page_id" not in downgraded_notion_page_columns
    assert "ix_notion_pages_parent_notion_page_id" not in downgraded_notion_page_indexes
    assert "ix_knowledge_chunks_source_kind" not in downgraded_indexes
    assert "ix_knowledge_chunks_notion_block_id" not in downgraded_indexes
    assert "ix_knowledge_chunks_notion_path" not in downgraded_indexes


def test_pgvector_migration_preserves_existing_chunk_rows_and_null_vectors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'populated.db'}"
    config = _build_alembic_config(database_url=database_url, monkeypatch=monkeypatch)

    command.upgrade(config, "989de3f24186")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO knowledge_chunks (
                    id,
                    source_document_id,
                    notion_block_id,
                    chunk_index,
                    chunk_text,
                    notion_path,
                    embedding_text,
                    source_kind
                ) VALUES (
                    1,
                    NULL,
                    NULL,
                    0,
                    'existing chunk',
                    'Knowledge/PageA/Intro',
                    '[0.1,0.2]',
                    'notion'
                )
                """
            )
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT chunk_text, source_kind, notion_path, embedding_text, embedding
                FROM knowledge_chunks
                WHERE id = 1
                """
            )
        ).mappings().one()

    assert row["chunk_text"] == "existing chunk"
    assert row["source_kind"] == "notion"
    assert row["notion_path"] == "Knowledge/PageA/Intro"
    assert row["embedding_text"] == "[0.1,0.2]"
    assert row["embedding"] is None


def _build_alembic_config(
    *,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Config:
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config(str(Path("alembic.ini").resolve()))
    config.set_main_option("script_location", str(Path("alembic").resolve()))
    return config
