from __future__ import annotations

import asyncio
import json
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional
from urllib import error as urllib_error
from urllib import request as urllib_request

from pydantic import BaseModel, Field, ValidationError

from src.observability.external_error import (
    ExternalErrorCategory,
    ExternalErrorDiagnostic,
    classify_http_error,
    extract_provider_error_category,
    parse_retry_after_seconds,
)

INPUT_SIZE_ESTIMATOR_VERSION = "utf8_bytes_div_4_v1"
OFFICIAL_OPENAI_BASE_URL = "https://api.openai.com/v1"


class EmbeddingRequest(BaseModel):
    inputs: List[str] = Field(min_length=1)
    model: Optional[str] = None
    dimensions: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None


class EmbeddingResponse(BaseModel):
    provider: str
    model: str
    embeddings: List[List[float]]
    indices: Optional[List[int]] = None
    token_input: Optional[int] = None
    raw_response: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class EmbeddingCapabilities:
    provider: str
    model: str
    dimensions: int
    max_input_count: int
    max_single_input_tokens: int
    max_aggregate_tokens: int
    tokenizer_model: str


@dataclass(frozen=True)
class EmbeddingRequestDiagnostics:
    provider_name: str
    model: str
    dimensions: Optional[int]
    endpoint_class: str
    input_count: int
    empty_input_count: int
    max_single_input_chars: int
    max_single_input_bytes: int
    max_single_input_token_estimate: int
    aggregate_input_bytes: int
    aggregate_input_token_estimate: int
    input_size_estimator_version: str = INPUT_SIZE_ESTIMATOR_VERSION

    def to_safe_dict(self) -> Dict[str, Any]:
        return {
            "dependency": "embedding",
            "operation": "embedding_request",
            "endpoint_class": self.endpoint_class,
            "provider_name": self.provider_name,
            "model": self.model,
            "dimensions": self.dimensions,
            "input_count": self.input_count,
            "empty_input_count": self.empty_input_count,
            "max_single_input_chars": self.max_single_input_chars,
            "max_single_input_bytes": self.max_single_input_bytes,
            "max_single_input_token_estimate": (
                self.max_single_input_token_estimate
            ),
            "aggregate_input_bytes": self.aggregate_input_bytes,
            "aggregate_input_token_estimate": (
                self.aggregate_input_token_estimate
            ),
            "input_size_estimator_version": self.input_size_estimator_version,
        }


class EmbeddingTransportError(Exception):
    def __init__(self, diagnostic: ExternalErrorDiagnostic) -> None:
        super().__init__("Embedding transport request failed")
        self.diagnostic = diagnostic
        self.category = diagnostic.category
        self.retryable = diagnostic.retryable
        self.http_status = diagnostic.http_status
        self.retry_after_seconds = diagnostic.retry_after_seconds


