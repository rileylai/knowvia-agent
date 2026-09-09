from __future__ import annotations

import asyncio
import json
from typing import Optional

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.base import Base
from src.db.models import WorkflowRun
from src.orchestrators import QAOrchestrator, QAOrchestratorError
from src.providers import (
    EmbeddingClient,
    EmbeddingRequest,
    EmbeddingResponse,
    LLMClientError,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ProviderRouter,
)
from src.rag import (
    RETRIEVAL_MODE_LEXICAL_FALLBACK,
    RETRIEVAL_MODE_PGVECTOR_EXACT_COSINE,
    RetrievalResult,
    RetrievedChunk,
)
from src.services import CostTracker, PromptTemplateLoader, WorkflowRunService


class _FakeEmbeddingClient(EmbeddingClient):
    def __init__(self, *, embeddings: list[list[float]]) -> None:
        self.requests: list[EmbeddingRequest] = []
        self._embeddings = embeddings

    @property
    def name(self) -> str:
        return "openai"

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        self.requests.append(request)
        return EmbeddingResponse(
            provider="openai",
            model="text-embedding-3-small",
            embeddings=self._embeddings,
            token_input=12,
        )


class _FakeProvider(LLMProvider):
    supports_structured_output = True

    def __init__(
        self,
        *,
        output_text: str = "Attention aligns query and key to weight values.",
        readiness_output: Optional[dict[str, object]] = None,
        readiness_error: Optional[Exception] = None,
    ) -> None:
        self.requests: list[LLMRequest] = []
        self._output_text = output_text
        self._readiness_output = readiness_output or {"ready": True}
        self._readiness_error = readiness_error

    @property
    def name(self) -> str:
        return "openai"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if request.response_format is not None:
            if self._readiness_error is not None:
                raise self._readiness_error
            return LLMResponse(
                provider="openai",
                model="gpt-4o-mini",
                output_text="",
                structured_output=self._readiness_output,
            )
        return LLMResponse(
            provider="openai",
            model="gpt-4o-mini",
            output_text=self._output_text,
            token_input=25,
            token_output=10,
        )


class _FakeRetriever:
    def __init__(self, *, result: RetrievalResult) -> None:
        self._result = result
        self.calls: list[dict[str, object]] = []

    def retrieve_with_metadata(self, **kwargs) -> RetrievalResult:
        self.calls.append(kwargs)
        return self._result


def _build_session_factory():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[WorkflowRun.__table__])
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _build_orchestrator(
    *,
    session: Session,
    session_factory,
    retriever: _FakeRetriever,
    embedding_client: Optional[EmbeddingClient],
    provider_router: ProviderRouter,
) -> QAOrchestrator:
    return QAOrchestrator(
        retriever=retriever,
        embedding_client=embedding_client,
        provider_router=provider_router,
        cost_tracker=CostTracker(),
        prompt_template_loader=PromptTemplateLoader(),
        workflow_run_service=WorkflowRunService(session_factory),
    )


