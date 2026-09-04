from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.db.base import Base
from src.db.models import KnowledgeChunk, NotionBlock, NotionPage, SourceDocument
from src.policies.synthetic_data import SYNTHETIC_NOTION_PAGE_IDS
from src.repositories import ChunkRepository
from tests.evals.step99_hybrid_eval import (
    DEFAULT_FIXTURE_DIR,
    EXPERIMENT_ID,
    Step99ContractError,
    canonical_digest,
    load_contract,
)


LIVE_ENV = "LEARNLOOP_RUN_STEP99_PGVECTOR_GATE"
TARGET_ENV = "LEARNLOOP_STEP99_PGVECTOR_TARGET_CLASS"
TARGET_CLASS = "disposable_non_production_postgresql"
APPROVAL_TEXT = "I_APPROVE_STEP99_DISPOSABLE_PGVECTOR_GATE"
DATABASE_PREFIX = "learnloop_step99_"
PAGE_ALPHA = "step99-eval-page-alpha"
PAGE_BETA = "step99-eval-page-beta"
VECTOR_DIMENSIONS = 1536


def validate_targets(environment: Mapping[str, str]) -> tuple[Any, Any]:
    production_value = environment.get("DATABASE_URL")
    admin_value = environment.get("LEARNLOOP_PGVECTOR_ADMIN_DATABASE_URL")
    if not production_value or not admin_value:
        raise Step99ContractError("production and maintenance targets required")
    try:
        production = make_url(production_value)
        admin = make_url(admin_value)
    except Exception:
        raise Step99ContractError("database target invalid") from None
    if not production.database or not admin.database or production.database == admin.database:
        raise Step99ContractError("application and maintenance databases must differ")
    if str(production.database).startswith(DATABASE_PREFIX):
        raise Step99ContractError("application database uses disposable namespace")
    if PAGE_ALPHA in SYNTHETIC_NOTION_PAGE_IDS or PAGE_BETA in SYNTHETIC_NOTION_PAGE_IDS:
        raise Step99ContractError("repository fixture id conflicts with synthetic exclusion policy")
    return production, admin


def run_gate(manifest: Mapping[str, Any], environment: Mapping[str, str]) -> dict[str, Any]:
    production, admin = validate_targets(environment)
    database_name = f"{DATABASE_PREFIX}{uuid.uuid4().hex[:12]}"
    database_url = admin.set(database=database_name)
    admin_engine = create_engine(admin.render_as_string(hide_password=False), isolation_level="AUTOCOMMIT")
    disposable_engine = None
    created = False
    cleanup_status = "not_started"
    try:
        with admin_engine.connect() as connection:
            current = connection.execute(text("select current_database()" )).scalar_one()
            can_create = connection.execute(
                text("select rolsuper or rolcreatedb from pg_roles where rolname=current_user")
            ).scalar_one()
            vector_available = connection.execute(
                text("select exists(select 1 from pg_available_extensions where name='vector')")
            ).scalar_one()
        if current != admin.database or not can_create or not vector_available:
            raise Step99ContractError("maintenance preflight failed")
        if production.host != admin.host or production.port != admin.port:
            raise Step99ContractError("application and maintenance server boundary mismatch")
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        created = True
        disposable_engine = create_engine(database_url.render_as_string(hide_password=False))
        with disposable_engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        Base.metadata.create_all(
            disposable_engine,
            tables=[
                NotionPage.__table__,
                NotionBlock.__table__,
                SourceDocument.__table__,
                KnowledgeChunk.__table__,
            ],
        )
        session = sessionmaker(bind=disposable_engine, autoflush=False, autocommit=False)()
        try:
            _seed(session)
            repository = ChunkRepository(session)
            page_candidates = repository.list_production_chunks(
                page_ids=[PAGE_ALPHA], source_kinds=["notion"]
            )
            section_candidates = repository.list_production_chunks(
                page_ids=[PAGE_ALPHA],
                section_paths=["Step99/Alpha/Target"],
                source_kinds=["notion"],
            )
            if [item.chunk_id for item in page_candidates] != [1, 2]:
                raise Step99ContractError("page eligible set mismatch")
            if [item.chunk_id for item in section_candidates] != [1]:
                raise Step99ContractError("section eligible set mismatch")
            page_vector = repository.list_production_chunks_by_vector(
                query_embedding=_embedding(1.0, 0.0),
                top_k=1,
                page_ids=[PAGE_ALPHA],
                source_kinds=["notion"],
            )
            section_vector = repository.list_production_chunks_by_vector(
                query_embedding=_embedding(1.0, 0.0),
                top_k=5,
                page_ids=[PAGE_ALPHA],
                section_paths=["Step99/Alpha/Target"],
                source_kinds=["notion"],
            )
            non_notion = repository.list_production_chunks_by_vector(
                query_embedding=_embedding(1.0, 0.0),
                top_k=5,
                source_kinds=["source_document"],
            )
            if [item.chunk_id for item in page_vector] != [2]:
                raise Step99ContractError("page filter-before-top-k mismatch")
            if [item.chunk_id for item in section_vector] != [1]:
                raise Step99ContractError("section filter-before-top-k mismatch")
            if non_notion:
                raise Step99ContractError("non-Notion exclusion mismatch")
        finally:
            session.close()
    finally:
        if disposable_engine is not None:
            disposable_engine.dispose()
        if created:
            try:
                with admin_engine.connect() as connection:
                    connection.execute(text(f'DROP DATABASE "{database_name}" WITH (FORCE)'))
                cleanup_status = "passed"
            except Exception:
                cleanup_status = "failed"
        admin_engine.dispose()
    verification_engine = create_engine(admin.render_as_string(hide_password=False))
    try:
        with verification_engine.connect() as connection:
            remaining = int(
                connection.execute(
                    text("select count(*) from pg_database where datname like :prefix"),
                    {"prefix": f"{DATABASE_PREFIX}%"},
                ).scalar_one()
            )
    finally:
        verification_engine.dispose()
    if cleanup_status != "passed" or remaining != 0:
        raise Step99ContractError("disposable database cleanup failed")
    evidence = {
        "status": "passed",
        "experiment_id": EXPERIMENT_ID,
        "manifest_digest": canonical_digest(manifest),
        "adapter": "postgresql_pgvector",
        "target_class": TARGET_CLASS,
        "database_prefix": DATABASE_PREFIX,
        "production_database_used": False,
        "production_database_distinct": True,
        "disposable_database_created": True,
        "schema_setup": "create_extension_and_four_model_tables_no_alembic",
        "expected_eligible_sets_nonempty": True,
        "filter_before_top_k_passed": True,
        "case_count": 5,
        "cleanup_status": cleanup_status,
        "remaining_database_count": remaining,
    }
    evidence["receipt_digest"] = canonical_digest(evidence)
    return evidence