class EmbeddingClientError(Exception):
    def __init__(
        self,
        message: str = "Embedding request failed",
        *,
        diagnostic: Optional[ExternalErrorDiagnostic] = None,
        request_diagnostics: Optional[EmbeddingRequestDiagnostics] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.diagnostic = diagnostic
        self.request_diagnostics = request_diagnostics
        self.category = diagnostic.category if diagnostic is not None else None
        self.retryable = diagnostic.retryable if diagnostic is not None else False
        self.http_status = diagnostic.http_status if diagnostic is not None else None
        self.retry_after_seconds = (
            diagnostic.retry_after_seconds if diagnostic is not None else None
        )


class EmbeddingClient(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    def get_capabilities(
        self,
        *,
        model: str,
        dimensions: int,
    ) -> Optional[EmbeddingCapabilities]:
        _ = model, dimensions
        return None

    @abstractmethod
    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        raise NotImplementedError


TransportFn = Callable[[str, Dict[str, str], Dict[str, Any]], Dict[str, Any]]


class OpenAIEmbeddingClient(EmbeddingClient):
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        default_model: str = "text-embedding-3-small",
        transport: Optional[TransportFn] = None,
        infrastructure_exception_classifier: Optional[
            Callable[[BaseException], Optional[BaseException]]
        ] = None,
    ) -> None:
        normalized_key = api_key.strip()
        if not normalized_key:
            raise EmbeddingClientError("api_key must not be empty")

        normalized_model = default_model.strip()
        if not normalized_model:
            raise EmbeddingClientError("default_model must not be empty")

        self._api_key = normalized_key
        self._base_url = base_url.rstrip("/")
        self._endpoint_class = (
            "openai_embeddings"
            if self._base_url == OFFICIAL_OPENAI_BASE_URL
            else "openai_compatible_embeddings"
        )
        self._default_model = normalized_model
        self._transport = transport or _default_transport
        self._infrastructure_exception_classifier = (
            infrastructure_exception_classifier
        )

    @property
    def name(self) -> str:
        return "openai"

    def get_capabilities(
        self,
        *,
        model: str,
        dimensions: int,
    ) -> Optional[EmbeddingCapabilities]:
        if self._endpoint_class != "openai_embeddings":
            return None
        return get_openai_embedding_capabilities(
            model=model,
            dimensions=dimensions,
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        model_name = (request.model or self._default_model).strip()
        if not model_name:
            raise EmbeddingClientError("model must not be empty")

        request_diagnostics = build_embedding_request_diagnostics(
            inputs=request.inputs,
            provider_name=self.name,
            model=model_name,
            dimensions=request.dimensions,
            endpoint_class=self._endpoint_class,
        )
        if request_diagnostics.empty_input_count:
            raise EmbeddingClientError(
                "Embedding inputs must not be empty",
                diagnostic=ExternalErrorDiagnostic(
                    category=ExternalErrorCategory.VALIDATION_FAILED,
                    retryable=False,
                ),
                request_diagnostics=request_diagnostics,
            )

        payload: Dict[str, Any] = {
            "model": model_name,
            "input": request.inputs,
        }
        if request.dimensions is not None:
            payload["dimensions"] = request.dimensions

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self._base_url}/embeddings"

        try:
            raw_response = await asyncio.to_thread(self._transport, url, headers, payload)
        except EmbeddingTransportError as exc:
            raise EmbeddingClientError(
                "Embedding request failed",
                diagnostic=exc.diagnostic,
                request_diagnostics=request_diagnostics,
            ) from None
        except TimeoutError as exc:
            raise EmbeddingClientError(
                "Embedding request failed",
                diagnostic=ExternalErrorDiagnostic(
                    category=ExternalErrorCategory.TIMEOUT,
                    retryable=True,
                ),
                request_diagnostics=request_diagnostics,
            ) from None
        except Exception as exc:
            infrastructure_error = (
                self._infrastructure_exception_classifier(exc)
                if self._infrastructure_exception_classifier is not None
                else None
            )
            if infrastructure_error is not None:
                raise infrastructure_error from exc
            raise EmbeddingClientError(
                "Embedding request failed",
                diagnostic=ExternalErrorDiagnostic(
                    category=ExternalErrorCategory.TRANSPORT_UNAVAILABLE,
                    retryable=True,
                ),
                request_diagnostics=request_diagnostics,
            ) from None

        try:
            data = raw_response["data"]
            usage = raw_response.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens")
        except (AttributeError, KeyError, TypeError):
            raise _invalid_response_error(request_diagnostics) from None

        if not isinstance(data, list):
            raise _invalid_response_error(request_diagnostics)

        embeddings: List[List[float]] = []
        indices: List[int] = []
        for item in data:
            if not isinstance(item, dict):
                raise _invalid_response_error(request_diagnostics)
            embedding = item.get("embedding")
            index = item.get("index")
            if (
                not isinstance(embedding, list)
                or not isinstance(index, int)
                or isinstance(index, bool)
            ):
                raise _invalid_response_error(request_diagnostics)
            embeddings.append(embedding)
            indices.append(index)

        expected_indices = list(range(len(request.inputs)))
        if len(embeddings) != len(request.inputs) or indices != expected_indices:
            raise _invalid_response_error(request_diagnostics)

        try:
            return EmbeddingResponse(
                provider=self.name,
                model=model_name,
                embeddings=embeddings,
                indices=indices,
                token_input=prompt_tokens,
            )
        except ValidationError:
            raise _invalid_response_error(request_diagnostics) from None


def build_embedding_request_diagnostics(
    *,
    inputs: List[str],
    provider_name: str,
    model: str,
    dimensions: Optional[int],
    endpoint_class: str,
) -> EmbeddingRequestDiagnostics:
    encoded_sizes = [len(value.encode("utf-8")) for value in inputs]
    character_sizes = [len(value) for value in inputs]
    token_estimates = [_estimate_tokens(size) for size in encoded_sizes]
    return EmbeddingRequestDiagnostics(
        provider_name=provider_name,
        model=model,
        dimensions=dimensions,
        endpoint_class=endpoint_class,
        input_count=len(inputs),
        empty_input_count=sum(1 for value in inputs if not value.strip()),
        max_single_input_chars=max(character_sizes, default=0),
        max_single_input_bytes=max(encoded_sizes, default=0),
        max_single_input_token_estimate=max(token_estimates, default=0),
        aggregate_input_bytes=sum(encoded_sizes),
        aggregate_input_token_estimate=sum(token_estimates),
    )


def get_openai_embedding_capabilities(
    *,
    model: str,
    dimensions: int,
) -> Optional[EmbeddingCapabilities]:
    normalized_model = model.strip()
    if normalized_model != "text-embedding-3-small" or dimensions != 1536:
        return None
    return EmbeddingCapabilities(
        provider="openai",
        model=normalized_model,
        dimensions=dimensions,
        max_input_count=2048,
        max_single_input_tokens=8192,
        max_aggregate_tokens=300000,
        tokenizer_model=normalized_model,
    )


def _estimate_tokens(encoded_byte_count: int) -> int:
    if encoded_byte_count <= 0:
        return 0
    return max(1, math.ceil(encoded_byte_count / 4))


def _invalid_response_error(
    request_diagnostics: EmbeddingRequestDiagnostics,
) -> EmbeddingClientError:
    return EmbeddingClientError(
        "Embedding response schema is invalid",
        diagnostic=ExternalErrorDiagnostic(
            category=ExternalErrorCategory.RESPONSE_INVALID,
            retryable=False,
        ),
        request_diagnostics=request_diagnostics,
    )


def _default_transport(
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(url=url, data=body, headers=headers, method="POST")
    try:
        with urllib_request.urlopen(req, timeout=60) as response:
            response_body = response.read()
    except urllib_error.HTTPError as exc:
        response_payload = _read_error_payload(exc)
        retry_after = parse_retry_after_seconds(
            exc.headers.get("Retry-After") if exc.headers is not None else None
        )
        diagnostic = classify_http_error(
            status_code=int(exc.code),
            provider_category=extract_provider_error_category(response_payload),
            retry_after_seconds=retry_after,
        )
        raise EmbeddingTransportError(diagnostic) from None
    except TimeoutError:
        raise EmbeddingTransportError(
            ExternalErrorDiagnostic(
                category=ExternalErrorCategory.TIMEOUT,
                retryable=True,
            )
        ) from None
    except (urllib_error.URLError, OSError):
        raise EmbeddingTransportError(
            ExternalErrorDiagnostic(
                category=ExternalErrorCategory.TRANSPORT_UNAVAILABLE,
                retryable=True,
            )
        ) from None

    try:
        parsed_response = json.loads(response_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise EmbeddingTransportError(
            ExternalErrorDiagnostic(
                category=ExternalErrorCategory.RESPONSE_INVALID,
                retryable=False,
            )
        ) from None
    if not isinstance(parsed_response, dict):
        raise EmbeddingTransportError(
            ExternalErrorDiagnostic(
                category=ExternalErrorCategory.RESPONSE_INVALID,
                retryable=False,
            )
        )
    return parsed_response


def _read_error_payload(exc: urllib_error.HTTPError) -> Optional[Dict[str, Any]]:
    try:
        response_body = exc.read()
        parsed_payload = json.loads(response_body.decode("utf-8"))
    except (
        AttributeError,
        OSError,
        TypeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return None
    return parsed_payload if isinstance(parsed_payload, dict) else None
