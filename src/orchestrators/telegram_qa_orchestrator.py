from __future__ import annotations

import shlex
from dataclasses import dataclass
from http import HTTPStatus
from typing import List, Optional

from src.orchestrators.qa_orchestrator import (
    DEFAULT_QA_MODEL,
    DEFAULT_QA_PROVIDER_NAME,
    DEFAULT_QA_TOP_K,
    QAOrchestrator,
    QAOrchestratorError,
)

ASK_USAGE_REPLY = (
    "Usage: /ask [--page <page_id>] [--section <notion/path>] <question>"
)


@dataclass
class TelegramQACommandResult:
    reply_text: str
    qa_workflow_run_id: Optional[int]
    insufficient_info: Optional[bool]
    citation_paths: List[str]


@dataclass
class _ParsedAskCommand:
    query: str
    page_ids: List[str]
    section_paths: List[str]


class TelegramQAError(Exception):
    def __init__(
        self,
        *,
        error_code: str,
        message: str,
        http_status_code: int,
        failure_reason: str = "UNKNOWN_ERROR",
        workflow_run_id: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.http_status_code = http_status_code
        self.failure_reason = failure_reason
        self.workflow_run_id = workflow_run_id


class TelegramQAOrchestrator:
    def __init__(self, *, qa_orchestrator: QAOrchestrator) -> None:
        self._qa_orchestrator = qa_orchestrator

    async def handle_ask_command(
        self,
        *,
        command_text: str,
        request_workflow_id: str,
    ) -> TelegramQACommandResult:
        parsed = self._parse_ask_command(command_text)
        if not parsed.query:
            return TelegramQACommandResult(
                reply_text=ASK_USAGE_REPLY,
                qa_workflow_run_id=None,
                insufficient_info=None,
                citation_paths=[],
            )

        try:
            result = await self._qa_orchestrator.answer_question(
                query=parsed.query,
                top_k=DEFAULT_QA_TOP_K,
                page_ids=parsed.page_ids or None,
                section_paths=parsed.section_paths or None,
                source_kinds=["notion"],
                provider_name=DEFAULT_QA_PROVIDER_NAME,
                model=DEFAULT_QA_MODEL,
                request_workflow_id=request_workflow_id,
            )
        except QAOrchestratorError as exc:
            raise TelegramQAError(
                error_code=exc.error_code,
                message=exc.message,
                http_status_code=exc.http_status_code,
                failure_reason=exc.failure_reason,
                workflow_run_id=exc.workflow_run_id,
            ) from exc

        citation_paths = self._unique_paths(
            [citation.notion_path for citation in result.citations]
        )
        return TelegramQACommandResult(
            reply_text=self._build_reply(
                answer=result.answer,
                citation_paths=citation_paths,
            ),
            qa_workflow_run_id=result.workflow_run_id,
            insufficient_info=result.insufficient_info,
            citation_paths=citation_paths,
        )

    def _parse_ask_command(self, command_text: str) -> _ParsedAskCommand:
        try:
            tokens = shlex.split(command_text)
        except ValueError as exc:
            raise self._invalid_argument(f"Invalid /ask command: {exc}") from exc

        arguments = tokens[1:] if tokens else []
        page_ids: List[str] = []
        section_paths: List[str] = []
        query_parts: List[str] = []

        index = 0
        while index < len(arguments):
            token = arguments[index]
            if token in {"--page", "--section"}:
                if index + 1 >= len(arguments) or arguments[index + 1].startswith("--"):
                    raise self._invalid_argument(f"{token} requires a value")
                value = arguments[index + 1].strip()
                if not value:
                    raise self._invalid_argument(f"{token} requires a value")
                if token == "--page":
                    page_ids.append(value)
                else:
                    section_paths.append(value)
                index += 2
                continue

            if token.startswith("--page="):
                page_ids.append(self._parse_inline_scope_value(token, "--page="))
                index += 1
                continue
            if token.startswith("--section="):
                section_paths.append(self._parse_inline_scope_value(token, "--section="))
                index += 1
                continue
            if token.startswith("--"):
                raise self._invalid_argument(f"Unsupported /ask scope flag: {token}")

            query_parts.append(token)
            index += 1

        return _ParsedAskCommand(
            query=" ".join(query_parts).strip(),
            page_ids=self._unique_paths(page_ids),
            section_paths=self._unique_paths(section_paths),
        )

    def _parse_inline_scope_value(self, token: str, prefix: str) -> str:
        value = token[len(prefix) :].strip()
        if not value:
            raise self._invalid_argument(f"{prefix[:-1]} requires a value")
        return value

    def _build_reply(self, *, answer: str, citation_paths: List[str]) -> str:
        if not citation_paths:
            return answer
        citations = "\n".join(f"- {path}" for path in citation_paths)
        return f"{answer}\n\nNotion citations:\n{citations}"

    def _unique_paths(self, paths: List[str]) -> List[str]:
        unique: List[str] = []
        seen = set()
        for path in paths:
            normalized = path.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            unique.append(normalized)
        return unique

    def _invalid_argument(self, message: str) -> TelegramQAError:
        return TelegramQAError(
            error_code="INVALID_ARGUMENT",
            message=message,
            http_status_code=HTTPStatus.BAD_REQUEST,
            failure_reason="UNKNOWN_ERROR",
        )
