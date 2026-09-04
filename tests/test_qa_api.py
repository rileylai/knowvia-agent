from __future__ import annotations

import json

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.app.dependencies import get_provider_router
from src.app.main import app
from src.db.base import Base
from src.db.models import KnowledgeChunk, NotionBlock, NotionPage, WorkflowRun
from src.db.session import get_db_session, get_db_session_factory
from src.providers import (
    LLMClientError,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ProviderRouter,
)


class FakeProvider(LLMProvider):
    @property
    def name(self) -> str:
        return "openai"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        _ = request
        return LLMResponse(
            provider="openai",
            model="gpt-4o-mini",
            output_text="Attention aligns query and key to weight values.",
            token_input=25,
            token_output=10,
        )


class FailingProvider(LLMProvider):
    @property
    def name(self) -> str:
        return "openai"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        _ = request
        raise LLMClientError("upstream timeout")


def _build_session_factory():
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


def _seed_chunks(session: Session) -> None:
    page = NotionPage(
        id=1,
        notion_page_id="page-nlp-week5",
        title="NLP Week 5",
        notion_path="Knowledge/NLP/Week5",
    )
    session.add(page)
    session.flush()

    block_attention = NotionBlock(
        id=1,
        notion_block_id="blk-attention",
        notion_page_id=page.id,
        parent_block_id=None,
        block_type="paragraph",
        content_text="Attention uses query key value vectors",
        block_path="Knowledge/NLP/Week5/Attention",
        block_order=0,
    )
    block_dropout = NotionBlock(
        id=2,
        notion_block_id="blk-dropout",
        notion_page_id=page.id,
        parent_block_id=None,
        block_type="paragraph",
        content_text="Dropout regularization",
        block_path="Knowledge/NLP/Week5/Dropout",
        block_order=1,
    )
    session.add_all([block_attention, block_dropout])
    session.flush()

    session.add_all(
        [
            KnowledgeChunk(
                id=1,
                source_document_id=None,
                notion_block_id=block_attention.id,
                chunk_index=0,
                chunk_text="Attention uses query key value vectors",
                notion_path="Knowledge/NLP/Week5/Attention",
                embedding_text=None,
                source_kind="notion",
            ),
            KnowledgeChunk(
                id=2,
                source_document_id=None,
                notion_block_id=block_dropout.id,
                chunk_index=1,
                chunk_text="Dropout regularization",
                notion_path="Knowledge/NLP/Week5/Dropout",
                embedding_text=None,
                source_kind="notion",
            ),
        ]
    )
    session.commit()


