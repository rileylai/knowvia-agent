from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib import error, request
from urllib.parse import quote

from src.observability.redaction import sanitize_sensitive_text
from src.tools.base import Tool
from src.tools.models import ToolContext, ToolResult, ToolSpec


class TelegramBotClientError(Exception):
    pass


class TelegramBotNotConfiguredError(TelegramBotClientError):
    pass


class TelegramBotSendError(TelegramBotClientError):
    pass


class TelegramBotFileDownloadError(TelegramBotClientError):
    pass


@dataclass
class TelegramSentMessage:
    chat_id: str
    text: str
    message_id: int
    reply_markup: Optional[Dict[str, Any]] = None


@dataclass
class TelegramDownloadedFile:
    file_id: str
    file_name: str
    file_bytes: bytes


class TelegramBotClient:
    def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> TelegramSentMessage:
        raise NotImplementedError

    def download_file(self, *, file_id: str) -> TelegramDownloadedFile:
        raise NotImplementedError

    def answer_callback_query(
        self,
        *,
        callback_query_id: str,
        text: Optional[str] = None,
    ) -> None:
        raise NotImplementedError


class DisabledTelegramBotClient(TelegramBotClient):
    def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> TelegramSentMessage:
        _ = chat_id
        _ = text
        raise TelegramBotNotConfiguredError(
            "Telegram bot token is not configured. Set TELEGRAM_BOT_TOKEN."
        )

    def download_file(self, *, file_id: str) -> TelegramDownloadedFile:
        _ = file_id
        raise TelegramBotNotConfiguredError(
            "Telegram bot token is not configured. Set TELEGRAM_BOT_TOKEN."
        )

    def answer_callback_query(self, *, callback_query_id: str, text: Optional[str] = None) -> None:
        _ = callback_query_id
        _ = text
        raise TelegramBotNotConfiguredError(
            "Telegram bot token is not configured. Set TELEGRAM_BOT_TOKEN."
        )


class InMemoryTelegramBotClient(TelegramBotClient):
    def __init__(self) -> None:
        self._sent_messages: List[TelegramSentMessage] = []
        self._files: Dict[str, TelegramDownloadedFile] = {}
        self._callback_answers: List[Dict[str, Optional[str]]] = []

    def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> TelegramSentMessage:
        message = TelegramSentMessage(
            chat_id=chat_id,
            text=text,
            message_id=len(self._sent_messages) + 1,
            reply_markup=reply_markup,
        )
        self._sent_messages.append(message)
        return message

    def list_sent_messages(self) -> List[TelegramSentMessage]:
        return list(self._sent_messages)

    def list_callback_answers(self) -> List[Dict[str, Optional[str]]]:
        return list(self._callback_answers)

    def answer_callback_query(
        self,
        *,
        callback_query_id: str,
        text: Optional[str] = None,
    ) -> None:
        self._callback_answers.append(
            {"callback_query_id": callback_query_id, "text": text}
        )

    def add_file(
        self,
        *,
        file_id: str,
        file_bytes: bytes,
        file_name: Optional[str] = None,
    ) -> None:
        normalized_file_id = file_id.strip()
        if not normalized_file_id:
            raise ValueError("file_id must not be empty")
        if not file_bytes:
            raise ValueError("file_bytes must not be empty")
        normalized_file_name = (file_name or "").strip() or f"{normalized_file_id}.bin"
        self._files[normalized_file_id] = TelegramDownloadedFile(
            file_id=normalized_file_id,
            file_name=normalized_file_name,
            file_bytes=file_bytes,
        )

    def download_file(self, *, file_id: str) -> TelegramDownloadedFile:
        normalized_file_id = file_id.strip()
        if not normalized_file_id:
            raise TelegramBotFileDownloadError("file_id is required")
        downloaded = self._files.get(normalized_file_id)
        if downloaded is None:
            raise TelegramBotFileDownloadError(
                f"Telegram file is not found: file_id={normalized_file_id}"
            )
        return downloaded