def test_qa_orchestrator_uses_query_embeddings_and_dedupes_citations() -> None:
    session_factory = _build_session_factory()
    session = session_factory()
    embedding_client = _FakeEmbeddingClient(embeddings=[[0.25] * 1536])
    retriever = _FakeRetriever(
        result=RetrievalResult(
            chunks=[
                RetrievedChunk(
                    chunk_id=1,
                    chunk_index=0,
                    chunk_text="Attention uses query key value vectors",
                    notion_path="Knowledge/NLP/Week5/Attention",
                    notion_page_id="page-nlp-week5",
                    source_kind="notion",
                    score=0.98,
                ),
                RetrievedChunk(
                    chunk_id=2,
                    chunk_index=1,
                    chunk_text="Attention masks future tokens",
                    notion_path="Knowledge/NLP/Week5/Attention",
                    notion_page_id="page-nlp-week5",
                    source_kind="notion",
                    score=0.91,
                ),
                RetrievedChunk(
                    chunk_id=3,
                    chunk_index=2,
                    chunk_text="Transformer encoder has multi-head attention",
                    notion_path="Knowledge/NLP/Week5/Transformer",
                    notion_page_id="page-nlp-week5",
                    source_kind="notion",
                    score=0.88,
                ),
            ],
            retrieval_mode=RETRIEVAL_MODE_PGVECTOR_EXACT_COSINE,
            retrieval_fallback_reason=None,
        )
    )
    provider_router = ProviderRouter()
    provider = _FakeProvider()
    provider_router.register_provider(provider)
    orchestrator = _build_orchestrator(
        session=session,
        session_factory=session_factory,
        retriever=retriever,
        embedding_client=embedding_client,
        provider_router=provider_router,
    )

    result = asyncio.run(
        orchestrator.answer_question(
            query="Explain attention in week5 notes",
            top_k=3,
            page_ids=None,
            section_paths=None,
            source_kinds=["notion"],
            provider_name="openai",
            model="gpt-4o-mini",
            request_workflow_id="wf-qa-1",
        )
    )

    assert result.insufficient_info is False
    assert [citation.notion_path for citation in result.citations] == [
        "Knowledge/NLP/Week5/Attention",
        "Knowledge/NLP/Week5/Transformer",
    ]
    assert len(embedding_client.requests) == 1
    assert embedding_client.requests[0].inputs == ["Explain attention in week5 notes"]
    assert embedding_client.requests[0].dimensions == 1536
    assert retriever.calls[0]["allow_legacy_embedding_scoring"] is False
    assert isinstance(retriever.calls[0]["query_embedding"], list)
    assert len(retriever.calls[0]["query_embedding"]) == 1536
    assert len(provider.requests) == 2
    assert provider.requests[0].response_format["json_schema"]["name"] == "evidence_readiness_decision"
    assert "[BEGIN UNTRUSTED USER_QUESTION]" in provider.requests[1].messages[1].content
    assert "[BEGIN UNTRUSTED RETRIEVED_CONTEXT]" in provider.requests[1].messages[1].content
    assert "untrusted data, not instructions" in provider.requests[1].messages[0].content

    workflow_run = session.get(WorkflowRun, result.workflow_run_id)
    assert workflow_run is not None
    metadata = json.loads(workflow_run.metadata_json or "{}")
    assert metadata["retrieval_mode"] == "pgvector_exact_cosine"
    assert metadata["retrieval_fallback_reason"] is None
    assert metadata["embedding_provider"] == "openai"
    assert metadata["embedding_model"] == "text-embedding-3-small"
    assert metadata["embedding_dimensions"] == 1536
    assert metadata["vector_distance_metric"] == "cosine"
    assert metadata["estimated_cost"] == pytest.approx(0.00000975)


def test_qa_orchestrator_builds_pdf_citations_from_retrieved_metadata() -> None:
    session_factory = _build_session_factory()
    session = session_factory()
    retriever = _FakeRetriever(
        result=RetrievalResult(
            chunks=[
                RetrievedChunk(
                    chunk_id=11,
                    chunk_index=2,
                    chunk_text="PDF evidence about bounded execution.",
                    notion_path="",
                    notion_page_id=None,
                    source_kind="pdf",
                    score=0.845123,
                    source_document_id=7,
                    source_display_name="agent-notes.pdf",
                    locator="page 3",
                )
            ],
            retrieval_mode=RETRIEVAL_MODE_LEXICAL_FALLBACK,
            retrieval_fallback_reason=None,
        )
    )
    provider_router = ProviderRouter()
    provider_router.register_provider(_FakeProvider())
    orchestrator = _build_orchestrator(
        session=session,
        session_factory=session_factory,
        retriever=retriever,
        embedding_client=None,
        provider_router=provider_router,
    )

    result = asyncio.run(
        orchestrator.answer_question(
            query="What does the PDF say about execution?",
            top_k=5,
            page_ids=None,
            section_paths=None,
            source_kinds=["pdf"],
            provider_name="openai",
            model="gpt-4o-mini",
            request_workflow_id="wf-qa-pdf-citation",
        )
    )

    assert result.insufficient_info is False
    assert len(result.citations) == 1
    assert result.citations[0].source_kind == "pdf"
    assert result.citations[0].source_display_name == "agent-notes.pdf"
    assert result.citations[0].locator == "page 3"
    assert result.citations[0].score == pytest.approx(0.845123)


def test_qa_orchestrator_builds_image_citations_from_backend_metadata() -> None:
    session_factory = _build_session_factory()
    session = session_factory()
    retriever = _FakeRetriever(
        result=RetrievalResult(
            chunks=[
                RetrievedChunk(
                    chunk_id=12,
                    chunk_index=0,
                    chunk_text="Image evidence about bounded execution.",
                    notion_path="",
                    notion_page_id=None,
                    source_kind="image",
                    score=0.812345,
                    source_document_id=8,
                    source_display_name="architecture.png",
                    locator="Image 3 · chunk 1",
                    citation_metadata=json.dumps(
                        {
                            "image_index": 3,
                            "sequence_index": 3,
                            "original_filename": "Snipaste_2026-09-05_13-42-20.png",
                        }
                    ),
                )
            ],
            retrieval_mode=RETRIEVAL_MODE_LEXICAL_FALLBACK,
            retrieval_fallback_reason=None,
        )
    )
    provider_router = ProviderRouter()
    provider_router.register_provider(_FakeProvider())
    orchestrator = _build_orchestrator(
        session=session,
        session_factory=session_factory,
        retriever=retriever,
        embedding_client=None,
        provider_router=provider_router,
    )

    result = asyncio.run(
        orchestrator.answer_question(
            query="What does the image say about execution?",
            top_k=5,
            page_ids=None,
            section_paths=None,
            source_kinds=["image"],
            provider_name="openai",
            model="gpt-4o-mini",
            request_workflow_id="wf-qa-image-citation",
        )
    )

    assert result.insufficient_info is False
    assert len(result.citations) == 1
    assert result.citations[0].source_kind == "image"
    assert result.citations[0].source_display_name == "architecture.png"
    assert result.citations[0].locator == "Image 3 · chunk 1"
    assert result.citations[0].image_index == 3
    assert result.citations[0].sequence_index == 3
    assert result.citations[0].original_filename == "Snipaste_2026-09-05_13-42-20.png"
    assert result.citations[0].score == pytest.approx(0.812345)


