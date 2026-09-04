from __future__ import annotations

import json
import secrets
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


TELEGRAM_SYNC_SESSION_TTL_SECONDS = 600
TELEGRAM_SYNC_MAX_SELECTED_PAGES = 10
TELEGRAM_SYNC_MAX_DISCOVERED_PAGES = 100


@dataclass(frozen=True)
class TelegramSyncPage:
    page_id: str
    title: str
    display_path: str


@dataclass
class TelegramSyncSession:
    session_id: str
    chat_id: str
    user_id: str
    pages: list[TelegramSyncPage] = field(default_factory=list)
    selected_page_ids: list[str] = field(default_factory=list)
    state: str = "selecting"
    workflow_run_id: Optional[int] = None
    succeeded_page_count: int = 0
    failed_page_count: int = 0
    failure_reason: Optional[str] = None
    updated_at: float = field(default_factory=time.time)


class TelegramSyncSessionStore(ABC):
    @abstractmethod
    def create_session(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
        pages: list[TelegramSyncPage],
    ) -> TelegramSyncSession:
        raise NotImplementedError

    @abstractmethod
    def get_session(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
    ) -> Optional[TelegramSyncSession]:
        raise NotImplementedError

    @abstractmethod
    def toggle_page(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
        page_id: str,
    ) -> tuple[str, Optional[TelegramSyncSession]]:
        raise NotImplementedError

    @abstractmethod
    def claim_confirm(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
    ) -> tuple[str, Optional[TelegramSyncSession]]:
        raise NotImplementedError

    @abstractmethod
    def cancel(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
    ) -> Optional[TelegramSyncSession]:
        raise NotImplementedError

    @abstractmethod
    def complete(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
        state: str,
        workflow_run_id: int,
        succeeded_page_count: int,
        failed_page_count: int,
        failure_reason: Optional[str] = None,
    ) -> Optional[TelegramSyncSession]:
        raise NotImplementedError


def _sync_key(chat_id: str, user_id: str, session_id: str) -> str:
    return f"learnloop:telegram:sync:{chat_id}:{user_id}:{session_id}"


def _session_to_json(session: TelegramSyncSession) -> str:
    return json.dumps(asdict(session), sort_keys=True)


def _session_from_json(raw: str) -> TelegramSyncSession:
    payload = json.loads(raw)
    payload["pages"] = [TelegramSyncPage(**page) for page in payload.get("pages", [])]
    return TelegramSyncSession(**payload)


