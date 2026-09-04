from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.providers import EmbeddingClient, EmbeddingRequest, EmbeddingResponse
from src.rag import (
    ProductionChunkRetriever,
    RETRIEVAL_MODE_LEXICAL_FALLBACK,
    RETRIEVAL_MODE_PGVECTOR_EXACT_COSINE,
)
from src.repositories import (
    ChunkVectorQueryError,
    RetrievalChunkCandidate,
    SemanticChunkMatch,
)


@dataclass(frozen=True)
class _FixtureChunk:
    chunk_id: int
    chunk_index: int
    chunk_text: str
    notion_path: str
    source_kind: str
    notion_page_id: Optional[str]
    semantic_score: Optional[float] = None
    embedding_text: Optional[str] = None


@dataclass(frozen=True)
class _VectorScenario:
    scenario_id: str
    query: str
    top_k: int
    repository: "_FakeVectorRepository"
    expected_mode: str
    expected_fallback_reason: Optional[str]
    expected_paths: List[str]
    expected_citation_paths: List[str]
    forbidden_paths: List[str]
    page_ids: Optional[List[str]] = None
    section_paths: Optional[List[str]] = None
    source_kinds: Optional[List[str]] = None


@dataclass(frozen=True)
class VectorScenarioResult:
    scenario_id: str
    retrieval_mode: str
    retrieval_fallback_reason: Optional[str]
    retrieved_paths: List[str]
    citation_paths: List[str]
    passed: bool


@dataclass(frozen=True)
class VectorRetrievalEvalResult:
    total_scenarios: int
    passed_scenarios: int
    passed: bool
    scenario_results: List[VectorScenarioResult]


class _DeterministicEmbeddingClient(EmbeddingClient):
    def __init__(self, *, vectors_by_query: Dict[str, List[float]]) -> None:
        self._vectors_by_query = dict(vectors_by_query)

    @property
    def name(self) -> str:
        return "openai"

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        embeddings = [
            list(self._vectors_by_query.get(input_text, [0.0, 0.0]))
            for input_text in request.inputs
        ]
        return EmbeddingResponse(
            provider="openai",
            model="text-embedding-3-small",
            embeddings=embeddings,
            token_input=len(request.inputs),
        )


