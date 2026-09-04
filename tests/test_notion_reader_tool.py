import asyncio
from datetime import datetime, timezone

from src.tools import (
    InMemoryNotionReaderClient,
    NotionBlockNode,
    NotionPageTree,
    NotionReaderTool,
    ToolContext,
)


class FailingNotionReaderClient(InMemoryNotionReaderClient):
    def fetch_page_tree(self, page_id: str):
        raise RuntimeError(f"boom-{page_id}")


def _sample_page_tree() -> NotionPageTree:
    return NotionPageTree(
        page_id="page-nlp-week5",
        title="NLP Week 5",
        notion_path="Knowledge/NLP/Week5",
        last_edited_time=datetime(2026, 7, 27, 12, tzinfo=timezone.utc),
        blocks=[
            NotionBlockNode(
                block_id="blk-attention",
                block_type="heading_2",
                content_text="Attention",
                block_path="Knowledge/NLP/Week5/Attention",
                children=[
                    NotionBlockNode(
                        block_id="blk-sdp",
                        block_type="bulleted_list_item",
                        content_text="Scaled dot-product attention",
                        block_path="Knowledge/NLP/Week5/Attention/Scaled dot-product attention",
                    )
                ],
            )
        ],
    )


def test_notion_reader_tool_reads_block_tree_and_paths() -> None:
    client = InMemoryNotionReaderClient({"page-nlp-week5": _sample_page_tree()})
    tool = NotionReaderTool(client)
    context = ToolContext(workflow_id="wf-001")

    result = asyncio.run(tool.run(context=context, arguments={"page_id": "page-nlp-week5"}))

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["page"]["notion_path"] == "Knowledge/NLP/Week5"
    assert (
        result.structured_content["page"]["last_edited_time"]
        == "2026-07-27T12:00:00+00:00"
    )
    assert result.content is not None
    assert "Path: Knowledge/NLP/Week5" in result.content
    assert "path=Knowledge/NLP/Week5/Attention" in result.content
    assert "path=Knowledge/NLP/Week5/Attention/Scaled dot-product attention" in result.content


def test_notion_reader_tool_returns_not_found_for_missing_page() -> None:
    client = InMemoryNotionReaderClient({})
    tool = NotionReaderTool(client)
    context = ToolContext(workflow_id="wf-001")

    result = asyncio.run(tool.run(context=context, arguments={"page_id": "missing-page"}))

    assert result.is_error is True
    assert result.error is not None
    assert result.error.code == "NOTION_PAGE_NOT_FOUND"


def test_notion_reader_tool_returns_invalid_argument_for_missing_page_id() -> None:
    client = InMemoryNotionReaderClient({})
    tool = NotionReaderTool(client)
    context = ToolContext(workflow_id="wf-001")

    result = asyncio.run(tool.run(context=context, arguments={}))

    assert result.is_error is True
    assert result.error is not None
    assert result.error.code == "INVALID_ARGUMENT"


def test_notion_reader_tool_lists_page_ids_for_full_index_discovery() -> None:
    client = InMemoryNotionReaderClient(
        {
            "page-b": _sample_page_tree(),
            "page-a": NotionPageTree(
                page_id="page-a",
                title="Earlier Page",
                notion_path="Knowledge/Earlier",
            ),
        }
    )
    tool = NotionReaderTool(client)
    context = ToolContext(workflow_id="wf-001")

    result = asyncio.run(
        tool.run(context=context, arguments={"action": "list_pages"})
    )

    assert result.is_error is False
    assert result.structured_content == {
        "pages": [
            {
                "page_id": "page-a",
                "title": "Earlier Page",
                "parent_notion_page_id": None,
                "last_edited_time": None,
            },
            {
                "page_id": "page-nlp-week5",
                "title": "NLP Week 5",
                "parent_notion_page_id": None,
                "last_edited_time": "2026-07-27T12:00:00+00:00",
            },
        ]
    }


def test_notion_reader_tool_returns_fetch_failed_when_client_raises() -> None:
    client = FailingNotionReaderClient({})
    tool = NotionReaderTool(client)
    context = ToolContext(workflow_id="wf-001")

    result = asyncio.run(tool.run(context=context, arguments={"page_id": "page-1"}))

    assert result.is_error is True
    assert result.error is not None
    assert result.error.code == "NOTION_BLOCK_FETCH_FAILED"
