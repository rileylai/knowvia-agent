from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional, Sequence, Tuple

from src.observability.external_error import ExternalErrorCategory
from src.providers.embedding import (
    EmbeddingCapabilities,
    EmbeddingClient,
    EmbeddingClientError,
    EmbeddingRequest,
    EmbeddingResponse,
)


TokenCounter = Callable[[str], int]
AsyncSleeper = Callable[[float], Awaitable[None]]
TOKEN_SAFETY_ESTIMATOR_VERSION = "utf8_chars_or_three_quarters_bytes_v1"

RETRYABLE_EMBEDDING_CATEGORIES = frozenset(
    {
        ExternalErrorCategory.TIMEOUT,
        ExternalErrorCategory.TRANSPORT_UNAVAILABLE,
        ExternalErrorCategory.REQUEST_TIMEOUT,
        ExternalErrorCategory.RATE_LIMITED,
        ExternalErrorCategory.UPSTREAM_SERVER_ERROR,
    }
)


@dataclass(frozen=True)
class EmbeddingBatchLimits:
    max_inputs: int = 512
    max_single_input_bytes: int = 32768
    max_single_input_tokens: int = 8000
    max_aggregate_bytes: int = 1000000
    max_aggregate_tokens: int = 250000

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")


@dataclass(frozen=True)
class EmbeddingRetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        if not math.isfinite(self.base_delay_seconds) or self.base_delay_seconds <= 0:
            raise ValueError("base_delay_seconds must be positive")
        if not math.isfinite(self.max_delay_seconds) or self.max_delay_seconds <= 0:
            raise ValueError("max_delay_seconds must be positive")


@dataclass(frozen=True)
class EmbeddingBatch:
    ordinal: int
    ordinals: Tuple[int, ...]
    inputs: Tuple[str, ...]
    aggregate_bytes: int
    aggregate_tokens: int

    @property
    def input_count(self) -> int:
        return len(self.inputs)


@dataclass(frozen=True)
class EmbeddingBatchPlan:
    batches: Tuple[EmbeddingBatch, ...]
    input_count: int
    aggregate_bytes: int
    aggregate_tokens: int
    tokenizer_version: str


@dataclass(frozen=True)
class EmbeddingExecutionResult:
    provider: str
    model: str
    dimensions: int
    embeddings: List[List[float]]
    token_input: Optional[int]
    batch_count: int
    retry_count: int


class EmbeddingBatchError(Exception):
    def __init__(
        self,
        *,
        reason: str,
        failure_reason: str = "EMBEDDING_PROVIDER_ERROR",
        input_ordinal: Optional[int] = None,
        batch_ordinal: Optional[int] = None,
    ) -> None:
        super().__init__("Embedding batch execution failed")
        self.reason = reason
        self.failure_reason = failure_reason
        self.input_ordinal = input_ordinal
        self.batch_ordinal = batch_ordinal


