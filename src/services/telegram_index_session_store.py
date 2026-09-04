from __future__ import annotations

import json
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


TELEGRAM_INDEX_SESSION_TTL_SECONDS = 600


@dataclass
class TelegramFullIndexSession:
    session_id: str
    chat_id: str
    user_id: str
    state: str = "warning"
    workflow_run_id: Optional[int] = None
    discovered_page_count: int = 0
    processed_page_count: int = 0
    failed_page_count: int = 0
    remaining_page_count: int = 0
    failure_reason: Optional[str] = None
    updated_at: float = field(default_factory=time.time)


class TelegramIndexSessionStore(ABC):
    @abstractmethod
    def create_full_index_session(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
    ) -> TelegramFullIndexSession:
        raise NotImplementedError

    @abstractmethod
    def get_full_index_session(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
    ) -> Optional[TelegramFullIndexSession]:
        raise NotImplementedError

    @abstractmethod
    def claim_full_index(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
    ) -> tuple[str, Optional[TelegramFullIndexSession]]:
        raise NotImplementedError

    @abstractmethod
    def cancel_full_index(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
    ) -> Optional[TelegramFullIndexSession]:
        raise NotImplementedError

    @abstractmethod
    def mark_full_index_queued(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
        workflow_run_id: int,
    ) -> Optional[TelegramFullIndexSession]:
        raise NotImplementedError

    @abstractmethod
    def complete_full_index(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
        state: str,
        workflow_run_id: Optional[int],
        discovered_page_count: int,
        processed_page_count: int,
        failed_page_count: int,
        remaining_page_count: int,
        failure_reason: Optional[str] = None,
    ) -> Optional[TelegramFullIndexSession]:
        raise NotImplementedError


def _session_key(chat_id: str, user_id: str, session_id: str) -> str:
    return f"learnloop:telegram:index:{chat_id}:{user_id}:{session_id}"


def _session_to_json(session: TelegramFullIndexSession) -> str:
    return json.dumps(asdict(session), sort_keys=True)


def _session_from_json(raw: str) -> TelegramFullIndexSession:
    return TelegramFullIndexSession(**json.loads(raw))