class TelegramHTTPBotClient(TelegramBotClient):
    def __init__(
        self,
        *,
        bot_token: str,
        timeout_seconds: float = 10.0,
    ) -> None:
        normalized_token = bot_token.strip()
        if not normalized_token:
            raise TelegramBotNotConfiguredError(
                "Telegram bot token is empty. Set TELEGRAM_BOT_TOKEN."
            )
        self._bot_token = normalized_token
        self._timeout_seconds = timeout_seconds

    def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> TelegramSentMessage:
        payload = {
            "chat_id": chat_id,
            "text": text,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        req = request.Request(
            url=f"https://api.telegram.org/bot{self._bot_token}/sendMessage",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self._timeout_seconds) as response:
                response_body = response.read().decode("utf-8")
        except error.URLError as exc:
            raise TelegramBotSendError(
                f"Telegram request failed: {sanitize_sensitive_text(str(exc))}"
            ) from exc

        try:
            parsed = json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise TelegramBotSendError(
                "Telegram response is not valid JSON"
            ) from exc

        if not isinstance(parsed, dict) or not parsed.get("ok"):
            description = ""
            if isinstance(parsed, dict):
                description = str(parsed.get("description", "")).strip()
            if description:
                raise TelegramBotSendError(f"Telegram sendMessage failed: {description}")
            raise TelegramBotSendError("Telegram sendMessage failed")

        result = parsed.get("result")
        if not isinstance(result, dict):
            raise TelegramBotSendError("Telegram response missing result object")

        raw_message_id = result.get("message_id")
        try:
            message_id = int(raw_message_id)
        except (TypeError, ValueError) as exc:
            raise TelegramBotSendError(
                "Telegram response missing valid message_id"
            ) from exc

        return TelegramSentMessage(
            chat_id=chat_id,
            text=text,
            message_id=message_id,
            reply_markup=reply_markup,
        )

    def answer_callback_query(
        self,
        *,
        callback_query_id: str,
        text: Optional[str] = None,
    ) -> None:
        payload: Dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        self._post_json_method(method_name="answerCallbackQuery", payload=payload)

    def download_file(self, *, file_id: str) -> TelegramDownloadedFile:
        normalized_file_id = file_id.strip()
        if not normalized_file_id:
            raise TelegramBotFileDownloadError("file_id is required")

        try:
            get_file_result = self._post_json_method(
                method_name="getFile",
                payload={"file_id": normalized_file_id},
            )
        except TelegramBotSendError as exc:
            raise TelegramBotFileDownloadError(
                sanitize_sensitive_text(str(exc))
            ) from exc

        file_path = str(get_file_result.get("file_path", "")).strip()
        if not file_path:
            raise TelegramBotFileDownloadError("Telegram getFile response missing file_path")

        encoded_file_path = quote(file_path, safe="/")
        file_url = f"https://api.telegram.org/file/bot{self._bot_token}/{encoded_file_path}"
        try:
            with request.urlopen(file_url, timeout=self._timeout_seconds) as response:
                file_bytes = response.read()
        except error.URLError as exc:
            raise TelegramBotFileDownloadError(
                f"Telegram file download failed: {sanitize_sensitive_text(str(exc))}"
            ) from exc

        if not file_bytes:
            raise TelegramBotFileDownloadError(
                f"Downloaded Telegram file is empty: file_id={normalized_file_id}"
            )

        file_name = os.path.basename(file_path.strip("/")) or f"{normalized_file_id}.bin"
        return TelegramDownloadedFile(
            file_id=normalized_file_id,
            file_name=file_name,
            file_bytes=file_bytes,
        )

    def _post_json_method(
        self,
        *,
        method_name: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        req = request.Request(
            url=f"https://api.telegram.org/bot{self._bot_token}/{method_name}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self._timeout_seconds) as response:
                response_body = response.read().decode("utf-8")
        except error.URLError as exc:
            raise TelegramBotSendError(
                f"Telegram request failed: {sanitize_sensitive_text(str(exc))}"
            ) from exc

        try:
            parsed = json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise TelegramBotSendError(
                "Telegram response is not valid JSON"
            ) from exc

        if not isinstance(parsed, dict) or not parsed.get("ok"):
            description = ""
            if isinstance(parsed, dict):
                description = str(parsed.get("description", "")).strip()
            if description:
                raise TelegramBotSendError(
                    f"Telegram {method_name} failed: {description}"
                )
            raise TelegramBotSendError(f"Telegram {method_name} failed")

        result = parsed.get("result")
        if not isinstance(result, dict):
            raise TelegramBotSendError("Telegram response missing result object")
        return result


class TelegramBotTool(Tool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="telegram_bot",
            description="Send Telegram messages through the bot API adapter.",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "send_message",
                            "download_file",
                            "answer_callback_query",
                        ],
                    },
                    "chat_id": {"type": "string"},
                    "text": {"type": "string"},
                    "file_id": {"type": "string"},
                    "reply_markup": {"type": "object"},
                    "callback_query_id": {"type": "string"},
                },
            },
            output_schema={"type": "object"},
        )

    def __init__(self, telegram_client: TelegramBotClient) -> None:
        self._telegram_client = telegram_client

    async def run(self, context: ToolContext, arguments: Dict[str, Any]) -> ToolResult:
        _ = context
        action = str(arguments.get("action", "send_message")).strip().lower()
        if action == "send_message":
            return self._run_send_message(arguments)
        if action == "download_file":
            return self._run_download_file(arguments)
        if action == "answer_callback_query":
            return self._run_answer_callback_query(arguments)
        return ToolResult.failure(
            code="INVALID_ARGUMENT",
            message=f"Unsupported telegram_bot action: {action}",
        )

    def _run_send_message(self, arguments: Dict[str, Any]) -> ToolResult:
        chat_id = str(arguments.get("chat_id", "")).strip()
        text = str(arguments.get("text", "")).strip()
        reply_markup = arguments.get("reply_markup")
        if not chat_id:
            return ToolResult.failure(
                code="INVALID_ARGUMENT",
                message="chat_id is required",
            )
        if not text:
            return ToolResult.failure(
                code="INVALID_ARGUMENT",
                message="text is required",
            )

        try:
            if isinstance(reply_markup, dict):
                sent = self._telegram_client.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=reply_markup,
                )
            else:
                sent = self._telegram_client.send_message(chat_id=chat_id, text=text)
        except TelegramBotNotConfiguredError as exc:
            return ToolResult.failure(
                code="TELEGRAM_NOT_CONFIGURED",
                message=sanitize_sensitive_text(str(exc)),
            )
        except TelegramBotSendError as exc:
            return ToolResult.failure(
                code="TELEGRAM_SEND_FAILED",
                message=sanitize_sensitive_text(str(exc)),
            )
        except TelegramBotClientError as exc:
            return ToolResult.failure(
                code="UNKNOWN_ERROR",
                message=sanitize_sensitive_text(str(exc)),
            )

        return ToolResult.success(
            content=f"sent telegram message to chat_id={sent.chat_id}",
            structured_content={
                "chat_id": sent.chat_id,
                "text": sent.text,
                "message_id": sent.message_id,
                "reply_markup": sent.reply_markup,
            },
        )

    def _run_answer_callback_query(self, arguments: Dict[str, Any]) -> ToolResult:
        callback_query_id = str(arguments.get("callback_query_id", "")).strip()
        if not callback_query_id:
            return ToolResult.failure(
                code="INVALID_ARGUMENT",
                message="callback_query_id is required",
            )
        text_value = str(arguments.get("text", "")).strip() or None
        try:
            self._telegram_client.answer_callback_query(
                callback_query_id=callback_query_id,
                text=text_value,
            )
        except TelegramBotNotConfiguredError as exc:
            return ToolResult.failure(
                code="TELEGRAM_CALLBACK_ACK_FAILED",
                message=sanitize_sensitive_text(str(exc)),
            )
        except TelegramBotSendError as exc:
            return ToolResult.failure(
                code="TELEGRAM_CALLBACK_ACK_FAILED",
                message=sanitize_sensitive_text(str(exc)),
            )
        except TelegramBotClientError as exc:
            return ToolResult.failure(
                code="TELEGRAM_CALLBACK_ACK_FAILED",
                message=sanitize_sensitive_text(str(exc)),
            )
        return ToolResult.success(
            content="answered telegram callback query",
            structured_content={"callback_query_id": callback_query_id},
        )

    def _run_download_file(self, arguments: Dict[str, Any]) -> ToolResult:
        file_id = str(arguments.get("file_id", "")).strip()
        if not file_id:
            return ToolResult.failure(
                code="INVALID_ARGUMENT",
                message="file_id is required",
            )

        try:
            downloaded = self._telegram_client.download_file(file_id=file_id)
        except TelegramBotNotConfiguredError as exc:
            return ToolResult.failure(
                code="TELEGRAM_NOT_CONFIGURED",
                message=sanitize_sensitive_text(str(exc)),
            )
        except TelegramBotFileDownloadError as exc:
            return ToolResult.failure(
                code="TELEGRAM_FILE_DOWNLOAD_FAILED",
                message=sanitize_sensitive_text(str(exc)),
            )
        except TelegramBotClientError as exc:
            return ToolResult.failure(
                code="UNKNOWN_ERROR",
                message=sanitize_sensitive_text(str(exc)),
            )

        encoded = base64.b64encode(downloaded.file_bytes).decode("ascii")
        return ToolResult.success(
            content=f"downloaded telegram file file_id={downloaded.file_id}",
            structured_content={
                "file_id": downloaded.file_id,
                "file_name": downloaded.file_name,
                "file_size_bytes": len(downloaded.file_bytes),
                "file_bytes_base64": encoded,
            },
        )
