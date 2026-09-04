from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any, Dict, List, Mapping
from urllib.parse import unquote

from src.tools import NotionAPIReaderClient, NotionAPIWriterClient, NotionHTTPResponse, NotionHTTPTransport

from .notion_append_canary import (
    CANARY_PAGE_ID_ENV,
    NOTION_TOKEN_ENV,
    NotionAppendAudit,
    NotionAppendCanaryReport,
    NotionAppendCanaryWriteBlocked,
    RecordingAppendCanaryTransport,
    run_append_canary_workflow,
    run_notion_append_canary,
)


class _FakeAppendTransport(NotionHTTPTransport):
    def __init__(self) -> None:
        self._next_id = 1
        self._children: Dict[str, List[Dict[str, Any]]] = {
            "sandbox-page": [
                {
                    "id": "original-block",
                    "type": "paragraph",
                    "has_children": False,
                    "paragraph": {
                        "rich_text": [{"type": "text", "plain_text": "Original note."}]
                    },
                }
            ]
        }

    def get_json(self, *, path: str, query: Mapping[str, str], headers: Mapping[str, str]) -> NotionHTTPResponse:
        _ = query, headers
        if path == "/v1/pages/sandbox-page":
            return NotionHTTPResponse(
                status_code=200,
                payload={
                    "properties": {
                        "Name": {"title": [{"type": "text", "plain_text": "Sandbox Page"}]}
                    },
                    "last_edited_time": "2026-07-29T10:00:00.000Z",
                },
            )
        if path.startswith("/v1/blocks/") and path.endswith("/children"):
            parent_id = unquote(path[len("/v1/blocks/") : -len("/children")].strip("/"))
            return NotionHTTPResponse(
                status_code=200,
                payload={
                    "results": deepcopy(self._children.get(parent_id, [])),
                    "has_more": False,
                    "next_cursor": None,
                },
            )
        return NotionHTTPResponse(status_code=404, payload=None)

    def patch_json(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
    ) -> NotionHTTPResponse:
        _ = query, headers
        parent_id = unquote(path[len("/v1/blocks/") : -len("/children")].strip("/"))
        results: List[Dict[str, Any]] = []
        for child in payload.get("children", []):
            block_id = f"generated-{self._next_id}"
            self._next_id += 1
            block_type = str(child["type"])
            stored = {
                "id": block_id,
                "type": block_type,
                "has_children": block_type == "toggle",
                block_type: deepcopy(child[block_type]),
            }
            self._children.setdefault(parent_id, []).append(stored)
            if block_type == "toggle":
                self._children[block_id] = []
            results.append({"id": block_id})
        return NotionHTTPResponse(status_code=200, payload={"results": results})


def test_default_append_canary_is_opt_in() -> None:
    report = run_notion_append_canary(include_live=False)

    assert report == NotionAppendCanaryReport(
        status="skipped",
        message="live Notion append canary is disabled",
    )


def test_append_canary_requires_human_approval_before_configuration() -> None:
    report = run_notion_append_canary(
        include_live=True,
        approval_confirmed=False,
        environment={NOTION_TOKEN_ENV: "secret", CANARY_PAGE_ID_ENV: "sandbox-page"},
    )

    assert report.status == "failed"
    assert report.failure_code == "HUMAN_APPROVAL_REQUIRED"
    assert report.notion_request_count == 0


def test_append_canary_accepts_appends_reindexes_and_cites_same_page() -> None:
    audit = NotionAppendAudit()
    transport = RecordingAppendCanaryTransport(
        audit=audit,
        delegate=_FakeAppendTransport(),
    )
    reader = NotionAPIReaderClient(token="secret", transport=transport)
    writer = NotionAPIWriterClient(token="secret", transport=transport)

    report = asyncio.run(
        run_append_canary_workflow(
            reader_client=reader,
            writer_client=writer,
            target_page_id="sandbox-page",
            audit=audit,
        )
    )

    assert report.status == "passed"
    assert report.change_request_status == "accepted"
    assert report.identity_visible is True
    assert report.append_block_count > 0
    assert report.indexed_block_count > 1
    assert report.indexed_chunk_count > 1
    assert report.citation_count > 0
    assert report.notion_unexpected_operation_count == 0
    assert "PATCH /v1/blocks/{id}/children" in report.notion_operations
    assert "sandbox-page" not in str(report.to_dict())


def test_append_canary_transport_blocks_non_append_operations() -> None:
    audit = NotionAppendAudit()
    transport = RecordingAppendCanaryTransport(
        audit=audit,
        delegate=_FakeAppendTransport(),
    )

    try:
        transport.patch_json(
            path="/v1/pages/sandbox-page",
            query={},
            headers={},
            payload={},
        )
    except NotionAppendCanaryWriteBlocked:
        pass
    else:
        raise AssertionError("the append canary must block page PATCH operations")

    assert audit.unexpected_operations