class _FakeVectorRepository:
    def __init__(
        self,
        *,
        chunks: Sequence[_FixtureChunk],
        raise_vector_error: bool = False,
        enforce_production_scope: bool = True,
    ) -> None:
        self._chunks = list(chunks)
        self._raise_vector_error = raise_vector_error
        self._enforce_production_scope = enforce_production_scope

    def supports_vector_query(self) -> bool:
        return True

    def list_production_chunks_by_vector(
        self,
        *,
        query_embedding: List[float],
        top_k: int,
        page_ids: Optional[List[str]] = None,
        section_paths: Optional[List[str]] = None,
        source_kinds: Optional[List[str]] = None,
    ) -> List[SemanticChunkMatch]:
        _ = query_embedding
        if self._raise_vector_error:
            raise ChunkVectorQueryError("synthetic pgvector failure")

        rows = [
            chunk
            for chunk in self._filter_chunks(
                page_ids=page_ids,
                section_paths=section_paths,
                source_kinds=source_kinds,
            )
            if chunk.semantic_score is not None
        ]
        rows.sort(
            key=lambda chunk: (
                -float(chunk.semantic_score or 0.0),
                chunk.chunk_id,
            )
        )
        return [
            SemanticChunkMatch(
                chunk_id=chunk.chunk_id,
                chunk_index=chunk.chunk_index,
                chunk_text=chunk.chunk_text,
                notion_path=chunk.notion_path,
                source_kind=chunk.source_kind,
                notion_page_id=chunk.notion_page_id,
                score=float(chunk.semantic_score or 0.0),
            )
            for chunk in rows[:top_k]
        ]

    def list_production_chunks(
        self,
        *,
        page_ids: Optional[List[str]] = None,
        section_paths: Optional[List[str]] = None,
        source_kinds: Optional[List[str]] = None,
    ) -> List[RetrievalChunkCandidate]:
        rows = self._filter_chunks(
            page_ids=page_ids,
            section_paths=section_paths,
            source_kinds=source_kinds,
        )
        return [
            RetrievalChunkCandidate(
                chunk_id=chunk.chunk_id,
                chunk_index=chunk.chunk_index,
                chunk_text=chunk.chunk_text,
                notion_path=chunk.notion_path,
                source_kind=chunk.source_kind,
                notion_page_id=chunk.notion_page_id,
                embedding_text=chunk.embedding_text,
            )
            for chunk in rows
        ]

    def _filter_chunks(
        self,
        *,
        page_ids: Optional[List[str]],
        section_paths: Optional[List[str]],
        source_kinds: Optional[List[str]],
    ) -> List[_FixtureChunk]:
        effective_source_kinds = self._effective_source_kinds(source_kinds)
        if not effective_source_kinds:
            return []

        normalized_page_ids = {
            str(page_id).strip()
            for page_id in page_ids or []
            if str(page_id).strip()
        }
        normalized_section_paths = [
            str(section_path).strip()
            for section_path in section_paths or []
            if str(section_path).strip()
        ]

        rows = [
            chunk
            for chunk in self._chunks
            if chunk.source_kind in effective_source_kinds
        ]
        if normalized_page_ids:
            rows = [
                chunk
                for chunk in rows
                if chunk.notion_page_id in normalized_page_ids
            ]
        if normalized_section_paths:
            rows = [
                chunk
                for chunk in rows
                if any(
                    chunk.notion_path.startswith(section_path)
                    for section_path in normalized_section_paths
                )
            ]
        return rows

    def _effective_source_kinds(
        self,
        source_kinds: Optional[List[str]],
    ) -> List[str]:
        normalized = [
            str(source_kind).strip()
            for source_kind in source_kinds or []
            if str(source_kind).strip()
        ]
        if self._enforce_production_scope:
            if normalized and "notion" not in normalized:
                return []
            return ["notion"]

        if normalized:
            return normalized
        return ["notion", "source_document"]


def evaluate_vector_retrieval_regressions(
    *,
    enforce_production_scope: bool = True,
) -> VectorRetrievalEvalResult:
    embedding_client = _DeterministicEmbeddingClient(
        vectors_by_query={
            "Summarize the transformer note": [0.9, 0.1],
            "Find the accepted supplement explanation": [0.7, 0.3],
            "Show external PDF attention notes": [1.0, 0.0],
        }
    )
    scenario_results: List[VectorScenarioResult] = []

    for scenario in build_vector_retrieval_scenarios(
        enforce_production_scope=enforce_production_scope
    ):
        query_embedding = _embed_query(
            embedding_client=embedding_client,
            query=scenario.query,
        )
        retriever = ProductionChunkRetriever(chunk_repository=scenario.repository)
        retrieval_result = retriever.retrieve_with_metadata(
            query_text=scenario.query,
            top_k=scenario.top_k,
            page_ids=scenario.page_ids,
            section_paths=scenario.section_paths,
            source_kinds=scenario.source_kinds,
            query_embedding=query_embedding,
            allow_legacy_embedding_scoring=False,
        )
        retrieved_paths = [chunk.notion_path for chunk in retrieval_result.chunks]
        citation_paths = _build_unique_paths(retrieved_paths)
        passed = (
            retrieval_result.retrieval_mode == scenario.expected_mode
            and retrieval_result.retrieval_fallback_reason
            == scenario.expected_fallback_reason
            and retrieved_paths == scenario.expected_paths
            and citation_paths == scenario.expected_citation_paths
            and not any(
                forbidden_path in retrieved_paths or forbidden_path in citation_paths
                for forbidden_path in scenario.forbidden_paths
            )
        )
        scenario_results.append(
            VectorScenarioResult(
                scenario_id=scenario.scenario_id,
                retrieval_mode=retrieval_result.retrieval_mode,
                retrieval_fallback_reason=retrieval_result.retrieval_fallback_reason,
                retrieved_paths=retrieved_paths,
                citation_paths=citation_paths,
                passed=passed,
            )
        )

    total_scenarios = len(scenario_results)
    passed_scenarios = sum(1 for result in scenario_results if result.passed)
    return VectorRetrievalEvalResult(
        total_scenarios=total_scenarios,
        passed_scenarios=passed_scenarios,
        passed=passed_scenarios == total_scenarios,
        scenario_results=scenario_results,
    )