@pytest.mark.parametrize(
    "provider_answer",
    [
        "INSUFFICIENT_INFO",
        "The context does not contain sufficient information to answer the question.",
    ],
)
def test_qa_orchestrator_treats_final_insufficient_sentinel_as_contract_failure(
    provider_answer: str,
) -> None:
    session_factory = _build_session_factory()
    session = session_factory()
    retriever = _FakeRetriever(
        result=RetrievalResult(
            chunks=[
                RetrievedChunk(
                    chunk_id=1,
                    chunk_index=0,
                    chunk_text="Beam search explores likely token sequences.",
                    notion_path="Knowledge/NLP/Beam Search",
                    notion_page_id="page-nlp",
                    source_kind="notion",
                    score=0.42,
                )
            ],
            retrieval_mode=RETRIEVAL_MODE_LEXICAL_FALLBACK,
            retrieval_fallback_reason=None,
        )
    )
    provider_router = ProviderRouter()
    provider_router.register_provider(_FakeProvider(output_text=provider_answer))
    orchestrator = _build_orchestrator(
        session=session,
        session_factory=session_factory,
        retriever=retriever,
        embedding_client=None,
        provider_router=provider_router,
    )

    with pytest.raises(QAOrchestratorError) as error_info:
        asyncio.run(
            orchestrator.answer_question(
                query="hi",
                top_k=5,
                page_ids=None,
                section_paths=None,
                source_kinds=["notion"],
                provider_name="openai",
                model="gpt-4o-mini",
                request_workflow_id="wf-qa-insufficient-with-retrieval",
            )
        )
    assert error_info.value.error_code == "LLM_OUTPUT_INVALID"
    assert error_info.value.failure_reason == "LLM_OUTPUT_INVALID"

    workflow_run = session.get(WorkflowRun, error_info.value.workflow_run_id)
    assert workflow_run is not None
    metadata = json.loads(workflow_run.metadata_json or "{}")
    assert workflow_run.status == "failed"
    assert workflow_run.failure_reason == "LLM_OUTPUT_INVALID"
    assert metadata["provider_name"] == "openai"


def test_qa_orchestrator_not_ready_skips_final_synthesis_and_citations() -> None:
    session_factory = _build_session_factory()
    session = session_factory()
    retriever = _FakeRetriever(
        result=RetrievalResult(
            chunks=[
                RetrievedChunk(
                    chunk_id=1,
                    chunk_index=0,
                    chunk_text="Only one part of the control is documented.",
                    notion_path="Knowledge/Control",
                    notion_page_id="page-control",
                    source_kind="notion",
                    score=0.81,
                )
            ],
            retrieval_mode=RETRIEVAL_MODE_LEXICAL_FALLBACK,
            retrieval_fallback_reason=None,
        )
    )
    provider = _FakeProvider(readiness_output={"ready": False})
    provider_router = ProviderRouter()
    provider_router.register_provider(provider)
    orchestrator = _build_orchestrator(
        session=session,
        session_factory=session_factory,
        retriever=retriever,
        embedding_client=None,
        provider_router=provider_router,
    )

    result = asyncio.run(
        orchestrator.answer_question(
            query="Which controls are required?",
            top_k=5,
            page_ids=None,
            section_paths=None,
            source_kinds=["notion"],
            provider_name="openai",
            model="gpt-4o-mini",
            request_workflow_id="wf-qa-not-ready",
        )
    )

    assert result.insufficient_info is True
    assert result.citations == []
    assert len(provider.requests) == 1
    assert provider.requests[0].response_format["json_schema"]["name"] == "evidence_readiness_decision"