class InMemoryTelegramIndexSessionStore(TelegramIndexSessionStore):
    def __init__(
        self,
        *,
        ttl_seconds: int = TELEGRAM_INDEX_SESSION_TTL_SECONDS,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._sessions: dict[tuple[str, str, str], TelegramFullIndexSession] = {}
        self._lock = threading.RLock()

    def _get_unlocked(self, **kwargs) -> Optional[TelegramFullIndexSession]:
        key = (kwargs["chat_id"], kwargs["user_id"], kwargs["session_id"])
        session = self._sessions.get(key)
        if session is None:
            return None
        if session.updated_at + self._ttl_seconds <= time.time():
            self._sessions.pop(key, None)
            return None
        return session

    def create_full_index_session(self, **kwargs):
        with self._lock:
            session = TelegramFullIndexSession(
                session_id=kwargs["session_id"],
                chat_id=kwargs["chat_id"],
                user_id=kwargs["user_id"],
            )
            self._sessions[(session.chat_id, session.user_id, session.session_id)] = session
            return session

    def get_full_index_session(self, **kwargs):
        with self._lock:
            return self._get_unlocked(**kwargs)

    def claim_full_index(self, **kwargs):
        with self._lock:
            session = self._get_unlocked(**kwargs)
            if session is None:
                return "missing", None
            if session.state != "warning":
                return "already", session
            session.state = "processing"
            session.updated_at = time.time()
            return "claimed", session

    def cancel_full_index(self, **kwargs):
        with self._lock:
            session = self._get_unlocked(**kwargs)
            if session is None:
                return None
            if session.state == "warning":
                session.state = "cancelled"
                session.updated_at = time.time()
            return session

    def mark_full_index_queued(self, **kwargs):
        with self._lock:
            session = self._get_unlocked(**kwargs)
            if session is None:
                return None
            session.state = "running"
            session.workflow_run_id = int(kwargs["workflow_run_id"])
            session.updated_at = time.time()
            return session

    def complete_full_index(self, **kwargs):
        with self._lock:
            session = self._get_unlocked(**kwargs)
            if session is None:
                return None
            session.state = kwargs["state"]
            session.workflow_run_id = kwargs.get("workflow_run_id")
            session.discovered_page_count = int(kwargs["discovered_page_count"])
            session.processed_page_count = int(kwargs["processed_page_count"])
            session.failed_page_count = int(kwargs["failed_page_count"])
            session.remaining_page_count = int(kwargs["remaining_page_count"])
            session.failure_reason = kwargs.get("failure_reason")
            session.updated_at = time.time()
            return session


class RedisTelegramIndexSessionStore(TelegramIndexSessionStore):
    def __init__(
        self,
        *,
        redis_client: Any,
        ttl_seconds: int = TELEGRAM_INDEX_SESSION_TTL_SECONDS,
    ) -> None:
        self._redis = redis_client
        self._ttl_seconds = ttl_seconds

    def _locked(self, key: str):
        return self._redis.lock(f"{key}:lock", timeout=10, blocking_timeout=5)

    def get_full_index_session(self, **kwargs):
        raw = self._redis.get(
            _session_key(kwargs["chat_id"], kwargs["user_id"], kwargs["session_id"])
        )
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return _session_from_json(str(raw))

    def create_full_index_session(self, **kwargs):
        session = TelegramFullIndexSession(
            session_id=kwargs["session_id"],
            chat_id=kwargs["chat_id"],
            user_id=kwargs["user_id"],
        )
        self._redis.setex(
            _session_key(session.chat_id, session.user_id, session.session_id),
            self._ttl_seconds,
            _session_to_json(session),
        )
        return session

    def claim_full_index(self, **kwargs):
        key = _session_key(kwargs["chat_id"], kwargs["user_id"], kwargs["session_id"])
        with self._locked(key):
            session = self.get_full_index_session(**kwargs)
            if session is None:
                return "missing", None
            if session.state != "warning":
                return "already", session
            session.state = "processing"
            session.updated_at = time.time()
            self._redis.setex(key, self._ttl_seconds, _session_to_json(session))
            return "claimed", session

    def cancel_full_index(self, **kwargs):
        key = _session_key(kwargs["chat_id"], kwargs["user_id"], kwargs["session_id"])
        with self._locked(key):
            session = self.get_full_index_session(**kwargs)
            if session is None:
                return None
            if session.state == "warning":
                session.state = "cancelled"
                session.updated_at = time.time()
                self._redis.setex(key, self._ttl_seconds, _session_to_json(session))
            return session

    def mark_full_index_queued(self, **kwargs):
        key = _session_key(kwargs["chat_id"], kwargs["user_id"], kwargs["session_id"])
        with self._locked(key):
            session = self.get_full_index_session(**kwargs)
            if session is None:
                return None
            session.state = "running"
            session.workflow_run_id = int(kwargs["workflow_run_id"])
            session.updated_at = time.time()
            self._redis.setex(key, self._ttl_seconds, _session_to_json(session))
            return session

    def complete_full_index(self, **kwargs):
        key = _session_key(kwargs["chat_id"], kwargs["user_id"], kwargs["session_id"])
        with self._locked(key):
            session = self.get_full_index_session(**kwargs)
            if session is None:
                return None
            session.state = kwargs["state"]
            session.workflow_run_id = kwargs.get("workflow_run_id")
            session.discovered_page_count = int(kwargs["discovered_page_count"])
            session.processed_page_count = int(kwargs["processed_page_count"])
            session.failed_page_count = int(kwargs["failed_page_count"])
            session.remaining_page_count = int(kwargs["remaining_page_count"])
            session.failure_reason = kwargs.get("failure_reason")
            session.updated_at = time.time()
            self._redis.setex(key, self._ttl_seconds, _session_to_json(session))
            return session
