from __future__ import annotations

import json

from tests.evals.adapter_smoke_matrix import (
    render_report,
    run_adapter_smoke_matrix,
)


def test_adapter_smoke_matrix_runs_real_library_fixture_checks() -> None:
    report = run_adapter_smoke_matrix()

    checks = {check.check_id: check for check in report.checks}
    assert checks["pdf_pypdf_fixture"].status == "passed"
    assert checks["url_trafilatura_fixture"].status == "passed"
    assert checks["youtube_transcript_live"].status == "skipped"
    assert checks["openai_embedding_live"].status == "skipped"
    assert checks["postgres_readiness_live"].status == "skipped"
    assert checks["telegram_send_live"].status == "skipped"
    assert report.failed is False


def test_adapter_smoke_report_is_redacted_and_machine_readable() -> None:
    report = run_adapter_smoke_matrix(
        include_live=True,
        environment={
            "LEARNLOOP_SMOKE_YOUTUBE_URL": "",
            "OPENAI_API_KEY": "",
            "LEARNLOOP_SMOKE_DATABASE_URL": "",
            "TELEGRAM_BOT_TOKEN": "123:secret-token",
            "LEARNLOOP_SMOKE_TELEGRAM_CHAT_ID": "chat-secret",
        },
    )

    encoded = render_report(report, as_json=True)
    parsed = json.loads(encoded)
    assert parsed["summary"]["failed"] is False
    assert "sk-test-secret" not in encoded
    assert "secret-token" not in encoded
    assert "chat-secret" not in encoded
    assert "LearnLoop PDF smoke" not in encoded
    assert "LearnLoop URL extraction smoke content" not in encoded
    assert "adapter smoke matrix" not in encoded