def _seed(session: Any) -> None:
    session.add_all(
        [
            NotionPage(id=1, notion_page_id=PAGE_ALPHA, title="Alpha", notion_path="Step99/Alpha"),
            NotionPage(id=2, notion_page_id=PAGE_BETA, title="Beta", notion_path="Step99/Beta"),
            NotionBlock(id=1, notion_block_id="step99-alpha-target", notion_page_id=1, parent_block_id=None, block_type="paragraph", content_text="alpha target", block_path="Step99/Alpha/Target", block_order=0),
            NotionBlock(id=2, notion_block_id="step99-alpha-other", notion_page_id=1, parent_block_id=None, block_type="paragraph", content_text="alpha other", block_path="Step99/Alpha/Other", block_order=1),
            NotionBlock(id=3, notion_block_id="step99-beta-target", notion_page_id=2, parent_block_id=None, block_type="paragraph", content_text="beta target", block_path="Step99/Beta/Target", block_order=0),
        ]
    )
    session.flush()
    source = SourceDocument(id=1, source_type="chat", source_display_name="public-safe", content_hash="step99-public-safe", raw_text="public safe decoy")
    session.add(source)
    session.flush()
    session.add_all(
        [
            KnowledgeChunk(id=1, source_document_id=None, notion_block_id=1, chunk_index=0, chunk_text="alpha target", notion_path="Step99/Alpha/Target", embedding=_embedding(0.8, 0.2), embedding_text=None, source_kind="notion"),
            KnowledgeChunk(id=2, source_document_id=None, notion_block_id=2, chunk_index=1, chunk_text="alpha other", notion_path="Step99/Alpha/Other", embedding=_embedding(1.0, 0.0), embedding_text=None, source_kind="notion"),
            KnowledgeChunk(id=3, source_document_id=None, notion_block_id=3, chunk_index=0, chunk_text="beta target", notion_path="Step99/Beta/Target", embedding=_embedding(1.0, 0.0), embedding_text=None, source_kind="notion"),
            KnowledgeChunk(id=4, source_document_id=source.id, notion_block_id=None, chunk_index=0, chunk_text="pending rejected non notion", notion_path=None, embedding=_embedding(1.0, 0.0), embedding_text=None, source_kind="source_document"),
        ]
    )
    session.commit()


def _embedding(*leading: float) -> list[float]:
    values = [0.0] * VECTOR_DIMENSIONS
    for index, value in enumerate(leading):
        values[index] = value
    return values


def _write_json_create_only(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.pending")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Guarded Step 99 disposable pgvector gate")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--approval", default="")
    parser.add_argument("--fixture-dir", type=Path, default=DEFAULT_FIXTURE_DIR)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.execute or os.getenv(LIVE_ENV) != "1" or os.getenv(TARGET_ENV) != TARGET_CLASS or args.approval != APPROVAL_TEXT:
        print(json.dumps({"status": "skipped", "database_operations": 0}, sort_keys=True))
        return
    try:
        manifest = load_contract(args.fixture_dir)
        expected = _REPO_ROOT / manifest["artifacts"]["pgvector_evidence_path"]
        if args.output.resolve() != expected.resolve() or args.output.exists():
            raise Step99ContractError("pgvector evidence destination invalid")
        evidence = run_gate(manifest, os.environ)
        _write_json_create_only(args.output, evidence)
    except Step99ContractError as exc:
        print(json.dumps({"status": "inconclusive", "safe_failure_category": str(exc)}, sort_keys=True))
        raise SystemExit(2)
    print(json.dumps({"status": "passed", "receipt_digest": evidence["receipt_digest"]}, sort_keys=True))


if __name__ == "__main__":
    main()
