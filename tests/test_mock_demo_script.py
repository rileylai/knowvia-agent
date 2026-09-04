from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def _load_demo_module():
    script_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "run_mock_demo.py"
    )
    spec = importlib.util.spec_from_file_location("run_mock_demo", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_run_mock_demo_returns_expected_summary() -> None:
    module = _load_demo_module()

    summary = module.run_demo()

    assert summary.health_status == "ok"
    assert summary.indexed_page_id == "page-nlp-week5"
    assert summary.indexed_page_title == "NLP Week 5"
    assert summary.indexed_block_count > 0
    assert summary.qa_provider == "openai"
    assert summary.qa_model == "gpt-4o-mini"
    assert summary.qa_citation_path.startswith("Knowledge/NLP/Week5")
    assert "order signal" in summary.qa_answer
