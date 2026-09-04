import logging
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Generator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.app.dependencies import get_embedding_client, get_provider_router
from src.app.main import app
from src.db.base import Base
from src.db.models import KnowledgeChunk, NotionBlock, NotionPage, WorkflowRun
from src.db.session import get_db_session, get_db_session_factory, get_unit_of_work_factory
from src.db.unit_of_work import SqlAlchemyUnitOfWork
from src.providers import (
    EmbeddingClient,
    EmbeddingRequest,
    EmbeddingResponse,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ProviderRouter,
    get_openai_embedding_capabilities,
)


class _FakeProvider(LLMProvider):
    @property
    def name(self) -> str:
        return "openai"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        _ = request
        return LLMResponse(
            provider="openai",
            model="gpt-4o-mini",
            output_text=(
                "Positional encoding gives the model an order signal for tokens "
                "because self-attention alone does not preserve sequence position."
            ),
            token_input=32,
            token_output=21,
        )


class _FakeEmbeddingClient(EmbeddingClient):
    @property
    def name(self) -> str:
        return "openai"

    def get_capabilities(self, *, model: str, dimensions: int):
        return get_openai_embedding_capabilities(
            model=model,
            dimensions=dimensions,
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        embeddings = [
            [float(index + 1)] * 1536
            for index, _ in enumerate(request.inputs)
        ]
        return EmbeddingResponse(
            provider="openai",
            model="text-embedding-3-small",
            embeddings=embeddings,
            indices=list(range(len(request.inputs))),
            token_input=len(request.inputs) * 10,
        )


@dataclass
class DemoSummary:
    health_status: str
    indexed_page_id: str
    indexed_page_title: str
    indexed_block_count: int
    qa_answer: str
    qa_citation_path: str
    qa_provider: str
    qa_model: str


def _build_session_factory() -> sessionmaker:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            NotionPage.__table__,
            NotionBlock.__table__,
            KnowledgeChunk.__table__,
            WorkflowRun.__table__,
        ],
    )
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _db_override(session_factory: sessionmaker) -> Generator[Session, None, None]:
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


def _provider_router_override() -> ProviderRouter:
    router = ProviderRouter()
    router.register_provider(_FakeProvider())
    return router


def _embedding_client_override() -> EmbeddingClient:
    return _FakeEmbeddingClient()


def run_demo() -> DemoSummary:
    session_factory = _build_session_factory()
    logging.getLogger("learnloop.request").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    def _dependency_db_override() -> Generator[Session, None, None]:
        yield from _db_override(session_factory)

    app.dependency_overrides[get_db_session] = _dependency_db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_provider_router] = _provider_router_override
    app.dependency_overrides[get_embedding_client] = _embedding_client_override

    try:
        client = TestClient(app)

        health_response = client.get("/health")
        health_response.raise_for_status()

        index_response = client.post(
            "/api/notion/index/page",
            json={"page_id": "page-nlp-week5"},
        )
        index_response.raise_for_status()

        qa_response = client.post(
            "/api/qa",
            json={
                "query": "What does positional encoding do?",
                "page_ids": ["page-nlp-week5"],
                "top_k": 5,
                "provider_name": "openai",
                "model": "gpt-4o-mini",
            },
        )
        qa_response.raise_for_status()

        health_payload = health_response.json()
        index_payload = index_response.json()
        qa_payload = qa_response.json()
        citations = qa_payload.get("citations") or []
        if not citations:
            raise RuntimeError("demo QA response did not include citations")

        return DemoSummary(
            health_status=health_payload["status"],
            indexed_page_id=index_payload["page_id"],
            indexed_page_title=index_payload["page_title"],
            indexed_block_count=index_payload["indexed_block_count"],
            qa_answer=qa_payload["answer"],
            qa_citation_path=citations[0]["notion_path"],
            qa_provider=qa_payload["provider"],
            qa_model=qa_payload["model"],
        )
    finally:
        app.dependency_overrides.clear()


def main() -> int:
    summary = run_demo()
    print("LearnLoop mock demo: pass")
    print(f"health={summary.health_status}")
    print(
        "indexed_page="
        f"{summary.indexed_page_id} ({summary.indexed_page_title}), "
        f"blocks={summary.indexed_block_count}"
    )
    print(f"qa_provider={summary.qa_provider} model={summary.qa_model}")
    print(f"qa_citation={summary.qa_citation_path}")
    print(f"qa_answer={summary.qa_answer}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