def build_vector_retrieval_scenarios(
    *,
    enforce_production_scope: bool = True,
) -> List[_VectorScenario]:
    return [
        _VectorScenario(
            scenario_id="semantic_ranking_and_scope",
            query="Summarize the transformer note",
            top_k=2,
            repository=_FakeVectorRepository(
                chunks=[
                    _FixtureChunk(
                        chunk_id=1,
                        chunk_index=0,
                        chunk_text="Attention uses query key value vectors",
                        notion_path="Knowledge/NLP/Week5/Attention",
                        source_kind="notion",
                        notion_page_id="page-nlp-week5",
                        semantic_score=0.91,
                    ),
                    _FixtureChunk(
                        chunk_id=2,
                        chunk_index=1,
                        chunk_text="Transformer encoder has multi-head attention",
                        notion_path="Knowledge/NLP/Week5/Transformer",
                        source_kind="notion",
                        notion_page_id="page-nlp-week5",
                        semantic_score=0.97,
                    ),
                    _FixtureChunk(
                        chunk_id=3,
                        chunk_index=0,
                        chunk_text="External PDF source attention note",
                        notion_path="Synthetic/External/PDF",
                        source_kind="source_document",
                        notion_page_id=None,
                        semantic_score=0.99,
                    ),
                ],
                enforce_production_scope=enforce_production_scope,
            ),
            expected_mode=RETRIEVAL_MODE_PGVECTOR_EXACT_COSINE,
            expected_fallback_reason=None,
            expected_paths=[
                "Knowledge/NLP/Week5/Transformer",
                "Knowledge/NLP/Week5/Attention",
            ],
            expected_citation_paths=[
                "Knowledge/NLP/Week5/Transformer",
                "Knowledge/NLP/Week5/Attention",
            ],
            forbidden_paths=["Synthetic/External/PDF"],
            source_kinds=["notion"],
        ),
        _VectorScenario(
            scenario_id="vector_query_failure_fallback",
            query="Summarize the transformer note",
            top_k=2,
            repository=_FakeVectorRepository(
                chunks=[
                    _FixtureChunk(
                        chunk_id=10,
                        chunk_index=0,
                        chunk_text="Transformer encoder has multi-head attention",
                        notion_path="Knowledge/NLP/Week5/Transformer",
                        source_kind="notion",
                        notion_page_id="page-nlp-week5",
                    ),
                    _FixtureChunk(
                        chunk_id=11,
                        chunk_index=1,
                        chunk_text="Attention uses query key value vectors",
                        notion_path="Knowledge/NLP/Week5/Attention",
                        source_kind="notion",
                        notion_page_id="page-nlp-week5",
                    ),
                    _FixtureChunk(
                        chunk_id=12,
                        chunk_index=0,
                        chunk_text="External PDF source attention note",
                        notion_path="Synthetic/External/PDF",
                        source_kind="source_document",
                        notion_page_id=None,
                    ),
                ],
                raise_vector_error=True,
                enforce_production_scope=enforce_production_scope,
            ),
            expected_mode=RETRIEVAL_MODE_LEXICAL_FALLBACK,
            expected_fallback_reason="VECTOR_QUERY_FAILED",
            expected_paths=[
                "Knowledge/NLP/Week5/Transformer",
            ],
            expected_citation_paths=[
                "Knowledge/NLP/Week5/Transformer",
            ],
            forbidden_paths=["Synthetic/External/PDF"],
            source_kinds=["notion"],
        ),
        _VectorScenario(
            scenario_id="vector_data_unavailable_dedupes_citations",
            query="Find the accepted supplement explanation",
            top_k=3,
            repository=_FakeVectorRepository(
                chunks=[
                    _FixtureChunk(
                        chunk_id=20,
                        chunk_index=0,
                        chunk_text="accepted supplement explanation line one",
                        notion_path=(
                            "Knowledge/NLP/Week5/AI Supplement Zone/Accepted Idea"
                        ),
                        source_kind="notion",
                        notion_page_id="page-nlp-week5",
                    ),
                    _FixtureChunk(
                        chunk_id=21,
                        chunk_index=1,
                        chunk_text="accepted supplement explanation line two",
                        notion_path=(
                            "Knowledge/NLP/Week5/AI Supplement Zone/Accepted Idea"
                        ),
                        source_kind="notion",
                        notion_page_id="page-nlp-week5",
                    ),
                    _FixtureChunk(
                        chunk_id=22,
                        chunk_index=0,
                        chunk_text="accepted supplement explanation from rejected source",
                        notion_path="Synthetic/Rejected/Chunk",
                        source_kind="source_document",
                        notion_page_id=None,
                    ),
                ],
                enforce_production_scope=enforce_production_scope,
            ),
            expected_mode=RETRIEVAL_MODE_LEXICAL_FALLBACK,
            expected_fallback_reason="VECTOR_DATA_UNAVAILABLE",
            expected_paths=[
                "Knowledge/NLP/Week5/AI Supplement Zone/Accepted Idea",
                "Knowledge/NLP/Week5/AI Supplement Zone/Accepted Idea",
            ],
            expected_citation_paths=[
                "Knowledge/NLP/Week5/AI Supplement Zone/Accepted Idea",
            ],
            forbidden_paths=["Synthetic/Rejected/Chunk"],
            source_kinds=["notion"],
        ),
        _VectorScenario(
            scenario_id="production_scope_blocks_source_document_only_queries",
            query="Show external PDF attention notes",
            top_k=2,
            repository=_FakeVectorRepository(
                chunks=[
                    _FixtureChunk(
                        chunk_id=30,
                        chunk_index=0,
                        chunk_text="External PDF source attention note",
                        notion_path="Synthetic/External/PDF",
                        source_kind="source_document",
                        notion_page_id=None,
                        semantic_score=0.98,
                    ),
                ],
                enforce_production_scope=enforce_production_scope,
            ),
            expected_mode=RETRIEVAL_MODE_LEXICAL_FALLBACK,
            expected_fallback_reason=None,
            expected_paths=[],
            expected_citation_paths=[],
            forbidden_paths=["Synthetic/External/PDF"],
            source_kinds=["source_document"],
        ),
    ]


