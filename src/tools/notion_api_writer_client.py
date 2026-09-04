from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple
from urllib.parse import quote

from src.tools.notion_api_reader_client import (
    DEFAULT_NOTION_API_BASE_URL,
    DEFAULT_NOTION_VERSION,
    NotionAPIClientError,
    NotionAPIReaderClient,
    NotionHTTPTransport,
    NotionHTTPTransportError,
    UrllibNotionHTTPTransport,
)
from src.tools.notion_reader_tool import NotionBlockNode, NotionPageTree
from src.tools.notion_writer_tool import (
    NOTION_APPEND_IDENTITY_PREFIX,
    NotionAppendRequest,
    NotionAppendResult,
    NotionWriterAuthError,
    NotionWriterClient,
    NotionWriterClientError,
    NotionWriterPageNotFoundError,
)

MAX_NOTION_RICH_TEXT_CHARS = 2000


class NotionAPIWriterClient(NotionWriterClient):
    """Append-only Notion REST client with no update, delete, or move methods."""

    def __init__(
        self,
        *,
        token: str,
        transport: Optional[NotionHTTPTransport] = None,
        base_url: str = DEFAULT_NOTION_API_BASE_URL,
        notion_version: str = DEFAULT_NOTION_VERSION,
        timeout_seconds: float = 10.0,
        infrastructure_exception_classifier: Optional[
            Callable[[BaseException], Optional[BaseException]]
        ] = None,
    ) -> None:
        normalized_token = token.strip()
        if not normalized_token:
            raise NotionWriterClientError(
                "Notion token is not configured. Set NOTION_TOKEN."
            )
        normalized_version = notion_version.strip()
        if not normalized_version:
            raise ValueError("notion_version must not be empty")
        self._token = normalized_token
        self._notion_version = normalized_version
        self._infrastructure_exception_classifier = (
            infrastructure_exception_classifier
        )
        self._transport = transport or UrllibNotionHTTPTransport(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )
        self._reader = NotionAPIReaderClient(
            token=normalized_token,
            transport=self._transport,
            base_url=base_url,
            notion_version=normalized_version,
            timeout_seconds=timeout_seconds,
            infrastructure_exception_classifier=(
                infrastructure_exception_classifier
            ),
        )

    def append_to_ai_supplement_zone(
        self,
        *,
        request: NotionAppendRequest,
    ) -> NotionAppendResult:
        page = self._read_page(request.page_id)
        existing_result = self.find_ai_supplement_by_identity(
            page_id=request.page_id,
            idempotency_key=request.idempotency_key,
            change_request_id=request.change_request_id,
        )
        if existing_result is not None:
            return existing_result

        root_blocks = page.blocks
        zone = self._find_direct_container(
            blocks=root_blocks,
            title="AI Supplement Zone",
        )
        created_zone = zone is None
        zone_id = zone.block_id if zone is not None else request.page_id
        if zone is None:
            zone_id = self._append_children(
                parent_id=request.page_id,
                children=[self._build_toggle_block("AI Supplement Zone")],
            )[0]

        current_zone_children = zone.children if zone is not None else []
        date_group = self._find_direct_container(
            blocks=current_zone_children,
            title=request.append_date,
        )
        created_date_group = date_group is None
        date_parent_id = date_group.block_id if date_group is not None else zone_id
        if date_group is None:
            date_parent_id = self._append_children(
                parent_id=zone_id,
                children=[self._build_toggle_block(request.append_date)],
            )[0]

        topic_parent_id = self._append_children(
            parent_id=date_parent_id,
            children=[self._build_toggle_block(request.topic_title)],
        )[0]
        section_lines = self._build_section_lines(request=request)
        self._append_children(
            parent_id=topic_parent_id,
            children=[self._build_paragraph_block(line) for line in section_lines],
        )

        return NotionAppendResult(
            page_id=request.page_id,
            change_request_id=request.change_request_id,
            target_path=(
                f"{page.notion_path}/AI Supplement Zone/"
                f"{request.append_date}/{request.topic_title}"
            ),
            appended_block_count=(
                len(section_lines) + 2 + int(created_zone) + int(created_date_group)
            ),
            created_date_group=created_date_group,
            idempotent_replay=False,
            section_lines=section_lines,
        )

    def find_ai_supplement_by_identity(
        self,
        *,
        page_id: str,
        idempotency_key: str,
        change_request_id: Optional[int] = None,
    ) -> Optional[NotionAppendResult]:
        page = self._read_page(page_id)
        identity = f"{NOTION_APPEND_IDENTITY_PREFIX}{idempotency_key}"
        found = self._find_block_with_identity(page.blocks, identity)
        if found is None:
            return None

        identity_block, target_path = found
        topic_block = self._find_block_by_path(page.blocks, target_path)
        section_lines = (
            [child.content_text for child in topic_block.children]
            if topic_block is not None
            else [identity_block.content_text]
        )
        return NotionAppendResult(
            page_id=page_id,
            change_request_id=(
                change_request_id
                if change_request_id is not None
                else self._parse_change_request_id(idempotency_key)
            ),
            target_path=target_path,
            appended_block_count=(
                self._count_blocks(topic_block) if topic_block is not None else 1
            ),
            created_date_group=False,
            idempotent_replay=True,
            section_lines=section_lines,
        )

    def _read_page(self, page_id: str) -> NotionPageTree:
        try:
            page = self._reader.fetch_page_tree(page_id)
        except NotionAPIClientError as exc:
            if exc.code == "NOTION_AUTH_FAILED":
                raise NotionWriterAuthError(str(exc)) from exc
            raise NotionWriterClientError(
                "Failed to read Notion page before append"
            ) from exc
        if page is None:
            raise NotionWriterPageNotFoundError(
                f"Notion page is not found: page_id={page_id}"
            )
        return page

    def _append_children(
        self,
        *,
        parent_id: str,
        children: List[Dict[str, Any]],
    ) -> List[str]:
        try:
            response = self._transport.patch_json(
                path=f"/v1/blocks/{quote(parent_id, safe='')}/children",
                query={},
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Notion-Version": self._notion_version,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                payload={"children": children},
            )
        except NotionHTTPTransportError:
            raise NotionWriterClientError(
                "Notion append transport request failed"
            ) from None
        except Exception as exc:
            infrastructure_error = (
                self._infrastructure_exception_classifier(exc)
                if self._infrastructure_exception_classifier is not None
                else None
            )
            if infrastructure_error is not None:
                raise infrastructure_error from exc
            raise NotionWriterClientError("Notion append request failed") from None

        if response.status_code in (401, 403):
            raise NotionWriterAuthError("Notion authorization failed")
        if response.status_code == 404:
            raise NotionWriterPageNotFoundError(
                f"Notion append parent is not found: parent_id={parent_id}"
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise NotionWriterClientError("Notion append request failed")
        if not isinstance(response.payload, dict):
            raise NotionWriterClientError("Notion append response is invalid")
        raw_results = response.payload.get("results")
        if not isinstance(raw_results, list) or len(raw_results) != len(children):
            raise NotionWriterClientError("Notion append response is invalid")
        block_ids: List[str] = []
        for result in raw_results:
            block_id = result.get("id") if isinstance(result, dict) else None
            if not isinstance(block_id, str) or not block_id.strip():
                raise NotionWriterClientError("Notion append response is invalid")
            block_ids.append(block_id.strip())
        return block_ids

    def _build_section_lines(self, *, request: NotionAppendRequest) -> List[str]:
        concepts_text = "; ".join(request.concepts)
        return [
            f"Source: {request.source_display_name}",
            f"Summary: {request.summary}",
            f"Key Concepts: {concepts_text}",
            "Notes:",
            *(f"- {note}" for note in request.notes),
            f"{NOTION_APPEND_IDENTITY_PREFIX}{request.idempotency_key}",
        ]

    def _build_toggle_block(self, title: str) -> Dict[str, Any]:
        return {
            "object": "block",
            "type": "toggle",
            "toggle": {"rich_text": self._rich_text(title)},
        }

    def _build_paragraph_block(self, text: str) -> Dict[str, Any]:
        return {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": self._rich_text(text)},
        }

    def _rich_text(self, text: str) -> List[Dict[str, Any]]:
        return [
            {
                "type": "text",
                "text": {"content": text[index : index + MAX_NOTION_RICH_TEXT_CHARS]},
            }
            for index in range(0, len(text), MAX_NOTION_RICH_TEXT_CHARS)
        ] or [{"type": "text", "text": {"content": ""}}]

    def _find_direct_container(
        self,
        *,
        blocks: List[NotionBlockNode],
        title: str,
    ) -> Optional[NotionBlockNode]:
        for block in blocks:
            if block.block_type == "toggle" and block.content_text.strip() == title:
                return block
        return None

    def _find_block_with_identity(
        self,
        blocks: List[NotionBlockNode],
        identity: str,
    ) -> Optional[Tuple[NotionBlockNode, str]]:
        for block in blocks:
            if block.content_text.strip() == identity:
                target_path = block.block_path.rsplit("/", 1)[0]
                return block, target_path
            found = self._find_block_with_identity(block.children, identity)
            if found is not None:
                return found
        return None

    def _find_block_by_path(
        self,
        blocks: List[NotionBlockNode],
        block_path: str,
    ) -> Optional[NotionBlockNode]:
        for block in blocks:
            if block.block_path == block_path:
                return block
            found = self._find_block_by_path(block.children, block_path)
            if found is not None:
                return found
        return None

    def _count_blocks(self, block: NotionBlockNode) -> int:
        return 1 + sum(self._count_blocks(child) for child in block.children)

    def _parse_change_request_id(self, idempotency_key: str) -> int:
        prefix = "change-request-"
        suffix = idempotency_key[len(prefix) :]
        try:
            return int(suffix)
        except ValueError:
            return 0
