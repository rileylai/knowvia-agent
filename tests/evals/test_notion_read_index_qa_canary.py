from __future__ import annotations

import asyncio

from src.tools import NotionHTTPResponse, NotionHTTPTransport

from .notion_read_index_qa_canary import (
    CANARY_QUERY_ENV,
    CANARY_PAGE_ID_ENV,
    NOTION_TOKEN_ENV,
    NotionCanaryWriteBlocked,
    NotionReadAudit,
    NotionReadIndexQACanaryReport,
    RecordingReadOnlyNotionHTTPTransport,
    _is_allowed_read_operation,
    run_canary_workflow,
    run_notion_read_index_qa_canary,
)
from src.tools import (
    InMemoryNotionReaderClient,
    NotionBlockNode,
    NotionPageTree,
    NotionReaderClient,
    NotionReaderClientError,
)


class _NoopTransport(NotionHTTPTransport):
    def get_json(self, *, path, query, headers):
        return NotionHTTPResponse(status_code=200, payload={})

    def post_json(self, *, path, query, headers, payload):
        return NotionHTTPResponse(status_code=200, payload={})


def _reader() -> InMemoryNotionReaderClient:
    page = NotionPageTree(
        page_id="synthetic-page",
        title="Synthetic Step 82",
        notion_path="Knowledge/Synthetic Step 82",
        blocks=[
            NotionBlockNode(
                block_id="synthetic-block",
                block_type="paragraph",
                content_text="LearnLoop Step 82 canary anchor for QA.",
                block_path="Knowledge/Synthetic Step 82",
            )
        ],
    )
    return InMemoryNotionReaderClient({page.page_id: page})


class _DiscoveryFailureReader(NotionReaderClient):
    def list_pages(self):
        raise NotionReaderClientError(
            code="NOTION_BLOCK_FETCH_FAILED",
            message="safe synthetic discovery failure",
        )

    def fetch_page_tree(self, page_id):
        _ = page_id
        return None


def test_default_canary_is_opt_in_and_does_not_contact_notion() -> None:
    report = run_notion_read_index_qa_canary(
        include_live=False,
        environment={NOTION_TOKEN_ENV: "redacted"},
    )

    assert report == NotionReadIndexQACanaryReport(
        status="skipped",
        message="live Notion canary is disabled",
    )


def test_live_canary_requires_explicit_configuration() -> None:
    report = run_notion_read_index_qa_canary(
        include_live=True,
        environment={NOTION_TOKEN_ENV: ""},
    )

    assert report.status == "failed"
    assert report.failed_stage == "configuration"
    assert report.failure_reason == "NOTION_AUTH_FAILED"
    assert report.notion_request_count == 0
    assert report.notion_write_attempt_count == 0


def test_canary_workflow_indexes_incrementally_and_returns_scoped_citation() -> None:
    report = asyncio.run(
        run_canary_workflow(
            reader_client=_reader(),
            target_page_id="synthetic-page",
            query="LearnLoop Step 82 canary anchor",
        )
    )

    assert report.status == "passed"
    assert report.indexed_page_count == 1
    assert report.indexed_block_count == 1
    assert report.indexed_chunk_count == 1
    assert report.incremental_page_count == 1
    assert report.citation_count == 1


def test_canary_reports_full_discovery_failure_without_raw_exception() -> None:
    report = asyncio.run(
        run_canary_workflow(
            reader_client=_DiscoveryFailureReader(),
            target_page_id="synthetic-page",
            query="LearnLoop Step 82 canary anchor",
        )
    )

    assert report.status == "failed"
    assert report.failed_stage == "full_discovery"
    assert report.failure_reason == "NOTION_BLOCK_FETCH_FAILED"
    assert report.message == "Notion read/index/QA canary failed at full_discovery"
    assert "synthetic discovery failure" not in str(report.to_dict())


def test_transport_allows_only_reader_operations_and_blocks_writes() -> None:
    audit = NotionReadAudit()
    transport = RecordingReadOnlyNotionHTTPTransport(
        audit=audit,
        delegate=_NoopTransport(),
    )

    transport.get_json(path="/v1/pages/page-id", query={}, headers={})
    transport.post_json(
        path="/v1/search",
        query={},
        headers={},
        payload={},
    )

    try:
        transport.patch_json(
            path="/v1/blocks/block-id/children",
            query={},
            headers={},
            payload={},
        )
    except NotionCanaryWriteBlocked:
        pass
    else:
        raise AssertionError("the canary transport must block Notion writes")

    assert all(_is_allowed_read_operation(entry) for entry in audit.entries[:2])
    assert len(audit.write_attempts) == 1
    assert audit.write_attempts[0].method == "PATCH"


def test_report_operation_audit_is_redacted() -> None:
    audit = NotionReadAudit()
    audit.record(method="GET", path="/v1/pages/private-page-id")

    report = asyncio.run(
        run_canary_workflow(
            reader_client=_reader(),
            target_page_id="synthetic-page",
            query="LearnLoop Step 82 canary anchor",
            audit=audit,
        )
    )

    assert report.notion_operations == [
        "GET /v1/pages/{id}",
    ]
    assert "private-page-id" not in str(report.to_dict())