def format_vector_retrieval_eval_result(result: VectorRetrievalEvalResult) -> str:
    status = "pass" if result.passed else "fail"
    lines = [
        (
            "vector_retrieval_regression: "
            f"{status} ({result.passed_scenarios}/{result.total_scenarios})"
        ),
        "scenario_results:",
    ]
    for scenario_result in result.scenario_results:
        lines.append(
            "- "
            f"{scenario_result.scenario_id}: "
            f"{'pass' if scenario_result.passed else 'fail'}; "
            f"mode={scenario_result.retrieval_mode}; "
            f"fallback={scenario_result.retrieval_fallback_reason}; "
            f"retrieved={scenario_result.retrieved_paths}; "
            f"citations={scenario_result.citation_paths}"
        )
    return "\n".join(lines)


def _build_unique_paths(paths: Iterable[str]) -> List[str]:
    unique_paths: List[str] = []
    seen_paths = set()
    for value in paths:
        normalized = str(value).strip()
        if not normalized or normalized in seen_paths:
            continue
        seen_paths.add(normalized)
        unique_paths.append(normalized)
    return unique_paths


def _embed_query(
    *,
    embedding_client: EmbeddingClient,
    query: str,
) -> List[float]:
    response = asyncio.run(
        embedding_client.embed(
            EmbeddingRequest(
                inputs=[query],
                dimensions=2,
            )
        )
    )
    return list(response.embeddings[0])


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run deterministic vector retrieval regression coverage without "
            "real provider calls."
        )
    )
    args = parser.parse_args()
    _ = args

    result = evaluate_vector_retrieval_regressions()
    print(format_vector_retrieval_eval_result(result))
    if not result.passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
