from __future__ import annotations

import json
import secrets
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


TELEGRAM_UPLOAD_SESSION_TTL_SECONDS = 600
TELEGRAM_CALLBACK_TTL_SECONDS = 600

# Callback data is intentionally opaque to Telegram.  The server-side mapping
# keeps the semantic family explicit so review actions cannot fall through to
# the upload/page-picker state machine.
TELEGRAM_CALLBACK_KIND_REVIEW = "review"
TELEGRAM_CALLBACK_KIND_PICKER = "picker"
TELEGRAM_CALLBACK_KIND_OPERATOR = "operator"
TELEGRAM_CALLBACK_KIND_UNKNOWN = "unknown"

TELEGRAM_REVIEW_CALLBACK_ACTIONS = frozenset(
    {"accept", "reject", "change_target"}
)
TELEGRAM_PICKER_CALLBACK_ACTIONS = frozenset(
    {
        "open_page",
        "select_target",
        "back",
        "root",
        "next_page",
        "previous_page",
        "change_target_select",
    }
)
TELEGRAM_OPERATOR_CALLBACK_ACTIONS = frozenset(
    {
        "sync_toggle",
        "sync_confirm",
        "sync_cancel",
        "index_full_confirm",
        "index_full_cancel",
        "pending_view",
    }
)


def infer_telegram_callback_kind(action: str) -> str:
    """Infer the callback family for mappings written before callback_kind existed."""

    normalized_action = str(action or "").strip().lower()
    if normalized_action in TELEGRAM_REVIEW_CALLBACK_ACTIONS:
        return TELEGRAM_CALLBACK_KIND_REVIEW
    if normalized_action in TELEGRAM_PICKER_CALLBACK_ACTIONS:
        return TELEGRAM_CALLBACK_KIND_PICKER
    if normalized_action in TELEGRAM_OPERATOR_CALLBACK_ACTIONS:
        return TELEGRAM_CALLBACK_KIND_OPERATOR
    return TELEGRAM_CALLBACK_KIND_UNKNOWN


@dataclass(frozen=True)
class TelegramUploadAttachment:
    kind: str
    file_id: str
    message_id: Optional[int] = None
    file_unique_id: Optional[str] = None
    file_name: Optional[str] = None
    mime_type: Optional[str] = None
    file_size: Optional[int] = None


@dataclass
class TelegramUploadSession:
    session_id: str
    chat_id: str
    user_id: str
    media_group_id: Optional[str]
    attachments: List[TelegramUploadAttachment] = field(default_factory=list)
    command_text: Optional[str] = None
    state: str = "collecting"
    settle_scheduled: bool = False
    settle_version: int = 0
    target_notion_page_id: Optional[str] = None
    target_notion_path: Optional[str] = None
    source_document_id: Optional[int] = None
    change_request_id: Optional[int] = None
    source_type: Optional[str] = None
    preview_sent: bool = False
    preview_delivery_status: str = "not_started"
    receipt_sent: bool = False
    picker_sent: bool = False
    failure_reason: Optional[str] = None
    updated_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class TelegramCallbackAction:
    token: str
    session_id: str
    action: str
    callback_kind: str = TELEGRAM_CALLBACK_KIND_UNKNOWN
    change_request_id: Optional[int] = None
    target_notion_page_id: Optional[str] = None
    target_notion_path: Optional[str] = None
    picker_mode: str = "upload"
    navigation_page_id: Optional[str] = None
    navigation_page_number: int = 1


