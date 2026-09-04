from __future__ import annotations

import asyncio
from typing import Callable, List, Optional, Union

import pytest

from src.observability.external_error import (
    ExternalErrorCategory,
    ExternalErrorDiagnostic,
)
from src.providers.embedding import (
    EmbeddingCapabilities,
    EmbeddingClient,
    EmbeddingClientError,
    EmbeddingRequest,
    EmbeddingResponse,
)
from src.services.embedding_batch_service import (
    EmbeddingBatchError,
    EmbeddingBatchLimits,
    EmbeddingBatchService,
    EmbeddingRetryPolicy,
    estimate_embedding_tokens_safely,
)


Outcome = Union[EmbeddingResponse, EmbeddingClientError]


class _RecordingEmbeddingClient(EmbeddingClient):
    def __init__(
        self,
        *,
        dimensions: int = 3,
        outcomes: Optional[List[Outcome]] = None,
        response_factory: Optional[
            Callable[[EmbeddingRequest], EmbeddingResponse]
        ] = None,
    ) -> None:
        self._dimensions = dimensions
        self._outcomes = list(outcomes or [])
        self._response_factory = response_factory
        self.requests: List[EmbeddingRequest] = []
        self.in_flight = 0
        self.max_in_flight = 0

    @property
    def name(self) -> str:
        return "openai"

    def get_capabilities(
        self,
        *,
        model: str,
        dimensions: int,
    ) -> Optional[EmbeddingCapabilities]:
        if model != "text-embedding-3-small" or dimensions != self._dimensions:
            return None
        return EmbeddingCapabilities(
            provider="openai",
            model=model,
            dimensions=dimensions,
            max_input_count=2048,
            max_single_input_tokens=8192,
            max_aggregate_tokens=300000,
            tokenizer_model=model,
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        self.requests.append(request)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(0)
        self.in_flight -= 1
        if self._outcomes:
            outcome = self._outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        if self._response_factory is not None:
            return self._response_factory(request)
        return _response_for(request, dimensions=self._dimensions)


def _response_for(
    request: EmbeddingRequest,
    *,
    dimensions: int = 3,
    token_input: Optional[int] = None,
    indices: Optional[List[int]] = None,
) -> EmbeddingResponse:
    return EmbeddingResponse(
        provider="openai",
        model=request.model or "text-embedding-3-small",
        embeddings=[
            [float(int(value.split("-")[-1]))] * dimensions
            for value in request.inputs
        ],
        indices=indices if indices is not None else list(range(len(request.inputs))),
        token_input=(len(request.inputs) if token_input is None else token_input),
    )


def _limits(**overrides: int) -> EmbeddingBatchLimits:
    values = {
        "max_inputs": 512,
        "max_single_input_bytes": 32768,
        "max_single_input_tokens": 8000,
        "max_aggregate_bytes": 1000000,
        "max_aggregate_tokens": 250000,
    }
    values.update(overrides)
    return EmbeddingBatchLimits(**values)


def _service(
    client: _RecordingEmbeddingClient,
    *,
    limits: Optional[EmbeddingBatchLimits] = None,
    token_counter: Optional[Callable[[str], int]] = None,
    retry_policy: Optional[EmbeddingRetryPolicy] = None,
    sleeper=None,
) -> EmbeddingBatchService:
    return EmbeddingBatchService(
        embedding_client=client,
        model="text-embedding-3-small",
        dimensions=client._dimensions,
        limits=limits or _limits(),
        retry_policy=retry_policy or EmbeddingRetryPolicy(),
        token_counter=token_counter or (lambda value: 1),
        sleeper=sleeper,
    )


def test_retry_policy_rejects_non_finite_delays() -> None:
    with pytest.raises(ValueError, match="base_delay_seconds must be positive"):
        EmbeddingRetryPolicy(base_delay_seconds=float("nan"))

    with pytest.raises(ValueError, match="max_delay_seconds must be positive"):
        EmbeddingRetryPolicy(max_delay_seconds=float("inf"))


@pytest.mark.parametrize(
    "capability",
    [
        EmbeddingCapabilities(
            provider="different",
            model="text-embedding-3-small",
            dimensions=3,
            max_input_count=2048,
            max_single_input_tokens=8192,
            max_aggregate_tokens=300000,
            tokenizer_model="text-embedding-3-small",
        ),
        EmbeddingCapabilities(
            provider="openai",
            model="different",
            dimensions=3,
            max_input_count=2048,
            max_single_input_tokens=8192,
            max_aggregate_tokens=300000,
            tokenizer_model="text-embedding-3-small",
        ),
        EmbeddingCapabilities(
            provider="openai",
            model="text-embedding-3-small",
            dimensions=2,
            max_input_count=2048,
            max_single_input_tokens=8192,
            max_aggregate_tokens=300000,
            tokenizer_model="text-embedding-3-small",
        ),
    ],
)
def test_capability_identity_must_match_selected_execution(
    capability: EmbeddingCapabilities,
) -> None:
    client = _RecordingEmbeddingClient()
    client.get_capabilities = lambda **_: capability  # type: ignore[method-assign]

    with pytest.raises(EmbeddingBatchError) as exc_info:
        _service(client)

    assert exc_info.value.reason == "CAPABILITY_IDENTITY_MISMATCH"


def test_plans_2483_inputs_into_five_stable_count_bounded_batches() -> None:
    service = _service(_RecordingEmbeddingClient())

    plan = service.plan([f"item-{index}" for index in range(2483)])

    assert [batch.input_count for batch in plan.batches] == [512, 512, 512, 512, 435]
    assert plan.batches[0].ordinals == tuple(range(512))
    assert plan.batches[-1].ordinals == tuple(range(2048, 2483))
    assert [ordinal for batch in plan.batches for ordinal in batch.ordinals] == list(
        range(2483)
    )


def test_default_token_safety_estimator_is_more_conservative_than_bytes_div_four() -> None:
    assert estimate_embedding_tokens_safely("abcdefgh") == 8
    assert estimate_embedding_tokens_safely("學習") == 5
    assert estimate_embedding_tokens_safely("🙂") == 3


@pytest.mark.parametrize(
    ("limits", "reason"),
    [
        (_limits(max_inputs=2049), "INPUT_LIMIT_EXCEEDS_CAPABILITY"),
        (_limits(max_single_input_tokens=8193), "TOKEN_LIMIT_EXCEEDS_CAPABILITY"),
        (_limits(max_aggregate_tokens=300001), "TOKEN_LIMIT_EXCEEDS_CAPABILITY"),
    ],
)
def test_operational_limits_cannot_expand_provider_capability(
    limits: EmbeddingBatchLimits,
    reason: str,
) -> None:
    with pytest.raises(EmbeddingBatchError) as exc_info:
        _service(_RecordingEmbeddingClient(), limits=limits)

    assert exc_info.value.reason == reason


def test_planner_enforces_count_token_and_byte_boundaries_together() -> None:
    token_counts = {"aa": 2, "bbbb": 4, "ccc": 3, "d": 1}
    service = _service(
        _RecordingEmbeddingClient(),
        limits=_limits(
            max_inputs=3,
            max_single_input_bytes=10,
            max_single_input_tokens=10,
            max_aggregate_bytes=6,
            max_aggregate_tokens=6,
        ),
        token_counter=token_counts.__getitem__,
    )

    plan = service.plan(["aa", "bbbb", "ccc", "d"])

    assert [batch.ordinals for batch in plan.batches] == [(0, 1), (2, 3)]
    assert [batch.aggregate_bytes for batch in plan.batches] == [6, 4]
    assert [batch.aggregate_tokens for batch in plan.batches] == [6, 4]


@pytest.mark.parametrize(
    ("limits", "token_count", "reason"),
    [
        (_limits(max_single_input_bytes=3), 1, "SINGLE_INPUT_BYTES_EXCEEDED"),
        (_limits(max_single_input_tokens=3), 4, "SINGLE_INPUT_TOKENS_EXCEEDED"),
    ],
)
def test_planner_rejects_oversized_single_input_before_any_request(
    limits: EmbeddingBatchLimits,
    token_count: int,
    reason: str,
) -> None:
    client = _RecordingEmbeddingClient()
    service = _service(client, limits=limits, token_counter=lambda value: token_count)

    with pytest.raises(EmbeddingBatchError) as exc_info:
        service.plan(["item-0"])

    assert exc_info.value.reason == reason
    assert exc_info.value.input_ordinal == 0
    assert client.requests == []


@pytest.mark.parametrize(
    ("limits", "token_count", "reason"),
    [
        (
            _limits(max_single_input_bytes=10, max_aggregate_bytes=3),
            1,
            "AGGREGATE_BYTES_EXCEEDED",
        ),
        (
            _limits(
                max_single_input_tokens=10,
                max_aggregate_tokens=3,
            ),
            4,
            "AGGREGATE_TOKENS_EXCEEDED",
        ),
    ],
)
def test_planner_rejects_input_that_cannot_fit_an_empty_batch(
    limits: EmbeddingBatchLimits,
    token_count: int,
    reason: str,
) -> None:
    client = _RecordingEmbeddingClient()
    service = _service(client, limits=limits, token_counter=lambda value: token_count)

    with pytest.raises(EmbeddingBatchError) as exc_info:
        service.plan(["four"])

    assert exc_info.value.reason == reason
    assert exc_info.value.input_ordinal == 0
    assert client.requests == []


def test_service_executes_sequentially_and_restores_original_order() -> None:
    client = _RecordingEmbeddingClient()
    service = _service(client, limits=_limits(max_inputs=2))

    result = asyncio.run(service.embed([f"item-{index}" for index in range(5)]))

    assert [len(request.inputs) for request in client.requests] == [2, 2, 1]
    assert client.max_in_flight == 1
    assert [embedding[0] for embedding in result.embeddings] == [0, 1, 2, 3, 4]
    assert result.batch_count == 3


@pytest.mark.parametrize(
    ("indices", "reason"),
    [
        ([0, 0], "RESPONSE_INDEX_DUPLICATE"),
        ([0, 2], "RESPONSE_INDEX_OUT_OF_RANGE"),
        ([1, 0], "RESPONSE_INDEX_ORDER_INVALID"),
        ([0], "RESPONSE_COUNT_MISMATCH"),
        (None, "RESPONSE_INDEX_MISSING"),
    ],
)
def test_service_rejects_invalid_response_indices(
    indices: Optional[List[int]],
    reason: str,
) -> None:
    def response_factory(request: EmbeddingRequest) -> EmbeddingResponse:
        response = _response_for(request)
        if indices is None:
            response.indices = None
        else:
            response.indices = indices
            response.embeddings = response.embeddings[: len(indices)]
        return response

    service = _service(
        _RecordingEmbeddingClient(response_factory=response_factory),
        limits=_limits(max_inputs=2),
    )

    with pytest.raises(EmbeddingBatchError) as exc_info:
        asyncio.run(service.embed(["item-0", "item-1"]))

    assert exc_info.value.reason == reason


def test_service_rejects_response_dimension_mismatch() -> None:
    response = EmbeddingResponse(
        provider="openai",
        model="text-embedding-3-small",
        embeddings=[[0.0, 0.0]],
        indices=[0],
        token_input=1,
    )
    service = _service(_RecordingEmbeddingClient(outcomes=[response]))

    with pytest.raises(EmbeddingBatchError) as exc_info:
        asyncio.run(service.embed(["item-0"]))

    assert exc_info.value.reason == "VECTOR_DIMENSION_MISMATCH"
    assert exc_info.value.failure_reason == "VECTOR_DIMENSION_MISMATCH"


def _embedding_error(
    category: ExternalErrorCategory,
    *,
    retryable: bool,
    retry_after_seconds: Optional[int] = None,
) -> EmbeddingClientError:
    return EmbeddingClientError(
        diagnostic=ExternalErrorDiagnostic(
            category=category,
            retryable=retryable,
            retry_after_seconds=retry_after_seconds,
        )
    )


def test_retry_repeats_only_current_batch_and_caps_retry_after() -> None:
    first = EmbeddingResponse(
        provider="openai",
        model="text-embedding-3-small",
        embeddings=[[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]],
        indices=[0, 1],
        token_input=2,
    )
    retryable = _embedding_error(
        ExternalErrorCategory.RATE_LIMITED,
        retryable=True,
        retry_after_seconds=120,
    )
    second = EmbeddingResponse(
        provider="openai",
        model="text-embedding-3-small",
        embeddings=[[2.0, 2.0, 2.0], [3.0, 3.0, 3.0]],
        indices=[0, 1],
        token_input=2,
    )
    sleeps: List[float] = []

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    client = _RecordingEmbeddingClient(outcomes=[first, retryable, second])
    service = _service(
        client,
        limits=_limits(max_inputs=2),
        retry_policy=EmbeddingRetryPolicy(
            max_attempts=3,
            base_delay_seconds=1,
            max_delay_seconds=30,
        ),
        sleeper=sleeper,
    )

    result = asyncio.run(service.embed([f"item-{index}" for index in range(4)]))

    assert [request.inputs for request in client.requests] == [
        ["item-0", "item-1"],
        ["item-2", "item-3"],
        ["item-2", "item-3"],
    ]
    assert sleeps == [30]
    assert result.retry_count == 1
    assert result.token_input is None


@pytest.mark.parametrize(
    "category",
    [
        ExternalErrorCategory.REQUEST_INVALID,
        ExternalErrorCategory.AUTHENTICATION_FAILED,
        ExternalErrorCategory.REQUEST_TOO_LARGE,
        ExternalErrorCategory.VALIDATION_FAILED,
        ExternalErrorCategory.RESPONSE_INVALID,
    ],
)
def test_non_retryable_errors_are_attempted_once(
    category: ExternalErrorCategory,
) -> None:
    error = _embedding_error(category, retryable=False)
    client = _RecordingEmbeddingClient(outcomes=[error])
    sleeps: List[float] = []

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    service = _service(client, sleeper=sleeper)

    with pytest.raises(EmbeddingBatchError) as exc_info:
        asyncio.run(service.embed(["item-0"]))

    assert exc_info.value.reason == "PROVIDER_REQUEST_FAILED"
    assert len(client.requests) == 1
    assert sleeps == []


def test_usage_is_aggregated_only_when_every_batch_reports_it() -> None:
    known_client = _RecordingEmbeddingClient()
    known = asyncio.run(
        _service(known_client, limits=_limits(max_inputs=2)).embed(
            ["item-0", "item-1", "item-2"]
        )
    )
    assert known.token_input == 3

    missing_usage = EmbeddingResponse(
        provider="openai",
        model="text-embedding-3-small",
        embeddings=[[2.0, 2.0, 2.0]],
        indices=[0],
        token_input=None,
    )
    client = _RecordingEmbeddingClient(
        outcomes=[
            EmbeddingResponse(
                provider="openai",
                model="text-embedding-3-small",
                embeddings=[[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]],
                indices=[0, 1],
                token_input=2,
            ),
            missing_usage,
        ]
    )
    unknown = asyncio.run(
        _service(client, limits=_limits(max_inputs=2)).embed(
            ["item-0", "item-1", "item-2"]
        )
    )

    assert unknown.token_input is None