def test_qa_api_returns_grounded_answer_with_citations() -> None:
    session_factory = _build_session_factory()
    seed_session = session_factory()
    try:
        _seed_chunks(seed_session)
    finally:
        seed_session.close()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _provider_router_override() -> ProviderRouter:
        router = ProviderRouter()
        router.register_provider(FakeProvider())
        return router

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_provider_router] = _provider_router_override

    try:
        client = TestClient(app)
        response = client.post(
            "/api/qa",
            json={
                "query": "Explain attention in week5 notes",
                "top_k": 3,
                "provider_name": "openai",
                "model": "gpt-4o-mini",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "succeeded"
        assert payload["insufficient_info"] is False
        assert payload["answer"] == "Attention aligns query and key to weight values."
        assert payload["retrieved_chunk_count"] >= 1
        assert len(payload["citations"]) >= 1
        assert payload["citations"][0]["notion_path"].startswith("Knowledge/NLP/Week5")
        assert payload["provider"] == "openai"
        assert payload["model"] == "gpt-4o-mini"

        verify_session: Session = session_factory()
        try:
            workflow_run = verify_session.get(WorkflowRun, payload["workflow_run_id"])
            assert workflow_run is not None
            assert workflow_run.workflow_type == "qa"
            assert workflow_run.status == "succeeded"
            assert workflow_run.failure_reason is None
            metadata = json.loads(workflow_run.metadata_json or "{}")
            assert metadata["provider_name"] == "openai"
            assert metadata["model"] == "gpt-4o-mini"
            assert metadata["prompt_id"] == "qa_answer"
            assert metadata["prompt_version"] == "qa_answer_v2"
            assert metadata["retrieval_mode"] == "lexical_fallback"
            assert (
                metadata["retrieval_fallback_reason"]
                == "EMBEDDING_PROVIDER_NOT_CONFIGURED"
            )
            assert metadata["estimated_cost"] == pytest.approx(0.00000975)
        finally:
            verify_session.close()
    finally:
        app.dependency_overrides.clear()


def test_qa_api_returns_insufficient_info_when_no_retrieval_match() -> None:
    session_factory = _build_session_factory()
    seed_session = session_factory()
    try:
        _seed_chunks(seed_session)
    finally:
        seed_session.close()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _provider_router_override() -> ProviderRouter:
        return ProviderRouter()

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_provider_router] = _provider_router_override

    try:
        client = TestClient(app)
        response = client.post(
            "/api/qa",
            json={
                "query": "How to solve quantum tunneling equations?",
                "top_k": 3,
                "provider_name": "openai",
                "model": "gpt-4o-mini",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "succeeded"
        assert payload["insufficient_info"] is True
        assert payload["citations"] == []
        assert (
            payload["answer"]
            == "I do not have enough information in production notes to answer safely."
        )
        assert payload["provider"] is None
        assert payload["model"] is None

        verify_session: Session = session_factory()
        try:
            workflow_run = verify_session.get(WorkflowRun, payload["workflow_run_id"])
            assert workflow_run is not None
            metadata = json.loads(workflow_run.metadata_json or "{}")
            assert metadata["retrieval_mode"] == "lexical_fallback"
            assert (
                metadata["retrieval_fallback_reason"]
                == "EMBEDDING_PROVIDER_NOT_CONFIGURED"
            )
            assert metadata["insufficient_info"] is True
        finally:
            verify_session.close()
    finally:
        app.dependency_overrides.clear()


def test_qa_api_returns_provider_not_found_when_provider_missing() -> None:
    session_factory = _build_session_factory()
    seed_session = session_factory()
    try:
        _seed_chunks(seed_session)
    finally:
        seed_session.close()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _provider_router_override() -> ProviderRouter:
        return ProviderRouter()

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_provider_router] = _provider_router_override

    try:
        client = TestClient(app)
        response = client.post(
            "/api/qa",
            json={
                "query": "Explain attention in week5 notes",
                "top_k": 3,
                "provider_name": "openai",
                "model": "gpt-4o-mini",
            },
        )

        assert response.status_code == 500
        payload = response.json()["detail"]
        assert payload["error_code"] == "PROVIDER_NOT_FOUND"
        assert payload["failure_reason"] == "PROVIDER_NOT_FOUND"
        assert payload["workflow_run_id"] is not None

        verify_session: Session = session_factory()
        try:
            workflow_run = verify_session.get(WorkflowRun, payload["workflow_run_id"])
            assert workflow_run is not None
            assert workflow_run.workflow_type == "qa"
            assert workflow_run.status == "failed"
            assert workflow_run.failure_reason == "PROVIDER_NOT_FOUND"
            metadata = json.loads(workflow_run.metadata_json or "{}")
            assert metadata["provider_name"] == "openai"
            assert metadata["model"] == "gpt-4o-mini"
            assert metadata["prompt_id"] == "qa_answer"
            assert metadata["prompt_version"] == "qa_answer_v2"
            assert metadata["estimated_cost"] is None
        finally:
            verify_session.close()
    finally:
        app.dependency_overrides.clear()


def test_qa_api_returns_llm_provider_error_when_provider_request_fails() -> None:
    session_factory = _build_session_factory()
    seed_session = session_factory()
    try:
        _seed_chunks(seed_session)
    finally:
        seed_session.close()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _provider_router_override() -> ProviderRouter:
        router = ProviderRouter()
        router.register_provider(FailingProvider())
        return router

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_provider_router] = _provider_router_override

    try:
        client = TestClient(app)
        response = client.post(
            "/api/qa",
            json={
                "query": "Explain attention in week5 notes",
                "top_k": 3,
                "provider_name": "openai",
                "model": "gpt-4o-mini",
            },
        )

        assert response.status_code == 502
        payload = response.json()["detail"]
        assert payload["error_code"] == "LLM_PROVIDER_ERROR"
        assert payload["failure_reason"] == "LLM_PROVIDER_ERROR"
        assert payload["workflow_run_id"] is not None

        verify_session: Session = session_factory()
        try:
            workflow_run = verify_session.get(WorkflowRun, payload["workflow_run_id"])
            assert workflow_run is not None
            assert workflow_run.workflow_type == "qa"
            assert workflow_run.status == "failed"
            assert workflow_run.failure_reason == "LLM_PROVIDER_ERROR"
            metadata = json.loads(workflow_run.metadata_json or "{}")
            assert metadata["provider_name"] == "openai"
            assert metadata["model"] == "gpt-4o-mini"
            assert metadata["prompt_id"] == "qa_answer"
            assert metadata["prompt_version"] == "qa_answer_v2"
            assert metadata["estimated_cost"] is None
        finally:
            verify_session.close()
    finally:
        app.dependency_overrides.clear()
