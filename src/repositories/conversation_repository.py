from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Sequence

from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from src.db.models import ConversationMessage, ConversationSession
from src.conversation_context import build_conversation_title
from src.conversation_citations import (
    ConversationCitation,
    deserialize_conversation_citations,
    deserialize_used_saved_memory,
    serialize_conversation_citations,
)


@dataclass(frozen=True)
class ConversationMessageSnapshot:
    id: int
    session_id: int
    role: str
    content: str
    sequence_number: int
    created_at: datetime
    citations: List[ConversationCitation]
    used_saved_memory: bool = False


@dataclass(frozen=True)
class ConversationSessionSnapshot:
    id: int
    title: Optional[str]
    status: str
    created_at: datetime
    updated_at: datetime
    messages: List[ConversationMessageSnapshot]


class ConversationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _allocate_id(self, model) -> int:
        max_id = int(self._session.query(func.max(model.id)).scalar() or 0)
        for instance in self._session.identity_map.values():
            if isinstance(instance, model) and instance.id is not None:
                max_id = max(max_id, int(instance.id))
        return max_id + 1

    def create_session(self, *, owner_id: str) -> ConversationSession:
        normalized_owner_id = owner_id.strip()
        if not normalized_owner_id:
            raise ValueError("owner_id must not be empty")

        conversation_session = ConversationSession(
            owner_id=normalized_owner_id,
            status="active",
        )
        if self._is_sqlite:
            conversation_session.id = self._allocate_id(ConversationSession)
        self._session.add(conversation_session)
        self._session.flush()
        self._session.refresh(conversation_session)
        return conversation_session

    def list_sessions(self, *, owner_id: str) -> List[ConversationSessionSnapshot]:
        sessions = (
            self._session.query(ConversationSession)
            .filter(ConversationSession.owner_id == owner_id)
            .order_by(
                ConversationSession.updated_at.desc(),
                ConversationSession.id.desc(),
            )
            .all()
        )
        return [self._snapshot(session, messages=[]) for session in sessions]

    def get_session(
        self,
        *,
        session_id: int,
        owner_id: str,
        include_messages: bool = True,
    ) -> Optional[ConversationSessionSnapshot]:
        conversation_session = (
            self._session.query(ConversationSession)
            .filter(
                ConversationSession.id == session_id,
                ConversationSession.owner_id == owner_id,
            )
            .one_or_none()
        )
        if conversation_session is None:
            return None
        messages = self.list_messages(
            session_id=session_id,
            owner_id=owner_id,
        ) if include_messages else []
        return self._snapshot(conversation_session, messages=messages)

    def list_messages(
        self,
        *,
        session_id: int,
        owner_id: str,
        limit: Optional[int] = None,
    ) -> List[ConversationMessageSnapshot]:
        if not self._owns_session(session_id=session_id, owner_id=owner_id):
            return []
        query = self._session.query(ConversationMessage).filter(
            ConversationMessage.session_id == session_id
        )
        if limit is not None:
            if limit <= 0:
                return []
            rows = list(
                reversed(
                    query.order_by(desc(ConversationMessage.sequence_number))
                    .limit(limit)
                    .all()
                )
            )
        else:
            rows = query.order_by(ConversationMessage.sequence_number.asc()).all()
        return [self._message_snapshot(message) for message in rows]

    def append_message(
        self,
        *,
        session_id: int,
        owner_id: str,
        role: str,
        content: str,
        citations: Optional[Sequence[ConversationCitation]] = None,
        used_saved_memory: bool = False,
    ) -> ConversationMessage:
        conversation_session = (
            self._session.query(ConversationSession)
            .filter(
                ConversationSession.id == session_id,
                ConversationSession.owner_id == owner_id,
            )
            .one_or_none()
        )
        if conversation_session is None:
            raise ValueError("conversation session is unavailable")

        normalized_role = role.strip().lower()
        if normalized_role not in {"user", "assistant"}:
            raise ValueError("role must be user or assistant")
        if normalized_role != "assistant" and citations:
            raise ValueError("citations are only supported for assistant messages")
        normalized_content = content.strip()
        if not normalized_content:
            raise ValueError("content must not be empty")

        max_sequence = int(
            self._session.query(func.max(ConversationMessage.sequence_number))
            .filter(ConversationMessage.session_id == session_id)
            .scalar()
            or 0
        )
        message = ConversationMessage(
            session_id=session_id,
            role=normalized_role,
            content=normalized_content,
            sequence_number=max_sequence + 1,
            metadata_json=None,
        )
        if normalized_role == "assistant":
            message.metadata_json = serialize_conversation_citations(
                citations or [],
                used_saved_memory=used_saved_memory,
            )
        if self._is_sqlite:
            message.id = self._allocate_id(ConversationMessage)
        self._session.add(message)

        if normalized_role == "user" and conversation_session.title is None:
            conversation_session.title = build_conversation_title(normalized_content)
        conversation_session.updated_at = datetime.now(timezone.utc)
        self._session.flush()
        self._session.refresh(message)
        self._session.refresh(conversation_session)
        return message

    @property
    def _is_sqlite(self) -> bool:
        return (
            self._session.bind is not None
            and self._session.bind.dialect.name == "sqlite"
        )

    def _owns_session(self, *, session_id: int, owner_id: str) -> bool:
        return (
            self._session.query(ConversationSession.id)
            .filter(
                ConversationSession.id == session_id,
                ConversationSession.owner_id == owner_id,
            )
            .first()
            is not None
        )

    def _snapshot(
        self,
        conversation_session: ConversationSession,
        *,
        messages: List[ConversationMessageSnapshot],
    ) -> ConversationSessionSnapshot:
        return ConversationSessionSnapshot(
            id=int(conversation_session.id),
            title=conversation_session.title,
            status=conversation_session.status,
            created_at=conversation_session.created_at,
            updated_at=conversation_session.updated_at,
            messages=messages,
        )

    def _message_snapshot(
        self,
        message: ConversationMessage,
    ) -> ConversationMessageSnapshot:
        return ConversationMessageSnapshot(
            id=int(message.id),
            session_id=int(message.session_id),
            role=message.role,
            content=message.content,
            sequence_number=int(message.sequence_number),
            created_at=message.created_at,
            citations=deserialize_conversation_citations(message.metadata_json),
            used_saved_memory=deserialize_used_saved_memory(message.metadata_json),
        )
