from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.app.dependencies import get_current_owner_id, get_embedding_client
from src.app.main import app
from src.db.base import Base
from src.db.models import ConversationMessage, ConversationSession, LongTermMemory
from src.db.session import get_db_session_factory
from src.providers import EmbeddingClient, EmbeddingRequest, EmbeddingResponse


class FakeEmbeddingClient(EmbeddingClient):
    @property
    def name(self) -> str:
        return "fake"

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        return EmbeddingResponse(
            provider=self.name,
            model=request.model or "fake-model",
            embeddings=[[1.0] + [0.0] * 1535 for _ in request.inputs],
        )


def _build_session_factory():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[ConversationSession.__table__, ConversationMessage.__table__, LongTermMemory.__table__],
    )
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def test_memory_api_is_owner_scoped_and_supports_refresh_and_delete() -> None:
    session_factory = _build_session_factory()
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_embedding_client] = lambda: FakeEmbeddingClient()
    app.dependency_overrides[get_current_owner_id] = lambda: "owner-a"

    try:
        client = TestClient(app)
        save_response = client.post(
            "/api/memories",
            json={"content": "Use pgvector in production.", "memory_type": "decision"},
        )
        assert save_response.status_code == 201
        saved = save_response.json()
        assert saved["status"] == "saved"
        memory_id = saved["memory"]["id"]

        listed = client.get("/api/memories")
        assert listed.status_code == 200
        assert [memory["id"] for memory in listed.json()] == [memory_id]

        refreshed_client = TestClient(app)
        assert [memory["id"] for memory in refreshed_client.get("/api/memories").json()] == [memory_id]

        deleted = refreshed_client.delete(f"/api/memories/{memory_id}")
        assert deleted.status_code == 204
        assert refreshed_client.get("/api/memories").json() == []

        app.dependency_overrides[get_current_owner_id] = lambda: "owner-b"
        assert client.delete(f"/api/memories/{memory_id}").status_code == 404
    finally:
        app.dependency_overrides.clear()
