from __future__ import annotations

import shlex
from dataclasses import dataclass
from http import HTTPStatus
from typing import Optional

from src.orchestrators.supplement_review_orchestrator import (
    REVIEW_ACTION_ACCEPT,
    REVIEW_ACTION_REJECT,
    SupplementReviewError,
    SupplementReviewOrchestrator,
)

ACCEPT_USAGE_REPLY = "Usage: /accept <change_request_id>"
REJECT_USAGE_REPLY = "Usage: /reject <change_request_id> <reason>"


@dataclass
class TelegramReviewCommandResult:
    reply_text: str
    review_workflow_run_id: Optional[int]
    change_request_id: Optional[int]
    change_request_status: Optional[str]
    review_action: str


class TelegramReviewError(Exception):
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


class TelegramReviewOrchestrator:
    def __init__(
        self,
        *,
        supplement_review_orchestrator: SupplementReviewOrchestrator,
    ) -> None:
        self._supplement_review_orchestrator = supplement_review_orchestrator

    async def handle_review_command(
        self,
        *,
        command: str,
        command_text: str,
        chat_id: str,
        request_workflow_id: str,
    ) -> TelegramReviewCommandResult:
        normalized_command = command.strip().lower()
        normalized_chat_id = chat_id.strip()
        if not normalized_chat_id:
            raise self._invalid_argument("chat_id must not be empty")

        arguments = self._parse_arguments(command_text)
        reviewer = f"telegram-chat:{normalized_chat_id}"

        try:
            if normalized_command == REVIEW_ACTION_ACCEPT:
                if not arguments:
                    return self._usage_result(REVIEW_ACTION_ACCEPT)
                if len(arguments) != 1:
                    raise self._invalid_argument(ACCEPT_USAGE_REPLY)
                change_request_id = self._parse_change_request_id(arguments[0])
                result = await self._supplement_review_orchestrator.accept_change_request(
                    change_request_id=change_request_id,
                    reviewer=reviewer,
                    request_workflow_id=request_workflow_id,
                )
            elif normalized_command == REVIEW_ACTION_REJECT:
                if len(arguments) < 2:
                    return self._usage_result(REVIEW_ACTION_REJECT)
                change_request_id = self._parse_change_request_id(arguments[0])
                reason = " ".join(arguments[1:]).strip()
                result = await self._supplement_review_orchestrator.reject_change_request(
                    change_request_id=change_request_id,
                    reviewer=reviewer,
                    reason=reason,
                    request_workflow_id=request_workflow_id,
                )
            else:
                raise self._invalid_argument(f"Unsupported Telegram review command: {command}")
        except SupplementReviewError as exc:
            raise TelegramReviewError(
                error_code=exc.error_code,
                message=exc.message,
                http_status_code=exc.http_status_code,
                failure_reason=exc.failure_reason,
                workflow_run_id=exc.workflow_run_id,
            ) from exc

        return TelegramReviewCommandResult(
            reply_text=self._build_success_reply(
                review_action=result.review_action,
                change_request_id=result.change_request_id,
            ),
            review_workflow_run_id=result.workflow_run_id,
            change_request_id=result.change_request_id,
            change_request_status=result.change_request_status,
            review_action=result.review_action,
        )

    async def handle_change_target(
        self,
        *,
        change_request_id: int,
        target_notion_page_id: str,
        chat_id: str,
        request_workflow_id: str,
    ) -> TelegramReviewCommandResult:
        reviewer = f"telegram-chat:{chat_id.strip()}"
        try:
            result = self._supplement_review_orchestrator.change_target(
                change_request_id=change_request_id,
                target_notion_page_id=target_notion_page_id,
                reviewer=reviewer,
                request_workflow_id=request_workflow_id,
            )
        except SupplementReviewError as exc:
            raise TelegramReviewError(
                error_code=exc.error_code,
                message=exc.message,
                http_status_code=exc.http_status_code,
                failure_reason=exc.failure_reason,
                workflow_run_id=exc.workflow_run_id,
            ) from exc
        return TelegramReviewCommandResult(
            reply_text=(
                f"Target changed for pending proposal {result.change_request_id}. "
                "Review it again before explicitly accepting."
            ),
            review_workflow_run_id=result.workflow_run_id,
            change_request_id=result.change_request_id,
            change_request_status=result.change_request_status,
            review_action=result.review_action,
        )

    def _parse_arguments(self, command_text: str) -> list[str]:
        try:
            tokens = shlex.split(command_text)
        except ValueError as exc:
            raise self._invalid_argument(f"Invalid review command: {exc}") from exc
        return tokens[1:] if tokens else []

    def _parse_change_request_id(self, raw_value: str) -> int:
        try:
            change_request_id = int(raw_value)
        except ValueError as exc:
            raise self._invalid_argument("change_request_id must be an integer") from exc
        if change_request_id <= 0:
            raise self._invalid_argument("change_request_id must be positive")
        return change_request_id

    def _usage_result(self, review_action: str) -> TelegramReviewCommandResult:
        usage_reply = (
            ACCEPT_USAGE_REPLY
            if review_action == REVIEW_ACTION_ACCEPT
            else REJECT_USAGE_REPLY
        )
        return TelegramReviewCommandResult(
            reply_text=usage_reply,
            review_workflow_run_id=None,
            change_request_id=None,
            change_request_status=None,
            review_action=review_action,
        )

    def _build_success_reply(self, *, review_action: str, change_request_id: int) -> str:
        if review_action == REVIEW_ACTION_ACCEPT:
            return (
                f"Change request {change_request_id} accepted. "
                "Appended to AI Supplement Zone and page re-index completed."
            )
        return (
            f"Change request {change_request_id} rejected. "
            "No Notion write was performed."
        )

    def _invalid_argument(self, message: str) -> TelegramReviewError:
        return TelegramReviewError(
            error_code="INVALID_ARGUMENT",
            message=message,
            http_status_code=HTTPStatus.BAD_REQUEST,
            failure_reason="UNKNOWN_ERROR",
        )