class InMemoryTelegramSyncSessionStore(TelegramSyncSessionStore):
    def __init__(
        self,
        *,
        ttl_seconds: int = TELEGRAM_SYNC_SESSION_TTL_SECONDS,
        max_selected_pages: int = TELEGRAM_SYNC_MAX_SELECTED_PAGES,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_selected_pages = max_selected_pages
        self._sessions: dict[tuple[str, str, str], TelegramSyncSession] = {}
        self._lock = threading.RLock()

    def _get_unlocked(self, **kwargs) -> Optional[TelegramSyncSession]:
        key = (kwargs["chat_id"], kwargs["user_id"], kwargs["session_id"])
        session = self._sessions.get(key)
        if session is None:
            return None
        if session.updated_at + self._ttl_seconds <= time.time():
            self._sessions.pop(key, None)
            return None
        return session

    def create_session(self, **kwargs) -> TelegramSyncSession:
        with self._lock:
            session = TelegramSyncSession(
                session_id=kwargs["session_id"],
                chat_id=kwargs["chat_id"],
                user_id=kwargs["user_id"],
                pages=list(kwargs["pages"]),
            )
            self._sessions[(session.chat_id, session.user_id, session.session_id)] = session
            return session

    def get_session(self, **kwargs) -> Optional[TelegramSyncSession]:
        with self._lock:
            return self._get_unlocked(**kwargs)

    def toggle_page(self, **kwargs):
        with self._lock:
            session = self._get_unlocked(**kwargs)
            if session is None:
                return "missing", None
            if session.state != "selecting":
                return "invalid", session
            page_id = str(kwargs["page_id"]).strip()
            if not any(page.page_id == page_id for page in session.pages):
                return "invalid", session
            if page_id in session.selected_page_ids:
                session.selected_page_ids.remove(page_id)
                session.updated_at = time.time()
                return "deselected", session
            if len(session.selected_page_ids) >= self._max_selected_pages:
                return "limit", session
            session.selected_page_ids.append(page_id)
            session.updated_at = time.time()
            return "selected", session

    def claim_confirm(self, **kwargs):
        with self._lock:
            session = self._get_unlocked(**kwargs)
            if session is None:
                return "missing", None
            if session.state != "selecting":
                return "already", session
            if not session.selected_page_ids:
                return "empty", session
            session.state = "processing"
            session.updated_at = time.time()
            return "claimed", session

    def cancel(self, **kwargs):
        with self._lock:
            session = self._get_unlocked(**kwargs)
            if session is None:
                return None
            if session.state == "selecting":
                session.state = "cancelled"
                session.updated_at = time.time()
            return session

    def complete(self, **kwargs):
        with self._lock:
            session = self._get_unlocked(**kwargs)
            if session is None:
                return None
            session.state = kwargs["state"]
            session.workflow_run_id = int(kwargs["workflow_run_id"])
            session.succeeded_page_count = int(kwargs["succeeded_page_count"])
            session.failed_page_count = int(kwargs["failed_page_count"])
            session.failure_reason = kwargs.get("failure_reason")
            session.updated_at = time.time()
            return session


class RedisTelegramSyncSessionStore(TelegramSyncSessionStore):
    def __init__(
        self,
        *,
        redis_client: Any,
        ttl_seconds: int = TELEGRAM_SYNC_SESSION_TTL_SECONDS,
        max_selected_pages: int = TELEGRAM_SYNC_MAX_SELECTED_PAGES,
    ) -> None:
        self._redis = redis_client
        self._ttl_seconds = ttl_seconds
        self._max_selected_pages = max_selected_pages

    def _locked(self, key: str):
        return self._redis.lock(f"{key}:lock", timeout=10, blocking_timeout=5)

    def get_session(self, **kwargs):
        raw = self._redis.get(_sync_key(kwargs["chat_id"], kwargs["user_id"], kwargs["session_id"]))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return _session_from_json(str(raw))

    def create_session(self, **kwargs):
        session = TelegramSyncSession(
            session_id=kwargs["session_id"],
            chat_id=kwargs["chat_id"],
            user_id=kwargs["user_id"],
            pages=list(kwargs["pages"]),
        )
        self._redis.setex(
            _sync_key(session.chat_id, session.user_id, session.session_id),
            self._ttl_seconds,
            _session_to_json(session),
        )
        return session

    def toggle_page(self, **kwargs):
        key = _sync_key(kwargs["chat_id"], kwargs["user_id"], kwargs["session_id"])
        with self._locked(key):
            session = self.get_session(**kwargs)
            if session is None:
                return "missing", None
            page_id = str(kwargs["page_id"]).strip()
            if session.state != "selecting" or not any(page.page_id == page_id for page in session.pages):
                return "invalid", session
            if page_id in session.selected_page_ids:
                session.selected_page_ids.remove(page_id)
                status = "deselected"
            elif len(session.selected_page_ids) >= self._max_selected_pages:
                return "limit", session
            else:
                session.selected_page_ids.append(page_id)
                status = "selected"
            session.updated_at = time.time()
            self._redis.setex(key, self._ttl_seconds, _session_to_json(session))
            return status, session

    def claim_confirm(self, **kwargs):
        key = _sync_key(kwargs["chat_id"], kwargs["user_id"], kwargs["session_id"])
        with self._locked(key):
            session = self.get_session(**kwargs)
            if session is None:
                return "missing", None
            if session.state != "selecting":
                return "already", session
            if not session.selected_page_ids:
                return "empty", session
            session.state = "processing"
            session.updated_at = time.time()
            self._redis.setex(key, self._ttl_seconds, _session_to_json(session))
            return "claimed", session

    def cancel(self, **kwargs):
        key = _sync_key(kwargs["chat_id"], kwargs["user_id"], kwargs["session_id"])
        with self._locked(key):
            session = self.get_session(**kwargs)
            if session is None:
                return None
            if session.state == "selecting":
                session.state = "cancelled"
                session.updated_at = time.time()
                self._redis.setex(key, self._ttl_seconds, _session_to_json(session))
            return session

    def complete(self, **kwargs):
        key = _sync_key(kwargs["chat_id"], kwargs["user_id"], kwargs["session_id"])
        with self._locked(key):
            session = self.get_session(**kwargs)
            if session is None:
                return None
            session.state = kwargs["state"]
            session.workflow_run_id = int(kwargs["workflow_run_id"])
            session.succeeded_page_count = int(kwargs["succeeded_page_count"])
            session.failed_page_count = int(kwargs["failed_page_count"])
            session.failure_reason = kwargs.get("failure_reason")
            session.updated_at = time.time()
            self._redis.setex(key, self._ttl_seconds, _session_to_json(session))
            return session


def new_telegram_sync_session_id() -> str:
    return f"sync-{secrets.token_urlsafe(12)}"
