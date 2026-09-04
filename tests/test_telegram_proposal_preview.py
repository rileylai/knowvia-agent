from __future__ import annotations

from types import SimpleNamespace

from src.services.telegram_proposal_preview import (
    format_bounded_note_lines,
    truncate_telegram_preview,
)


def test_notes_render_as_bounded_bullets_and_preview_truncates_safely() -> None:
    lines = format_bounded_note_lines(["first note", "second note"])
    assert lines == ["- first note", "- second note"]

    preview = truncate_telegram_preview("\n".join(lines) + "\n" + ("x" * 5000))
    assert len(preview) <= 4096
    assert preview.startswith("- first note\n- second note")
    assert preview.endswith("…")


def test_preview_source_is_rendered_from_final_structured_source() -> None:
    source = SimpleNamespace(
        source_type="screenshot",
        source_display_name="Screenshot batch (7 images)",
    )
    proposal = SimpleNamespace(
        title="Index and query analysis",
        source=source,
        source_display_name=source.source_display_name,
        summary="Index supports the query access path.",
        concepts=["Index"],
        notes=["Index supports the query access path."],
    )
    item = SimpleNamespace(
        change_request_id=33007,
        proposal=proposal,
        citations=[],
    )

    from src.orchestrators.telegram_ingestion_orchestrator import (
        TelegramIngestionOrchestrator,
    )

    orchestrator = TelegramIngestionOrchestrator.__new__(
        TelegramIngestionOrchestrator
    )
    rendered = orchestrator._format_proposal_preview(
        item=item,
        target_notion_page_id="synthetic-page",
        target_notion_path="Knowledge/Database/AI Supplement Zone",
        source_type="screenshot",
        source_document_id=33007,
        source_count=7,
    )

    assert "Source: Screenshot batch (7 images)" in rendered