class EmbeddingBatchService:
    def __init__(
        self,
        *,
        embedding_client: EmbeddingClient,
        model: str,
        dimensions: int,
        limits: Optional[EmbeddingBatchLimits] = None,
        retry_policy: Optional[EmbeddingRetryPolicy] = None,
        token_counter: Optional[TokenCounter] = None,
        sleeper: Optional[AsyncSleeper] = None,
    ) -> None:
        normalized_model = model.strip()
        if not normalized_model:
            raise ValueError("model must not be empty")
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")

        capabilities = embedding_client.get_capabilities(
            model=normalized_model,
            dimensions=dimensions,
        )
        if capabilities is None:
            raise EmbeddingBatchError(reason="CAPABILITY_UNAVAILABLE")

        self._validate_capabilities(
            capabilities,
            provider=embedding_client.name,
            model=normalized_model,
            dimensions=dimensions,
        )
        selected_limits = limits or EmbeddingBatchLimits()
        self._validate_limits(selected_limits, capabilities)

        self._embedding_client = embedding_client
        self._model = normalized_model
        self._dimensions = dimensions
        self._capabilities = capabilities
        self._limits = selected_limits
        self._retry_policy = retry_policy or EmbeddingRetryPolicy()
        self._sleeper = sleeper or asyncio.sleep
        if token_counter is None:
            self._token_counter = estimate_embedding_tokens_safely
            self._tokenizer_version = TOKEN_SAFETY_ESTIMATOR_VERSION
        else:
            self._token_counter = token_counter
            self._tokenizer_version = "injected_token_counter"

    def plan(self, inputs: Sequence[str]) -> EmbeddingBatchPlan:
        if not inputs:
            raise EmbeddingBatchError(reason="EMPTY_INPUT_LIST")

        measured: List[Tuple[int, str, int, int]] = []
        for ordinal, value in enumerate(inputs):
            if not isinstance(value, str) or not value.strip():
                raise EmbeddingBatchError(
                    reason="EMPTY_INPUT",
                    input_ordinal=ordinal,
                )
            input_bytes = len(value.encode("utf-8"))
            input_tokens = self._token_counter(value)
            if input_tokens <= 0:
                raise EmbeddingBatchError(
                    reason="TOKEN_COUNT_INVALID",
                    input_ordinal=ordinal,
                )
            if input_bytes > self._limits.max_single_input_bytes:
                raise EmbeddingBatchError(
                    reason="SINGLE_INPUT_BYTES_EXCEEDED",
                    input_ordinal=ordinal,
                )
            if input_tokens > self._effective_single_token_limit:
                raise EmbeddingBatchError(
                    reason="SINGLE_INPUT_TOKENS_EXCEEDED",
                    input_ordinal=ordinal,
                )
            if input_bytes > self._limits.max_aggregate_bytes:
                raise EmbeddingBatchError(
                    reason="AGGREGATE_BYTES_EXCEEDED",
                    input_ordinal=ordinal,
                )
            if input_tokens > self._effective_aggregate_token_limit:
                raise EmbeddingBatchError(
                    reason="AGGREGATE_TOKENS_EXCEEDED",
                    input_ordinal=ordinal,
                )
            measured.append((ordinal, value, input_bytes, input_tokens))

        batches: List[EmbeddingBatch] = []
        current: List[Tuple[int, str, int, int]] = []
        current_bytes = 0
        current_tokens = 0
        for item in measured:
            _, _, item_bytes, item_tokens = item
            would_exceed = bool(current) and (
                len(current) + 1 > self._effective_input_limit
                or current_bytes + item_bytes > self._limits.max_aggregate_bytes
                or current_tokens + item_tokens > self._effective_aggregate_token_limit
            )
            if would_exceed:
                batches.append(self._build_batch(len(batches), current))
                current = []
                current_bytes = 0
                current_tokens = 0
            current.append(item)
            current_bytes += item_bytes
            current_tokens += item_tokens
        if current:
            batches.append(self._build_batch(len(batches), current))

        return EmbeddingBatchPlan(
            batches=tuple(batches),
            input_count=len(measured),
            aggregate_bytes=sum(item[2] for item in measured),
            aggregate_tokens=sum(item[3] for item in measured),
            tokenizer_version=self._tokenizer_version,
        )

    async def embed(
        self,
        inputs: Sequence[str],
        *,
        metadata: Optional[dict[str, object]] = None,
    ) -> EmbeddingExecutionResult:
        plan = self.plan(inputs)
        embeddings_by_ordinal: dict[int, List[float]] = {}
        retry_count = 0
        token_total = 0
        usage_complete = True

        for batch in plan.batches:
            response, retries = await self._execute_batch(
                batch,
                metadata=metadata,
            )
            retry_count += retries
            if retries:
                usage_complete = False
            self._validate_response(response, batch)
            for local_index, embedding in zip(
                response.indices or [],
                response.embeddings,
            ):
                original_ordinal = batch.ordinals[local_index]
                if original_ordinal in embeddings_by_ordinal:
                    raise EmbeddingBatchError(
                        reason="FINAL_RESPONSE_INDEX_DUPLICATE",
                        batch_ordinal=batch.ordinal,
                    )
                embeddings_by_ordinal[original_ordinal] = embedding
            if response.token_input is None:
                usage_complete = False
            else:
                token_total += response.token_input

        expected_ordinals = list(range(plan.input_count))
        if sorted(embeddings_by_ordinal) != expected_ordinals:
            raise EmbeddingBatchError(reason="FINAL_RESPONSE_COUNT_MISMATCH")
        ordered_embeddings = [
            embeddings_by_ordinal[ordinal] for ordinal in expected_ordinals
        ]

        return EmbeddingExecutionResult(
            provider=self._capabilities.provider,
            model=self._model,
            dimensions=self._dimensions,
            embeddings=ordered_embeddings,
            token_input=token_total if usage_complete else None,
            batch_count=len(plan.batches),
            retry_count=retry_count,
        )

    @property
    def _effective_input_limit(self) -> int:
        return min(self._limits.max_inputs, self._capabilities.max_input_count)

    @property
    def _effective_single_token_limit(self) -> int:
        return min(
            self._limits.max_single_input_tokens,
            self._capabilities.max_single_input_tokens,
        )

    @property
    def _effective_aggregate_token_limit(self) -> int:
        return min(
            self._limits.max_aggregate_tokens,
            self._capabilities.max_aggregate_tokens,
        )

    @staticmethod
    def _build_batch(
        ordinal: int,
        values: Sequence[Tuple[int, str, int, int]],
    ) -> EmbeddingBatch:
        return EmbeddingBatch(
            ordinal=ordinal,
            ordinals=tuple(item[0] for item in values),
            inputs=tuple(item[1] for item in values),
            aggregate_bytes=sum(item[2] for item in values),
            aggregate_tokens=sum(item[3] for item in values),
        )

    async def _execute_batch(
        self,
        batch: EmbeddingBatch,
        *,
        metadata: Optional[dict[str, object]],
    ) -> Tuple[EmbeddingResponse, int]:
        attempt = 0
        while True:
            attempt += 1
            try:
                response = await self._embedding_client.embed(
                    EmbeddingRequest(
                        inputs=list(batch.inputs),
                        model=self._model,
                        dimensions=self._dimensions,
                        metadata=metadata,
                    )
                )
                return response, attempt - 1
            except EmbeddingClientError as exc:
                if not self._should_retry(exc) or attempt >= self._retry_policy.max_attempts:
                    raise EmbeddingBatchError(
                        reason=(
                            "RETRY_EXHAUSTED"
                            if self._should_retry(exc)
                            else "PROVIDER_REQUEST_FAILED"
                        ),
                        batch_ordinal=batch.ordinal,
                    ) from None
                await self._sleeper(self._retry_delay(exc, attempt))

    def _should_retry(self, exc: EmbeddingClientError) -> bool:
        return bool(
            exc.retryable
            and exc.category in RETRYABLE_EMBEDDING_CATEGORIES
        )

    def _retry_delay(self, exc: EmbeddingClientError, attempt: int) -> float:
        backoff = self._retry_policy.base_delay_seconds * (2 ** (attempt - 1))
        retry_after = float(exc.retry_after_seconds or 0)
        return min(
            self._retry_policy.max_delay_seconds,
            max(backoff, retry_after),
        )

    def _validate_response(
        self,
        response: EmbeddingResponse,
        batch: EmbeddingBatch,
    ) -> None:
        if response.provider.strip().lower() != self._capabilities.provider.lower():
            raise EmbeddingBatchError(
                reason="RESPONSE_PROVIDER_MISMATCH",
                batch_ordinal=batch.ordinal,
            )
        if response.model.strip() != self._model:
            raise EmbeddingBatchError(
                reason="RESPONSE_MODEL_MISMATCH",
                batch_ordinal=batch.ordinal,
            )
        if len(response.embeddings) != batch.input_count:
            raise EmbeddingBatchError(
                reason="RESPONSE_COUNT_MISMATCH",
                batch_ordinal=batch.ordinal,
            )
        if response.indices is None:
            raise EmbeddingBatchError(
                reason="RESPONSE_INDEX_MISSING",
                batch_ordinal=batch.ordinal,
            )
        if len(response.indices) != batch.input_count:
            raise EmbeddingBatchError(
                reason="RESPONSE_COUNT_MISMATCH",
                batch_ordinal=batch.ordinal,
            )
        if len(set(response.indices)) != len(response.indices):
            raise EmbeddingBatchError(
                reason="RESPONSE_INDEX_DUPLICATE",
                batch_ordinal=batch.ordinal,
            )
        if any(index < 0 or index >= batch.input_count for index in response.indices):
            raise EmbeddingBatchError(
                reason="RESPONSE_INDEX_OUT_OF_RANGE",
                batch_ordinal=batch.ordinal,
            )
        if response.indices != list(range(batch.input_count)):
            raise EmbeddingBatchError(
                reason="RESPONSE_INDEX_ORDER_INVALID",
                batch_ordinal=batch.ordinal,
            )
        if any(len(embedding) != self._dimensions for embedding in response.embeddings):
            raise EmbeddingBatchError(
                reason="VECTOR_DIMENSION_MISMATCH",
                failure_reason="VECTOR_DIMENSION_MISMATCH",
                batch_ordinal=batch.ordinal,
            )

    @staticmethod
    def _validate_capabilities(
        capabilities: EmbeddingCapabilities,
        *,
        provider: str,
        model: str,
        dimensions: int,
    ) -> None:
        if (
            capabilities.provider.strip().lower() != provider.strip().lower()
            or capabilities.model.strip() != model
            or capabilities.dimensions != dimensions
        ):
            raise EmbeddingBatchError(reason="CAPABILITY_IDENTITY_MISMATCH")
        if any(
            value <= 0
            for value in (
                capabilities.dimensions,
                capabilities.max_input_count,
                capabilities.max_single_input_tokens,
                capabilities.max_aggregate_tokens,
            )
        ):
            raise EmbeddingBatchError(reason="CAPABILITY_INVALID")

    @staticmethod
    def _validate_limits(
        limits: EmbeddingBatchLimits,
        capabilities: EmbeddingCapabilities,
    ) -> None:
        if limits.max_inputs > capabilities.max_input_count:
            raise EmbeddingBatchError(reason="INPUT_LIMIT_EXCEEDS_CAPABILITY")
        if limits.max_single_input_tokens > capabilities.max_single_input_tokens:
            raise EmbeddingBatchError(reason="TOKEN_LIMIT_EXCEEDS_CAPABILITY")
        if limits.max_aggregate_tokens > capabilities.max_aggregate_tokens:
            raise EmbeddingBatchError(reason="TOKEN_LIMIT_EXCEEDS_CAPABILITY")


def estimate_embedding_tokens_safely(value: str) -> int:
    """Conservatively exceed the diagnostic bytes/4 estimate without I/O."""

    encoded_bytes = len(value.encode("utf-8"))
    if encoded_bytes == 0:
        return 0
    return max(len(value), math.ceil(encoded_bytes * 0.75))
