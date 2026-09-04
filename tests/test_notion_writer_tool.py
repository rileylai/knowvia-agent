import asyncio

from src.tools import (
    InMemoryNotionPageSnapshot,
    InMemoryNotionWriterClient,
    NotionWriterTool,
    ToolContext,
)


def _build_tool() -> tuple[NotionWriterTool, InMemoryNotionWriterClient]:
    client = InMemoryNotionWriterClient(
        pages={
            "page-nlp-week5": InMemoryNotionPageSnapshot(
                page_id="page-nlp-week5",
                title="NLP Week 5",
                notion_path="Knowledge/NLP/Week5",
                original_blocks=[
                    "Attention aligns query and key vectors.",
                    "Residual connections stabilize deep transformers.",
                ],
            )
        }
    )
    return NotionWriterTool(client), client


def test_notion_writer_tool_appends_only_under_ai_supplement_zone() -> None:
    tool, client = _build_tool()
    context = ToolContext(workflow_id="wf-append-001")
    before_blocks = list(
        client.get_page_snapshot("page-nlp-week5").original_blocks  # type: ignore[union-attr]
    )

    result = asyncio.run(
        tool.run(
            context=context,
            arguments={
                "page_id": "page-nlp-week5",
                "change_request_id": 21,
                "topic_title": "Positional Encoding Supplement",
                "source_display_name": "week5-attention.pdf",
                "summary": "Adds concise positional encoding notes for Week 5.",
                "concepts": ["positional encoding", "length generalization"],
                "notes": ["Compare sinusoidal and learned embeddings."],
                "append_date": "2026-05-27",
            },
        )
    )

    assert result.is_error is False
    assert result.structured_content is not None
    assert (
        result.structured_content["target_path"]
        == "Knowledge/NLP/Week5/AI Supplement Zone/2026-05-27/Positional Encoding Supplement"
    )
    assert result.structured_content["created_date_group"] is True
    assert result.structured_content["idempotent_replay"] is False
    assert result.structured_content["section_lines"] == [
        "Source: week5-attention.pdf",
        "Summary: Adds concise positional encoding notes for Week 5.",
        "Key Concepts: positional encoding; length generalization",
        "Notes:",
        "- Compare sinusoidal and learned embeddings.",
        "LearnLoop Change Request: change-request-21",
    ]

    page_snapshot = client.get_page_snapshot("page-nlp-week5")
    assert page_snapshot is not None
    assert page_snapshot.original_blocks == before_blocks
    assert len(page_snapshot.ai_supplement_entries) == 1
    assert page_snapshot.ai_supplement_entries[0].target_path.endswith(
        "/AI Supplement Zone/2026-05-27/Positional Encoding Supplement"
    )

    operations = client.list_operations(page_id="page-nlp-week5")
    assert len(operations) == 1
    assert operations[0].operation == "append_ai_supplement_zone"


def test_notion_writer_tool_is_idempotent_per_change_request() -> None:
    tool, client = _build_tool()
    context = ToolContext(workflow_id="wf-append-002")
    arguments = {
        "page_id": "page-nlp-week5",
        "change_request_id": 22,
        "topic_title": "Attention Scaling Note",
        "source_display_name": "chat-week5",
        "summary": "Clarifies scaling by sqrt(d_k).",
        "concepts": ["scaled dot-product attention"],
        "notes": ["Mention numerical stability."],
        "append_date": "2026-05-27",
    }

    first = asyncio.run(tool.run(context=context, arguments=arguments))
    second = asyncio.run(tool.run(context=context, arguments=arguments))

    assert first.is_error is False
    assert second.is_error is False
    assert first.structured_content is not None
    assert second.structured_content is not None
    assert first.structured_content["idempotent_replay"] is False
    assert second.structured_content["idempotent_replay"] is True

    page_snapshot = client.get_page_snapshot("page-nlp-week5")
    assert page_snapshot is not None
    assert len(page_snapshot.ai_supplement_entries) == 1
    assert len(client.list_operations(page_id="page-nlp-week5")) == 1


def test_notion_writer_tool_detects_durable_append_with_fresh_client() -> None:
    pages = {
        "page-nlp-week5": InMemoryNotionPageSnapshot(
            page_id="page-nlp-week5",
            title="NLP Week 5",
            notion_path="Knowledge/NLP/Week5",
        )
    }
    first_client = InMemoryNotionWriterClient(pages)
    first_tool = NotionWriterTool(first_client)
    arguments = {
        "page_id": "page-nlp-week5",
        "change_request_id": 23,
        "topic_title": "Durable Identity",
        "source_display_name": "source",
        "summary": "The identity survives a client restart.",
        "concepts": ["retry safety"],
        "notes": [],
        "append_date": "2026-05-27",
    }

    first = asyncio.run(
        first_tool.run(
            context=ToolContext(workflow_id="wf-append-005"),
            arguments=arguments,
        )
    )

    second_client = InMemoryNotionWriterClient(pages)
    second_tool = NotionWriterTool(second_client)
    second = asyncio.run(
        second_tool.run(
            context=ToolContext(workflow_id="wf-append-006"),
            arguments=arguments,
        )
    )

    assert first.is_error is False
    assert second.is_error is False
    assert second.structured_content is not None
    assert second.structured_content["idempotent_replay"] is True
    assert len(pages["page-nlp-week5"].ai_supplement_entries) == 1
    assert first_client.list_operations(page_id="page-nlp-week5")
    assert second_client.list_operations(page_id="page-nlp-week5") == []


def test_notion_writer_tool_returns_not_found_for_missing_page() -> None:
    tool = NotionWriterTool(InMemoryNotionWriterClient(pages={}))
    context = ToolContext(workflow_id="wf-append-003")

    result = asyncio.run(
        tool.run(
            context=context,
            arguments={
                "page_id": "missing-page",
                "change_request_id": 31,
                "topic_title": "Missing Page",
                "source_display_name": "source",
                "summary": "summary",
                "concepts": ["concept"],
                "notes": [],
            },
        )
    )

    assert result.is_error is True
    assert result.error is not None
    assert result.error.code == "NOTION_PAGE_NOT_FOUND"


def test_notion_writer_tool_validates_required_fields() -> None:
    tool, _ = _build_tool()
    context = ToolContext(workflow_id="wf-append-004")

    result = asyncio.run(
        tool.run(
            context=context,
            arguments={
                "page_id": "page-nlp-week5",
                "change_request_id": 0,
                "topic_title": "",
                "source_display_name": "source",
                "summary": "summary",
                "concepts": [],
                "notes": [],
            },
        )
    )

    assert result.is_error is True
    assert result.error is not None
    assert result.error.code == "INVALID_ARGUMENT"
