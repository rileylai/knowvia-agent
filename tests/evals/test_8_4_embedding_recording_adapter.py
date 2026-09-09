from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

import pytest

from src.providers.embedding import (
    EmbeddingCapabilities,
    EmbeddingClient,
    EmbeddingClientError,
    EmbeddingRequest,
    EmbeddingResponse,
    OpenAIEmbeddingClient,
)
from src.services.embedding_batch_service import (
    EmbeddingBatchError,
    EmbeddingBatchService,
)


VECTOR_MODEL = "text-embedding-3-small"
VECTOR_DIMENSIONS = 1536


class RecordingEmbeddingClient(EmbeddingClient):
    """Offline copy of the live harness recording adapter."""

    def __init__(self, delegate: EmbeddingClient) -> None:
        self._delegate = delegate
        self.calls: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return self._delegate.name

    def get_capabilities(
        self,
        *,
        model: str,
        dimensions: int,
    ) -> Optional[EmbeddingCapabilities]:
        return self._delegate.get_capabilities(
            model=model,
            dimensions=dimensions,
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        started = time.perf_counter()
        try:
            response = await self._delegate.embed(request)
        except Exception as exc:
            self.calls.append(
                {
                    "input_count": len(request.inputs),
                    "model": request.model or VECTOR_MODEL,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                    "error_type": type(exc).__name__,
                }
            )
            raise
        self.calls.append(
            {
                "input_count": len(request.inputs),
                "model": response.model,
                "token_input": response.token_input,
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            }
        )
        return response


def _openai_delegate(
    transport: Any,
) -> OpenAIEmbeddingClient:
    return OpenAIEmbeddingClient(
        api_key="offline-test-key",
        default_model=VECTOR_MODEL,
        transport=transport,
    )


def test_recording_adapter_delegates_identity_and_capabilities() -> None:
    delegate = _openai_delegate(lambda url, headers, payload: {"data": []})
    recording = RecordingEmbeddingClient(delegate)

    expected = delegate.get_capabilities(
        model=VECTOR_MODEL,
        dimensions=VECTOR_DIMENSIONS,
    )

    assert recording.name == delegate.name
    assert recording.get_capabilities(
        model=VECTOR_MODEL,
        dimensions=VECTOR_DIMENSIONS,
    ) == expected


def test_embedding_batch_service_accepts_supported_wrapped_delegate() -> None:
    transport_calls: list[Dict[str, Any]] = []

    def transport(url: str, headers: Dict[str, str], payload: Dict[str, Any]) -> Dict[str, Any]:
        transport_calls.append(payload)
        return {"data": []}

    recording = RecordingEmbeddingClient(_openai_delegate(transport))

    EmbeddingBatchService(
        embedding_client=recording,
        model=VECTOR_MODEL,
        dimensions=VECTOR_DIMENSIONS,
    )

    assert transport_calls == []


def test_embedding_batch_service_rejects_unsupported_wrapped_capability() -> None:
    recording = RecordingEmbeddingClient(
        _openai_delegate(lambda url, headers, payload: {"data": []})
    )

    with pytest.raises(EmbeddingBatchError) as exc_info:
        EmbeddingBatchService(
            embedding_client=recording,
            model="text-embedding-3-large",
            dimensions=VECTOR_DIMENSIONS,
        )

    assert exc_info.value.reason == "CAPABILITY_UNAVAILABLE"


def test_recording_adapter_delegates_embed_once_and_records_bounded_metadata() -> None:
    transport_calls: list[Dict[str, Any]] = []

    def transport(url: str, headers: Dict[str, str], payload: Dict[str, Any]) -> Dict[str, Any]:
        transport_calls.append(payload)
        return {
            "data": [{"index": 0, "embedding": [0.1, 0.2]}],
            "usage": {"prompt_tokens": 7},
        }

    recording = RecordingEmbeddingClient(_openai_delegate(transport))
    response = asyncio.run(
        recording.embed(
            EmbeddingRequest(
                inputs=["bounded secret document text"],
                model=VECTOR_MODEL,
                dimensions=VECTOR_DIMENSIONS,
            )
        )
    )

    assert len(transport_calls) == 1
    assert transport_calls[0]["model"] == VECTOR_MODEL
    assert transport_calls[0]["input"] == ["bounded secret document text"]
    assert transport_calls[0]["dimensions"] == VECTOR_DIMENSIONS
    assert response.model == VECTOR_MODEL
    assert len(recording.calls) == 1
    assert set(recording.calls[0]) == {
        "input_count",
        "model",
        "token_input",
        "latency_ms",
    }
    assert recording.calls[0]["input_count"] == 1
    assert recording.calls[0]["model"] == VECTOR_MODEL
    assert recording.calls[0]["token_input"] == 7
    assert recording.calls[0]["latency_ms"] >= 0
    assert "bounded secret document text" not in repr(recording.calls)
    assert "embeddings" not in recording.calls[0]
    assert "raw_response" not in recording.calls[0]
    assert "inputs" not in recording.calls[0]
    assert "offline-test-key" not in repr(recording.calls)


class _FailingEmbeddingDelegate(EmbeddingClient):
    @property
    def name(self) -> str:
        return "openai"

    def get_capabilities(
        self,
        *,
        model: str,
        dimensions: int,
    ) -> Optional[EmbeddingCapabilities]:
        return EmbeddingCapabilities(
            provider=self.name,
            model=model,
            dimensions=dimensions,
            max_input_count=2048,
            max_single_input_tokens=8192,
            max_aggregate_tokens=300000,
            tokenizer_model=model,
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        _ = request
        raise EmbeddingClientError("delegated provider failure")


def test_recording_adapter_propagates_delegate_error_and_records_no_payload() -> None:
    recording = RecordingEmbeddingClient(_FailingEmbeddingDelegate())

    with pytest.raises(EmbeddingClientError, match="delegated provider failure"):
        asyncio.run(
            recording.embed(
                EmbeddingRequest(
                    inputs=["not persisted"],
                    model=VECTOR_MODEL,
                    dimensions=VECTOR_DIMENSIONS,
                )
            )
        )

    assert len(recording.calls) == 1
    assert set(recording.calls[0]) == {
        "input_count",
        "model",
        "latency_ms",
        "error_type",
    }
    assert recording.calls[0]["input_count"] == 1
    assert recording.calls[0]["model"] == VECTOR_MODEL
    assert recording.calls[0]["latency_ms"] >= 0
    assert recording.calls[0]["error_type"] == "EmbeddingClientError"
    assert "not persisted" not in repr(recording.calls)
