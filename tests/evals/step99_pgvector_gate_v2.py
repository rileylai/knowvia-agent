from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.db.models import KnowledgeChunk, NotionBlock, NotionPage, SourceDocument
from tests.evals import step99_hybrid_eval as evaluation
from tests.evals import step99_pgvector_gate as gate


EXPERIMENT_ID = "step99-exp-002"
DEFAULT_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "step_99" / EXPERIMENT_ID


def _activate_contract() -> None:
    evaluation.EXPERIMENT_ID = EXPERIMENT_ID
    evaluation.DEFAULT_FIXTURE_DIR = DEFAULT_FIXTURE_DIR
    gate.EXPERIMENT_ID = EXPERIMENT_ID
    gate.DEFAULT_FIXTURE_DIR = DEFAULT_FIXTURE_DIR
    gate._seed = _seed_ordered


def _seed_ordered(session: Any) -> None:
    session.add_all(
        [
            NotionPage(id=1, notion_page_id=gate.PAGE_ALPHA, title="Alpha", notion_path="Step99/Alpha"),
            NotionPage(id=2, notion_page_id=gate.PAGE_BETA, title="Beta", notion_path="Step99/Beta"),
        ]
    )
    session.flush()
    session.add_all(
        [
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
            KnowledgeChunk(id=1, source_document_id=None, notion_block_id=1, chunk_index=0, chunk_text="alpha target", notion_path="Step99/Alpha/Target", embedding=gate._embedding(0.8, 0.2), embedding_text=None, source_kind="notion"),
            KnowledgeChunk(id=2, source_document_id=None, notion_block_id=2, chunk_index=1, chunk_text="alpha other", notion_path="Step99/Alpha/Other", embedding=gate._embedding(1.0, 0.0), embedding_text=None, source_kind="notion"),
            KnowledgeChunk(id=3, source_document_id=None, notion_block_id=3, chunk_index=0, chunk_text="beta target", notion_path="Step99/Beta/Target", embedding=gate._embedding(1.0, 0.0), embedding_text=None, source_kind="notion"),
            KnowledgeChunk(id=4, source_document_id=source.id, notion_block_id=None, chunk_index=0, chunk_text="pending rejected non notion", notion_path=None, embedding=gate._embedding(1.0, 0.0), embedding_text=None, source_kind="source_document"),
        ]
    )
    session.commit()


def main() -> None:
    _activate_contract()
    parser = argparse.ArgumentParser(description="Guarded Step 99 exp-002 disposable pgvector gate")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--approval", default="")
    parser.add_argument("--fixture-dir", type=Path, default=DEFAULT_FIXTURE_DIR)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.execute or os.getenv(gate.LIVE_ENV) != "1" or os.getenv(gate.TARGET_ENV) != gate.TARGET_CLASS or args.approval != gate.APPROVAL_TEXT:
        print(json.dumps({"status": "skipped", "database_operations": 0}, sort_keys=True))
        return
    try:
        manifest = evaluation.load_contract(args.fixture_dir)
        expected = evaluation._REPO_ROOT / manifest["artifacts"]["pgvector_evidence_path"]
        if args.output.resolve() != expected.resolve() or args.output.exists():
            raise evaluation.Step99ContractError("pgvector evidence destination invalid")
        evidence = gate.run_gate(manifest, os.environ)
        gate._write_json_create_only(args.output, evidence)
    except evaluation.Step99ContractError as exc:
        print(json.dumps({"status": "inconclusive", "safe_failure_category": str(exc)}, sort_keys=True))
        raise SystemExit(2)
    print(json.dumps({"status": "passed", "receipt_digest": evidence["receipt_digest"]}, sort_keys=True))


if __name__ == "__main__":
    main()