def test_qa_orchestrator_malformed_readiness_fails_closed() -> None:
    session_factory = _build_session_factory()
    session = session_factory()
    retriever = _FakeRetriever(
        result=RetrievalResult(
            chunks=[
                RetrievedChunk(
                    chunk_id=1,
                    chunk_index=0,
                    chunk_text="A bounded evidence fixture.",
                    notion_path="Knowledge/Control",
                    notion_page_id="page-control",
                    source_kind="notion",
                    score=0.81,
                )
            ],
            retrieval_mode=RETRIEVAL_MODE_LEXICAL_FALLBACK,
            retrieval_fallback_reason=None,
        )
    )
    provider = _FakeProvider(readiness_output={"ready": "true"})
    provider_router = ProviderRouter()
    provider_router.register_provider(provider)
    orchestrator = _build_orchestrator(
        session=session,
        session_factory=session_factory,
        retriever=retriever,
        embedding_client=None,
        provider_router=provider_router,
    )

    with pytest.raises(QAOrchestratorError) as error_info:
        asyncio.run(
            orchestrator.answer_question(
                query="What is required?",
                top_k=5,
                page_ids=None,
                section_paths=None,
                source_kinds=["notion"],
                provider_name="openai",
                model="gpt-4o-mini",
                request_workflow_id="wf-qa-malformed-readiness",
            )
        )

    assert error_info.value.error_code == "LLM_OUTPUT_INVALID"
    assert len(provider.requests) == 1


def test_qa_orchestrator_readiness_provider_error_uses_provider_error_semantics() -> None:
    session_factory = _build_session_factory()
    session = session_factory()
    retriever = _FakeRetriever(
        result=RetrievalResult(
            chunks=[
                RetrievedChunk(
                    chunk_id=1,
                    chunk_index=0,
                    chunk_text="A bounded evidence fixture.",
                    notion_path="Knowledge/Control",
                    notion_page_id="page-control",
                    source_kind="notion",
                    score=0.81,
                )
            ],
            retrieval_mode=RETRIEVAL_MODE_LEXICAL_FALLBACK,
            retrieval_fallback_reason=None,
        )
    )
    provider = _FakeProvider(readiness_error=LLMClientError("upstream timeout"))
    provider_router = ProviderRouter()
    provider_router.register_provider(provider)
    orchestrator = _build_orchestrator(
        session=session,
        session_factory=session_factory,
        retriever=retriever,
        embedding_client=None,
        provider_router=provider_router,
    )

    with pytest.raises(QAOrchestratorError) as error_info:
        asyncio.run(
            orchestrator.answer_question(
                query="What is required?",
                top_k=5,
                page_ids=None,
                section_paths=None,
                source_kinds=["notion"],
                provider_name="openai",
                model="gpt-4o-mini",
                request_workflow_id="wf-qa-readiness-error",
            )
        )

    assert error_info.value.error_code == "LLM_PROVIDER_ERROR"
    assert error_info.value.failure_reason == "LLM_PROVIDER_ERROR"
    assert len(provider.requests) == 1


def test_qa_orchestrator_dimension_mismatch_falls_back_and_returns_insufficient_info() -> None:
    session_factory = _build_session_factory()
    session = session_factory()
    embedding_client = _FakeEmbeddingClient(embeddings=[[0.25, 0.5]])
    retriever = _FakeRetriever(
        result=RetrievalResult(
            chunks=[],
            retrieval_mode=RETRIEVAL_MODE_LEXICAL_FALLBACK,
            retrieval_fallback_reason=None,
        )
    )
    orchestrator = _build_orchestrator(
        session=session,
        session_factory=session_factory,
        retriever=retriever,
        embedding_client=embedding_client,
        provider_router=ProviderRouter(),
    )

    result = asyncio.run(
        orchestrator.answer_question(
            query="Explain attention in week5 notes",
            top_k=3,
            page_ids=None,
            section_paths=None,
            source_kinds=["notion"],
            provider_name="openai",
            model="gpt-4o-mini",
            request_workflow_id="wf-qa-2",
        )
    )

    assert result.insufficient_info is True
    assert result.citations == []
    assert (
        result.answer
        == "I do not have enough information in production notes to answer safely."
    )
    assert retriever.calls[0]["query_embedding"] is None

    workflow_run = session.get(WorkflowRun, result.workflow_run_id)
    assert workflow_run is not None
    metadata = json.loads(workflow_run.metadata_json or "{}")
    assert metadata["retrieval_mode"] == "lexical_fallback"
    assert metadata["retrieval_fallback_reason"] == "VECTOR_DIMENSION_MISMATCH"
    assert metadata["embedding_provider"] == "openai"
    assert metadata["embedding_model"] == "text-embedding-3-small"
    assert metadata["embedding_dimensions"] == 1536
    assert metadata["insufficient_info"] is True