class TelegramSessionStore(ABC):
    @abstractmethod
    def upsert_upload(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
        media_group_id: Optional[str],
        attachments: List[TelegramUploadAttachment],
        command_text: Optional[str],
    ) -> TelegramUploadSession:
        raise NotImplementedError

    @abstractmethod
    def get_upload(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
    ) -> Optional[TelegramUploadSession]:
        raise NotImplementedError

    @abstractmethod
    def find_latest_upload(
        self,
        *,
        chat_id: str,
        user_id: str,
    ) -> Optional[TelegramUploadSession]:
        raise NotImplementedError

    @abstractmethod
    def mark_awaiting_target(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
    ) -> Optional[TelegramUploadSession]:
        raise NotImplementedError

    @abstractmethod
    def claim_settle(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
    ) -> bool:
        raise NotImplementedError

    @abstractmethod
    def claim_settled(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
        settle_version: Optional[int] = None,
    ) -> bool:
        """Atomically promote one scheduled session to settled."""
        raise NotImplementedError

    @abstractmethod
    def claim_picker(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
    ) -> bool:
        raise NotImplementedError

    @abstractmethod
    def claim_receipt(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
    ) -> bool:
        raise NotImplementedError

    @abstractmethod
    def claim_target(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
        target_notion_page_id: str,
        target_notion_path: str,
    ) -> tuple[str, Optional[TelegramUploadSession]]:
        """Atomically claim target processing.

        ``retry`` is returned for a failed proposal whose source document is
        already persisted.  That path must reuse the source instead of
        downloading and OCR-ing the attachments again.
        """
        raise NotImplementedError

    @abstractmethod
    def record_proposal(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
        source_document_id: int,
        change_request_id: int,
        source_type: str,
        target_notion_page_id: str,
        target_notion_path: str,
    ) -> Optional[TelegramUploadSession]:
        raise NotImplementedError

    @abstractmethod
    def record_source_document(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
        source_document_id: int,
        source_type: str,
        target_notion_page_id: Optional[str] = None,
        target_notion_path: Optional[str] = None,
    ) -> Optional[TelegramUploadSession]:
        """Persist source identity before proposal generation starts."""
        raise NotImplementedError

    @abstractmethod
    def claim_retry(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
    ) -> tuple[str, Optional[TelegramUploadSession]]:
        """Claim a failed proposal retry without re-ingesting its source."""
        raise NotImplementedError

    @abstractmethod
    def claim_preview(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
    ) -> bool:
        raise NotImplementedError

    @abstractmethod
    def complete_preview(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
        success: bool,
        failure_reason: Optional[str] = None,
    ) -> Optional[TelegramUploadSession]:
        raise NotImplementedError

    @abstractmethod
    def fail_upload(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
        failure_reason: str,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def create_callback(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
        action: str,
        callback_kind: Optional[str] = None,
        change_request_id: Optional[int] = None,
        target_notion_page_id: Optional[str] = None,
        target_notion_path: Optional[str] = None,
        picker_mode: str = "upload",
        navigation_page_id: Optional[str] = None,
        navigation_page_number: int = 1,
    ) -> str:
        raise NotImplementedError

    @abstractmethod
    def resolve_callback(
        self,
        *,
        token: str,
        chat_id: str,
        user_id: str,
    ) -> Optional[TelegramCallbackAction]:
        raise NotImplementedError

    def claim_callback(
        self,
        *,
        token: str,
        chat_id: str,
        user_id: str,
    ) -> bool:
        """Atomically consume one-shot callbacks when the store supports it."""

        return self.resolve_callback(
            token=token,
            chat_id=chat_id,
            user_id=user_id,
        ) is not None


def _session_key(chat_id: str, user_id: str, session_id: str) -> str:
    return f"learnloop:telegram:upload:{chat_id}:{user_id}:{session_id}"


def _latest_key(chat_id: str, user_id: str) -> str:
    return f"learnloop:telegram:upload-latest:{chat_id}:{user_id}"


def _callback_key(chat_id: str, user_id: str, token: str) -> str:
    return f"learnloop:telegram:callback:{chat_id}:{user_id}:{token}"


def _session_to_json(session: TelegramUploadSession) -> str:
    payload = asdict(session)
    payload["attachments"] = [asdict(item) for item in session.attachments]
    return json.dumps(payload, sort_keys=True)


def _session_from_json(raw: str) -> TelegramUploadSession:
    payload = json.loads(raw)
    payload["attachments"] = [
        TelegramUploadAttachment(**item) for item in payload.get("attachments", [])
    ]
    return TelegramUploadSession(**payload)


def _callback_from_payload(*, token: str, payload: dict[str, Any]) -> TelegramCallbackAction:
    """Build a callback mapping with legacy callback records normalized safely."""

    action = str(payload.get("action") or "").strip().lower()
    callback_kind = str(payload.get("callback_kind") or "").strip().lower()
    if not callback_kind:
        callback_kind = infer_telegram_callback_kind(action)
    raw_change_request_id = payload.get("change_request_id")
    try:
        change_request_id = (
            int(raw_change_request_id)
            if raw_change_request_id is not None
            else None
        )
    except (TypeError, ValueError):
        change_request_id = None
    raw_page_number = payload.get("navigation_page_number")
    try:
        navigation_page_number = max(1, int(raw_page_number or 1))
    except (TypeError, ValueError):
        navigation_page_number = 1
    raw_navigation_page_id = payload.get("navigation_page_id")
    return TelegramCallbackAction(
        token=token,
        session_id=str(payload.get("session_id") or ""),
        action=action,
        callback_kind=callback_kind,
        change_request_id=change_request_id,
        target_notion_page_id=payload.get("target_notion_page_id"),
        target_notion_path=payload.get("target_notion_path"),
        picker_mode=str(payload.get("picker_mode") or "upload").strip().lower(),
        navigation_page_id=(
            str(raw_navigation_page_id).strip()
            if raw_navigation_page_id is not None
            else None
        ),
        navigation_page_number=navigation_page_number,
    )


def _attachment_sort_key(attachment: TelegramUploadAttachment) -> tuple:
    """Sort media-group attachments deterministically by Telegram message id."""

    return (
        attachment.message_id is None,
        attachment.message_id if attachment.message_id is not None else 0,
        attachment.file_unique_id or attachment.file_id,
    )


def _sort_attachments(
    attachments: List[TelegramUploadAttachment],
) -> List[TelegramUploadAttachment]:
    return sorted(attachments, key=_attachment_sort_key)


class InMemoryTelegramSessionStore(TelegramSessionStore):
    """Deterministic test/demo store with the same ownership semantics as Redis."""

    def __init__(self, *, ttl_seconds: int = TELEGRAM_UPLOAD_SESSION_TTL_SECONDS) -> None:
        self._ttl_seconds = ttl_seconds
        self._sessions: Dict[tuple[str, str, str], TelegramUploadSession] = {}
        self._latest: Dict[tuple[str, str], str] = {}
        self._callbacks: Dict[tuple[str, str, str], TelegramCallbackAction] = {}
        self._callback_expiry: Dict[tuple[str, str, str], float] = {}
        self._lock = threading.RLock()

    def _get_unlocked(self, *, session_id: str, chat_id: str, user_id: str):
        session = self._sessions.get((chat_id, user_id, session_id))
        if session is None:
            return None
        if session.updated_at + self._ttl_seconds <= time.time():
            self._sessions.pop((chat_id, user_id, session_id), None)
            if self._latest.get((chat_id, user_id)) == session_id:
                self._latest.pop((chat_id, user_id), None)
            return None
        return session

    def upsert_upload(self, **kwargs) -> TelegramUploadSession:
        with self._lock:
            key = (kwargs["chat_id"], kwargs["user_id"], kwargs["session_id"])
            session = self._get_unlocked(
                session_id=kwargs["session_id"],
                chat_id=kwargs["chat_id"],
                user_id=kwargs["user_id"],
            )
            if session is None:
                session = TelegramUploadSession(
                    session_id=kwargs["session_id"],
                    chat_id=kwargs["chat_id"],
                    user_id=kwargs["user_id"],
                    media_group_id=kwargs["media_group_id"],
                )
                self._sessions[key] = session
            if session.state not in {"collecting", "settling"}:
                return session
            known = {item.file_unique_id or item.file_id for item in session.attachments}
            added_attachment = False
            for attachment in kwargs["attachments"]:
                identity = attachment.file_unique_id or attachment.file_id
                if identity not in known:
                    session.attachments.append(attachment)
                    known.add(identity)
                    added_attachment = True
            if added_attachment:
                # A new Telegram update reopens the debounce claim. The
                # previous delayed job remains queued but its version becomes
                # stale once the gateway claims the refreshed settle.
                session.settle_scheduled = False
            if kwargs.get("command_text"):
                session.command_text = kwargs["command_text"]
            session.updated_at = time.time()
            self._latest[(kwargs["chat_id"], kwargs["user_id"])] = kwargs["session_id"]
            return session

    def get_upload(self, **kwargs):
        with self._lock:
            return self._get_unlocked(**kwargs)

    def find_latest_upload(self, **kwargs):
        with self._lock:
            session_id = self._latest.get((kwargs["chat_id"], kwargs["user_id"]))
            if session_id is None:
                return None
            return self._get_unlocked(
                session_id=session_id,
                chat_id=kwargs["chat_id"],
                user_id=kwargs["user_id"],
            )

    def mark_awaiting_target(self, **kwargs):
        with self._lock:
            session = self._get_unlocked(**kwargs)
            if session is not None and session.state in {"collecting", "settling"}:
                session.state = "awaiting_target"
                session.picker_sent = True
                session.updated_at = time.time()
            return session

    def claim_settle(self, **kwargs) -> bool:
        with self._lock:
            session = self._get_unlocked(**kwargs)
            if (
                session is None
                or session.state != "collecting"
                or session.settle_scheduled
            ):
                return False
            # Every media-group update refreshes the debounce version. Older
            # delayed jobs become stale and skip; only the last quiet-period
            # job can promote the complete batch.
            session.settle_scheduled = True
            session.settle_version += 1
            session.updated_at = time.time()
            return True

    def claim_settled(self, **kwargs) -> bool:
        with self._lock:
            expected_version = kwargs.get("settle_version")
            session = self._get_unlocked(
                session_id=kwargs["session_id"],
                chat_id=kwargs["chat_id"],
                user_id=kwargs["user_id"],
            )
            if session is None or session.picker_sent:
                return False
            if expected_version is not None:
                if (
                    session.state != "collecting"
                    or not session.settle_scheduled
                    or session.settle_version != expected_version
                ):
                    return False
            elif session.state not in {"collecting", "settling"}:
                return False
            session.attachments = _sort_attachments(session.attachments)
            session.state = "settled"
            session.settle_scheduled = False
            session.updated_at = time.time()
            return True

    def claim_picker(self, **kwargs) -> bool:
        with self._lock:
            session = self._get_unlocked(**kwargs)
            if session is None or session.picker_sent:
                return False
            if session.state not in {
                "collecting",
                "settling",
                "settled",
                "awaiting_target",
            }:
                return False
            session.state = "awaiting_target"
            session.picker_sent = True
            session.updated_at = time.time()
            return True

    def claim_receipt(self, **kwargs) -> bool:
        with self._lock:
            session = self._get_unlocked(**kwargs)
            if session is None or session.receipt_sent:
                return False
            session.receipt_sent = True
            session.updated_at = time.time()
            return True

    def claim_target(self, **kwargs):
        with self._lock:
            session = self._get_unlocked(
                session_id=kwargs["session_id"],
                chat_id=kwargs["chat_id"],
                user_id=kwargs["user_id"],
            )
            if session is None:
                return "missing", None
            if session.state == "proposal_created":
                return "already", session
            if session.state == "processing":
                return "in_progress", session
            if (
                session.state == "failed"
                and session.failure_reason == "LLM_OUTPUT_INVALID"
                and session.source_document_id is not None
                and session.source_type == "screenshot"
            ):
                session.state = "processing"
                session.failure_reason = None
                session.target_notion_page_id = kwargs["target_notion_page_id"]
                session.target_notion_path = kwargs["target_notion_path"]
                session.updated_at = time.time()
                return "retry", session
            if session.state != "awaiting_target":
                return "invalid", session
            session.state = "processing"
            session.target_notion_page_id = kwargs["target_notion_page_id"]
            session.target_notion_path = kwargs["target_notion_path"]
            session.updated_at = time.time()
            return "new", session

    def record_proposal(self, **kwargs):
        with self._lock:
            session = self._get_unlocked(
                session_id=kwargs["session_id"],
                chat_id=kwargs["chat_id"],
                user_id=kwargs["user_id"],
            )
            if session is None:
                return None
            session.source_document_id = kwargs["source_document_id"]
            session.change_request_id = kwargs["change_request_id"]
            session.source_type = kwargs["source_type"]
            session.target_notion_page_id = kwargs["target_notion_page_id"]
            session.target_notion_path = kwargs["target_notion_path"]
            session.state = "proposal_created"
            session.updated_at = time.time()
            return session

    def record_source_document(self, **kwargs):
        with self._lock:
            session = self._get_unlocked(
                session_id=kwargs["session_id"],
                chat_id=kwargs["chat_id"],
                user_id=kwargs["user_id"],
            )
            if session is None:
                return None
            session.source_document_id = int(kwargs["source_document_id"])
            session.source_type = str(kwargs["source_type"])
            if kwargs.get("target_notion_page_id"):
                session.target_notion_page_id = kwargs["target_notion_page_id"]
            if kwargs.get("target_notion_path"):
                session.target_notion_path = kwargs["target_notion_path"]
            session.updated_at = time.time()
            return session

    def claim_retry(self, **kwargs):
        with self._lock:
            session = self._get_unlocked(
                session_id=kwargs["session_id"],
                chat_id=kwargs["chat_id"],
                user_id=kwargs["user_id"],
            )
            if session is None:
                return "missing", None
            if session.state == "proposal_created":
                return "already", session
            if session.state == "processing":
                return "in_progress", session
            if (
                session.state != "failed"
                or session.failure_reason != "LLM_OUTPUT_INVALID"
                or session.source_document_id is None
                or session.target_notion_page_id is None
                or session.target_notion_path is None
            ):
                return "invalid", session
            session.state = "processing"
            session.failure_reason = None
            session.updated_at = time.time()
            return "new", session

    def claim_preview(self, **kwargs) -> bool:
        with self._lock:
            session = self._get_unlocked(**kwargs)
            if session is None or session.preview_sent:
                return False
            if session.preview_delivery_status in {"sending", "succeeded"}:
                return False
            session.preview_delivery_status = "sending"
            session.updated_at = time.time()
            return True

    def complete_preview(self, **kwargs):
        with self._lock:
            session = self._get_unlocked(
                session_id=kwargs["session_id"],
                chat_id=kwargs["chat_id"],
                user_id=kwargs["user_id"],
            )
            if session is None:
                return None
            session.preview_delivery_status = "succeeded" if kwargs["success"] else "failed"
            session.preview_sent = bool(kwargs["success"])
            if not kwargs["success"]:
                session.failure_reason = kwargs.get("failure_reason")
            session.updated_at = time.time()
            return session

    def fail_upload(self, **kwargs) -> None:
        with self._lock:
            session = self._get_unlocked(
                session_id=kwargs["session_id"],
                chat_id=kwargs["chat_id"],
                user_id=kwargs["user_id"],
            )
            if session is not None:
                session.state = "failed"
                session.failure_reason = kwargs["failure_reason"]
                session.updated_at = time.time()

    def create_callback(self, **kwargs) -> str:
        with self._lock:
            token = secrets.token_urlsafe(12)
            payload = {
                "session_id": kwargs["session_id"],
                "action": kwargs["action"],
                "callback_kind": kwargs.get("callback_kind"),
                "change_request_id": kwargs.get("change_request_id"),
                "target_notion_page_id": kwargs.get("target_notion_page_id"),
                "target_notion_path": kwargs.get("target_notion_path"),
                "picker_mode": kwargs.get("picker_mode", "upload"),
                "navigation_page_id": kwargs.get("navigation_page_id"),
                "navigation_page_number": kwargs.get("navigation_page_number", 1),
            }
            action = _callback_from_payload(
                token=token,
                payload=payload,
            )
            key = (kwargs["chat_id"], kwargs["user_id"], token)
            self._callbacks[key] = action
            self._callback_expiry[key] = time.time() + TELEGRAM_CALLBACK_TTL_SECONDS
            return token

    def resolve_callback(self, **kwargs):
        with self._lock:
            key = (kwargs["chat_id"], kwargs["user_id"], kwargs["token"])
            if self._callback_expiry.get(key, 0) <= time.time():
                self._callbacks.pop(key, None)
                self._callback_expiry.pop(key, None)
                return None
            return self._callbacks.get(key)

    def claim_callback(self, **kwargs) -> bool:
        with self._lock:
            key = (kwargs["chat_id"], kwargs["user_id"], kwargs["token"])
            if self._callback_expiry.get(key, 0) <= time.time():
                self._callbacks.pop(key, None)
                self._callback_expiry.pop(key, None)
                return False
            action = self._callbacks.pop(key, None)
            self._callback_expiry.pop(key, None)
            return action is not None


class RedisTelegramSessionStore(TelegramSessionStore):
    def __init__(
        self,
        *,
        redis_client: Any,
        ttl_seconds: int = TELEGRAM_UPLOAD_SESSION_TTL_SECONDS,
    ) -> None:
        self._redis = redis_client
        self._ttl_seconds = ttl_seconds

    def _locked(self, key: str):
        return self._redis.lock(
            f"{key}:lock",
            timeout=10,
            blocking_timeout=5,
        )

    def _get(self, *, session_id: str, chat_id: str, user_id: str):
        raw = self._redis.get(_session_key(chat_id, user_id, session_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return _session_from_json(str(raw))

    def upsert_upload(self, **kwargs):
        key = _session_key(kwargs["chat_id"], kwargs["user_id"], kwargs["session_id"])
        with self._locked(key):
            session = self._get(
                session_id=kwargs["session_id"],
                chat_id=kwargs["chat_id"],
                user_id=kwargs["user_id"],
            )
            if session is None:
                session = TelegramUploadSession(
                    session_id=kwargs["session_id"],
                    chat_id=kwargs["chat_id"],
                    user_id=kwargs["user_id"],
                    media_group_id=kwargs["media_group_id"],
                )
            if session.state not in {"collecting", "settling"}:
                return session
            known = {item.file_unique_id or item.file_id for item in session.attachments}
            added_attachment = False
            for attachment in kwargs["attachments"]:
                identity = attachment.file_unique_id or attachment.file_id
                if identity not in known:
                    session.attachments.append(attachment)
                    known.add(identity)
                    added_attachment = True
            if added_attachment:
                # Refresh debounce only for a genuinely new attachment. A
                # duplicate Telegram delivery stays idempotent.
                session.settle_scheduled = False
            if kwargs.get("command_text"):
                session.command_text = kwargs["command_text"]
            session.updated_at = time.time()
            self._redis.setex(key, self._ttl_seconds, _session_to_json(session))
            self._redis.setex(
                _latest_key(kwargs["chat_id"], kwargs["user_id"]),
                self._ttl_seconds,
                kwargs["session_id"],
            )
            return session

    def get_upload(self, **kwargs):
        return self._get(
            session_id=kwargs["session_id"],
            chat_id=kwargs["chat_id"],
            user_id=kwargs["user_id"],
        )

    def find_latest_upload(self, **kwargs):
        raw = self._redis.get(_latest_key(kwargs["chat_id"], kwargs["user_id"]))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return self._get(
            session_id=str(raw),
            chat_id=kwargs["chat_id"],
            user_id=kwargs["user_id"],
        )

    def mark_awaiting_target(self, **kwargs):
        key = _session_key(kwargs["chat_id"], kwargs["user_id"], kwargs["session_id"])
        with self._locked(key):
            session = self._get(
                session_id=kwargs["session_id"],
                chat_id=kwargs["chat_id"],
                user_id=kwargs["user_id"],
            )
            if session is not None and session.state in {"collecting", "settling"}:
                session.state = "awaiting_target"
                session.picker_sent = True
                session.updated_at = time.time()
                self._redis.setex(key, self._ttl_seconds, _session_to_json(session))
            return session

    def claim_settle(self, **kwargs) -> bool:
        key = _session_key(kwargs["chat_id"], kwargs["user_id"], kwargs["session_id"])
        with self._locked(key):
            session = self._get(
                session_id=kwargs["session_id"],
                chat_id=kwargs["chat_id"],
                user_id=kwargs["user_id"],
            )
            if (
                session is None
                or session.state != "collecting"
                or session.settle_scheduled
            ):
                return False
            # Refresh the debounce version for every update in the same
            # media_group_id.  Previously the first scheduled job won even
            # when later Telegram updates had already appended attachments.
            session.settle_scheduled = True
            session.settle_version += 1
            session.updated_at = time.time()
            self._redis.setex(key, self._ttl_seconds, _session_to_json(session))
            return True

    def claim_settled(self, **kwargs) -> bool:
        key = _session_key(kwargs["chat_id"], kwargs["user_id"], kwargs["session_id"])
        with self._locked(key):
            session = self._get(
                session_id=kwargs["session_id"],
                chat_id=kwargs["chat_id"],
                user_id=kwargs["user_id"],
            )
            expected_version = kwargs.get("settle_version")
            if session is None or session.picker_sent:
                return False
            if expected_version is not None:
                if (
                    session.state != "collecting"
                    or not session.settle_scheduled
                    or session.settle_version != expected_version
                ):
                    return False
            elif session.state not in {"collecting", "settling"}:
                return False
            session.attachments = _sort_attachments(session.attachments)
            session.state = "settled"
            session.settle_scheduled = False
            session.updated_at = time.time()
            self._redis.setex(key, self._ttl_seconds, _session_to_json(session))
            return True

    def claim_picker(self, **kwargs) -> bool:
        key = _session_key(kwargs["chat_id"], kwargs["user_id"], kwargs["session_id"])
        with self._locked(key):
            session = self._get(
                session_id=kwargs["session_id"],
                chat_id=kwargs["chat_id"],
                user_id=kwargs["user_id"],
            )
            if session is None or session.picker_sent:
                return False
            if session.state not in {
                "collecting",
                "settling",
                "settled",
                "awaiting_target",
            }:
                return False
            session.state = "awaiting_target"
            session.picker_sent = True
            session.updated_at = time.time()
            self._redis.setex(key, self._ttl_seconds, _session_to_json(session))
            return True

    def claim_receipt(self, **kwargs) -> bool:
        key = _session_key(kwargs["chat_id"], kwargs["user_id"], kwargs["session_id"])
        with self._locked(key):
            session = self._get(
                session_id=kwargs["session_id"],
                chat_id=kwargs["chat_id"],
                user_id=kwargs["user_id"],
            )
            if session is None or session.receipt_sent:
                return False
            session.receipt_sent = True
            session.updated_at = time.time()
            self._redis.setex(key, self._ttl_seconds, _session_to_json(session))
            return True

    def claim_target(self, **kwargs):
        key = _session_key(kwargs["chat_id"], kwargs["user_id"], kwargs["session_id"])
        with self._locked(key):
            session = self._get(
                session_id=kwargs["session_id"],
                chat_id=kwargs["chat_id"],
                user_id=kwargs["user_id"],
            )
            if session is None:
                return "missing", None
            if session.state == "proposal_created":
                return "already", session
            if session.state == "processing":
                return "in_progress", session
            if (
                session.state == "failed"
                and session.failure_reason == "LLM_OUTPUT_INVALID"
                and session.source_document_id is not None
                and session.source_type == "screenshot"
            ):
                session.state = "processing"
                session.failure_reason = None
                session.target_notion_page_id = kwargs["target_notion_page_id"]
                session.target_notion_path = kwargs["target_notion_path"]
                session.updated_at = time.time()
                self._redis.setex(key, self._ttl_seconds, _session_to_json(session))
                return "retry", session
            if session.state != "awaiting_target":
                return "invalid", session
            session.state = "processing"
            session.target_notion_page_id = kwargs["target_notion_page_id"]
            session.target_notion_path = kwargs["target_notion_path"]
            session.updated_at = time.time()
            self._redis.setex(key, self._ttl_seconds, _session_to_json(session))
            return "new", session

    def record_proposal(self, **kwargs):
        key = _session_key(kwargs["chat_id"], kwargs["user_id"], kwargs["session_id"])
        with self._locked(key):
            session = self._get(
                session_id=kwargs["session_id"],
                chat_id=kwargs["chat_id"],
                user_id=kwargs["user_id"],
            )
            if session is None:
                return None
            session.source_document_id = kwargs["source_document_id"]
            session.change_request_id = kwargs["change_request_id"]
            session.source_type = kwargs["source_type"]
            session.target_notion_page_id = kwargs["target_notion_page_id"]
            session.target_notion_path = kwargs["target_notion_path"]
            session.state = "proposal_created"
            session.updated_at = time.time()
            self._redis.setex(key, self._ttl_seconds, _session_to_json(session))
            return session

    def record_source_document(self, **kwargs):
        key = _session_key(kwargs["chat_id"], kwargs["user_id"], kwargs["session_id"])
        with self._locked(key):
            session = self._get(
                session_id=kwargs["session_id"],
                chat_id=kwargs["chat_id"],
                user_id=kwargs["user_id"],
            )
            if session is None:
                return None
            session.source_document_id = int(kwargs["source_document_id"])
            session.source_type = str(kwargs["source_type"])
            if kwargs.get("target_notion_page_id"):
                session.target_notion_page_id = kwargs["target_notion_page_id"]
            if kwargs.get("target_notion_path"):
                session.target_notion_path = kwargs["target_notion_path"]
            session.updated_at = time.time()
            self._redis.setex(key, self._ttl_seconds, _session_to_json(session))
            return session

    def claim_retry(self, **kwargs):
        key = _session_key(kwargs["chat_id"], kwargs["user_id"], kwargs["session_id"])
        with self._locked(key):
            session = self._get(
                session_id=kwargs["session_id"],
                chat_id=kwargs["chat_id"],
                user_id=kwargs["user_id"],
            )
            if session is None:
                return "missing", None
            if session.state == "proposal_created":
                return "already", session
            if session.state == "processing":
                return "in_progress", session
            if (
                session.state != "failed"
                or session.failure_reason != "LLM_OUTPUT_INVALID"
                or session.source_document_id is None
                or session.target_notion_page_id is None
                or session.target_notion_path is None
            ):
                return "invalid", session
            session.state = "processing"
            session.failure_reason = None
            session.updated_at = time.time()
            self._redis.setex(key, self._ttl_seconds, _session_to_json(session))
            return "new", session

    def claim_preview(self, **kwargs) -> bool:
        key = _session_key(kwargs["chat_id"], kwargs["user_id"], kwargs["session_id"])
        with self._locked(key):
            session = self._get(
                session_id=kwargs["session_id"],
                chat_id=kwargs["chat_id"],
                user_id=kwargs["user_id"],
            )
            if session is None or session.preview_sent:
                return False
            if session.preview_delivery_status in {"sending", "succeeded"}:
                return False
            session.preview_delivery_status = "sending"
            session.updated_at = time.time()
            self._redis.setex(key, self._ttl_seconds, _session_to_json(session))
            return True

    def complete_preview(self, **kwargs):
        key = _session_key(kwargs["chat_id"], kwargs["user_id"], kwargs["session_id"])
        with self._locked(key):
            session = self._get(
                session_id=kwargs["session_id"],
                chat_id=kwargs["chat_id"],
                user_id=kwargs["user_id"],
            )
            if session is None:
                return None
            session.preview_delivery_status = "succeeded" if kwargs["success"] else "failed"
            session.preview_sent = bool(kwargs["success"])
            if not kwargs["success"]:
                session.failure_reason = kwargs.get("failure_reason")
            session.updated_at = time.time()
            self._redis.setex(key, self._ttl_seconds, _session_to_json(session))
            return session

    def fail_upload(self, **kwargs) -> None:
        key = _session_key(kwargs["chat_id"], kwargs["user_id"], kwargs["session_id"])
        with self._locked(key):
            session = self._get(
                session_id=kwargs["session_id"],
                chat_id=kwargs["chat_id"],
                user_id=kwargs["user_id"],
            )
            if session is not None:
                session.state = "failed"
                session.failure_reason = kwargs["failure_reason"]
                session.updated_at = time.time()
                self._redis.setex(key, self._ttl_seconds, _session_to_json(session))

    def create_callback(self, **kwargs) -> str:
        token = secrets.token_urlsafe(12)
        payload = {
            "session_id": kwargs["session_id"],
            "action": kwargs["action"],
            "callback_kind": kwargs.get("callback_kind")
            or infer_telegram_callback_kind(kwargs["action"]),
            "change_request_id": kwargs.get("change_request_id"),
            "target_notion_page_id": kwargs.get("target_notion_page_id"),
            "target_notion_path": kwargs.get("target_notion_path"),
            "picker_mode": kwargs.get("picker_mode", "upload"),
            "navigation_page_id": kwargs.get("navigation_page_id"),
            "navigation_page_number": kwargs.get("navigation_page_number", 1),
        }
        self._redis.setex(
            _callback_key(kwargs["chat_id"], kwargs["user_id"], token),
            TELEGRAM_CALLBACK_TTL_SECONDS,
            json.dumps(payload, sort_keys=True),
        )
        return token

    def resolve_callback(self, **kwargs):
        key = _callback_key(kwargs["chat_id"], kwargs["user_id"], kwargs["token"])
        raw = self._redis.get(key)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        payload = json.loads(raw)
        return _callback_from_payload(token=kwargs["token"], payload=payload)

    def claim_callback(self, **kwargs) -> bool:
        key = _callback_key(kwargs["chat_id"], kwargs["user_id"], kwargs["token"])
        with self._locked(key):
            raw = self._redis.get(key)
            if raw is None:
                return False
            self._redis.delete(key)
            return True
