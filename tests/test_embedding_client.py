import asyncio
import io
import json
import traceback
from typing import Any, Dict, List
from urllib import error

import pytest

from src.providers import (
    EmbeddingClientError,
    EmbeddingRequest,
    EmbeddingTransportError,
    OpenAIEmbeddingClient,
    build_embedding_request_diagnostics,
)
from src.observability.external_error import ExternalErrorCategory
from src.providers import embedding as embedding_module


def test_openai_embedding_client_returns_embeddings_with_mock_transport() -> None:
    captured_payload: Dict[str, Any] = {}
    captured_headers: Dict[str, str] = {}
    captured_url = ""

    def fake_transport(
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        nonlocal captured_url
        captured_url = url
        captured_headers.update(headers)
        captured_payload.update(payload)
        return {
                "data": [
                    {"index": 0, "embedding": [0.1, 0.2, 0.3]},
                    {"index": 1, "embedding": [0.4, 0.5, 0.6]},
            ],
            "usage": {"prompt_tokens": 12},
        }

    client = OpenAIEmbeddingClient(
        api_key="test-key",
        default_model="text-embedding-3-small",
        transport=fake_transport,
    )
    request = EmbeddingRequest(
        inputs=["chunk-a", "chunk-b"],
        metadata={"workflow_id": "wf-1"},
    )

    response = asyncio.run(client.embed(request))

    assert captured_url == "https://api.openai.com/v1/embeddings"
    assert captured_headers["Authorization"] == "Bearer test-key"
    assert captured_payload["model"] == "text-embedding-3-small"
    assert captured_payload["input"] == ["chunk-a", "chunk-b"]
    assert response.provider == "openai"
    assert response.model == "text-embedding-3-small"
    assert response.token_input == 12
    assert response.embeddings == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    assert response.indices == [0, 1]


def test_openai_embedding_client_uses_request_model_and_dimensions() -> None:
    captured_payload: Dict[str, Any] = {}

    def fake_transport(
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        _ = url, headers
        captured_payload.update(payload)
        return {"data": [{"index": 0, "embedding": [0.01, 0.02]}]}

    client = OpenAIEmbeddingClient(
        api_key="test-key",
        default_model="text-embedding-3-small",
        transport=fake_transport,
    )
    request = EmbeddingRequest(
        inputs=["chunk-a"],
        model="text-embedding-3-large",
        dimensions=2,
    )

    response = asyncio.run(client.embed(request))

    assert captured_payload["model"] == "text-embedding-3-large"
    assert captured_payload["dimensions"] == 2
    assert response.model == "text-embedding-3-large"
    assert response.embeddings == [[0.01, 0.02]]


def test_openai_embedding_client_raises_error_for_invalid_transport_output() -> None:
    def fake_transport(
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        _ = url, headers, payload
        return {"unexpected": "shape"}

    client = OpenAIEmbeddingClient(
        api_key="test-key",
        transport=fake_transport,
    )
    request = EmbeddingRequest(inputs=["chunk-a"])

    with pytest.raises(EmbeddingClientError):
        asyncio.run(client.embed(request))


@pytest.mark.parametrize(
    "indices",
    [
        [0, 0],
        [0, 2],
        [1, 0],
        [0],
    ],
)
def test_openai_embedding_client_rejects_duplicate_missing_out_of_range_or_unordered_indices(
    indices: List[int],
) -> None:
    def fake_transport(
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        _ = url, headers, payload
        return {
            "data": [
                {"index": index, "embedding": [float(position)]}
                for position, index in enumerate(indices)
            ]
        }

    client = OpenAIEmbeddingClient(api_key="test-key", transport=fake_transport)

    with pytest.raises(EmbeddingClientError) as exc_info:
        asyncio.run(client.embed(EmbeddingRequest(inputs=["one", "two"])))

    assert exc_info.value.category == ExternalErrorCategory.RESPONSE_INVALID
    assert exc_info.value.retryable is False


def test_openai_embedding_client_rejects_empty_api_key() -> None:
    with pytest.raises(EmbeddingClientError):
        OpenAIEmbeddingClient(api_key="  ")


def test_embedding_request_diagnostics_contains_only_safe_shape_fields() -> None:
    diagnostics = build_embedding_request_diagnostics(
        inputs=["alpha", "  ", "資料"],
        provider_name="openai",
        model="text-embedding-test",
        dimensions=1536,
        endpoint_class="openai_embeddings",
    )

    assert diagnostics.input_count == 3
    assert diagnostics.empty_input_count == 1
    assert diagnostics.max_single_input_chars == 5
    assert diagnostics.max_single_input_bytes == 6
    assert diagnostics.aggregate_input_bytes == 13
    assert diagnostics.aggregate_input_token_estimate > 0
    safe_payload = diagnostics.to_safe_dict()
    assert "inputs" not in safe_payload
    assert "payload" not in safe_payload
    assert "alpha" not in json.dumps(safe_payload)
    assert "資料" not in json.dumps(safe_payload)


def test_openai_embedding_client_rejects_empty_input_before_transport() -> None:
    transport_called = False

    def fake_transport(
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        nonlocal transport_called
        _ = url, headers, payload
        transport_called = True
        return {"data": []}

    client = OpenAIEmbeddingClient(api_key="test-key", transport=fake_transport)

    with pytest.raises(EmbeddingClientError) as exc_info:
        asyncio.run(client.embed(EmbeddingRequest(inputs=["valid", "  "])))

    assert transport_called is False
    assert exc_info.value.category == ExternalErrorCategory.VALIDATION_FAILED
    assert exc_info.value.retryable is False
    assert exc_info.value.request_diagnostics.empty_input_count == 1


@pytest.mark.parametrize(
    ("status_code", "provider_error", "expected_category", "expected_retryable"),
    [
        (400, {}, ExternalErrorCategory.REQUEST_INVALID, False),
        (
            400,
            {"code": "context_length_exceeded"},
            ExternalErrorCategory.REQUEST_TOO_LARGE,
            False,
        ),
        (
            400,
            {"type": "invalid_dimensions"},
            ExternalErrorCategory.VALIDATION_FAILED,
            False,
        ),
        (401, {}, ExternalErrorCategory.AUTHENTICATION_FAILED, False),
        (403, {}, ExternalErrorCategory.AUTHENTICATION_FAILED, False),
        (408, {}, ExternalErrorCategory.REQUEST_TIMEOUT, True),
        (413, {}, ExternalErrorCategory.REQUEST_TOO_LARGE, False),
        (422, {}, ExternalErrorCategory.VALIDATION_FAILED, False),
        (429, {}, ExternalErrorCategory.RATE_LIMITED, True),
        (500, {}, ExternalErrorCategory.UPSTREAM_SERVER_ERROR, True),
        (502, {}, ExternalErrorCategory.UPSTREAM_SERVER_ERROR, True),
        (503, {}, ExternalErrorCategory.UPSTREAM_SERVER_ERROR, True),
        (504, {}, ExternalErrorCategory.UPSTREAM_SERVER_ERROR, True),
        (501, {}, ExternalErrorCategory.UPSTREAM_SERVER_ERROR, False),
    ],
)
def test_default_embedding_transport_classifies_http_errors_without_body_leak(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    provider_error: Dict[str, str],
    expected_category: ExternalErrorCategory,
    expected_retryable: bool,
) -> None:
    private_body = {
        "error": {
            **provider_error,
            "message": "private chunk text and credential sk-secret",
        }
    }

    def fake_urlopen(request, timeout):
        _ = request, timeout
        raise error.HTTPError(
            url="https://api.openai.com/v1/embeddings",
            code=status_code,
            msg="private provider message",
            hdrs={"Retry-After": "30"},
            fp=io.BytesIO(json.dumps(private_body).encode("utf-8")),
        )

    monkeypatch.setattr(embedding_module.urllib_request, "urlopen", fake_urlopen)

    with pytest.raises(EmbeddingTransportError) as exc_info:
        embedding_module._default_transport(
            "https://api.openai.com/v1/embeddings",
            {"Authorization": "Bearer sk-secret"},
            {"model": "text-embedding-test", "input": ["private chunk text"]},
        )

    error_value = exc_info.value
    assert error_value.category == expected_category
    assert error_value.retryable is expected_retryable
    assert error_value.http_status == status_code
    assert error_value.retry_after_seconds == (
        30 if status_code == 429 else None
    )
    assert "private" not in str(error_value)
    assert "sk-secret" not in str(error_value)


def test_openai_embedding_client_maps_timeout_without_raw_exception() -> None:
    def fake_transport(
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        _ = url, headers, payload
        raise TimeoutError("private chunk text")

    client = OpenAIEmbeddingClient(api_key="test-key", transport=fake_transport)

    with pytest.raises(EmbeddingClientError) as exc_info:
        asyncio.run(client.embed(EmbeddingRequest(inputs=["synthetic input"])))

    assert exc_info.value.category == ExternalErrorCategory.TIMEOUT
    assert exc_info.value.retryable is True
    assert "private chunk text" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert "private chunk text" not in "".join(
        traceback.format_exception(
            type(exc_info.value),
            exc_info.value,
            exc_info.value.__traceback__,
        )
    )


def test_openai_embedding_client_maps_invalid_response_without_raw_body() -> None:
    private_response = {
        "unexpected": "private chunk text",
        "credential": "sk-secret",
    }
    client = OpenAIEmbeddingClient(
        api_key="test-key",
        transport=lambda url, headers, payload: private_response,
    )

    with pytest.raises(EmbeddingClientError) as exc_info:
        asyncio.run(client.embed(EmbeddingRequest(inputs=["synthetic input"])))

    assert exc_info.value.category == ExternalErrorCategory.RESPONSE_INVALID
    assert exc_info.value.retryable is False
    assert "private chunk text" not in str(exc_info.value)
    assert "sk-secret" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


def test_default_embedding_transport_maps_connection_failure_without_raw_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request, timeout):
        _ = request, timeout
        raise OSError("private chunk text sk-secret")

    monkeypatch.setattr(embedding_module.urllib_request, "urlopen", fake_urlopen)

    with pytest.raises(EmbeddingTransportError) as exc_info:
        embedding_module._default_transport(
            "https://api.openai.com/v1/embeddings",
            {},
            {"model": "test", "input": ["private chunk text"]},
        )

    assert exc_info.value.category == ExternalErrorCategory.TRANSPORT_UNAVAILABLE
    assert exc_info.value.retryable is True
    assert "private chunk text" not in str(exc_info.value)
    assert "sk-secret" not in str(exc_info.value)


def test_default_embedding_transport_maps_invalid_json_without_raw_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _InvalidResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            _ = exc_type, exc_value, traceback

        def read(self) -> bytes:
            return b"private chunk text sk-secret"

    monkeypatch.setattr(
        embedding_module.urllib_request,
        "urlopen",
        lambda request, timeout: _InvalidResponse(),
    )

    with pytest.raises(EmbeddingTransportError) as exc_info:
        embedding_module._default_transport(
            "https://api.openai.com/v1/embeddings",
            {},
            {"model": "test", "input": ["private chunk text"]},
        )

    assert exc_info.value.category == ExternalErrorCategory.RESPONSE_INVALID
    assert exc_info.value.retryable is False
    assert "private chunk text" not in str(exc_info.value)
    assert "sk-secret" not in str(exc_info.value)
