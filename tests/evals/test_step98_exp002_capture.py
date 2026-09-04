from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.observability.external_error import ExternalErrorCategory, ExternalErrorDiagnostic
from src.providers.embedding import (
    EmbeddingCapabilities,
    EmbeddingClient,
    EmbeddingClientError,
    EmbeddingRequest,
    EmbeddingResponse,
)
from src.services.embedding_batch_service import EmbeddingRetryPolicy

from .context_aware_embedding_input_eval import CapturePlan, CaptureRequest, Step98ContractError
from .step98_phase_b_capture_v2 import (
    CaptureFailure,
    GlobalAttemptBudget,
    execute_logical_requests,
    validate_preflight,
    write_failure_receipt,
)
from scripts.generate_step98_exp002_preregistration import generate
from .step98_experiment_v2 import load_preregistration


class ScriptedEmbeddingClient(EmbeddingClient):
    def __init__(self, *, fail_calls: set[int], delay_seconds: float = 0.0) -> None:
        self.fail_calls = fail_calls
        self.delay_seconds = delay_seconds
        self.call_count = 0

    @property
    def name(self) -> str:
        return "openai"

    def get_capabilities(self, *, model: str, dimensions: int) -> EmbeddingCapabilities:
        return EmbeddingCapabilities(
            provider="openai",
            model=model,
            dimensions=dimensions,
            max_input_count=32,
            max_single_input_tokens=8000,
            max_aggregate_tokens=250000,
            tokenizer_model=model,
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        self.call_count += 1
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.call_count in self.fail_calls:
            raise EmbeddingClientError(
                diagnostic=ExternalErrorDiagnostic(
                    category=ExternalErrorCategory.RATE_LIMITED,
                    retryable=True,
                )
            )
        return EmbeddingResponse(
            provider="openai",
            model=request.model or "text-embedding-3-small",
            embeddings=[[1.0] for _ in request.inputs],
            indices=list(range(len(request.inputs))),
            token_input=len(request.inputs),
        )


@pytest.mark.parametrize(
    ("fail_calls", "expected_attempts", "expected_retries"),
    [
        (set(), 15, 0),
        ({1}, 16, 1),
    ],
)
def test_global_attempt_budget_counts_success_and_retry(
    tmp_path: Path,
    fail_calls: set[int],
    expected_attempts: int,
    expected_retries: int,
) -> None:
    plan = _plan()
    client = ScriptedEmbeddingClient(fail_calls=fail_calls)
    budget = GlobalAttemptBudget(tmp_path / "run-state", max_external_attempts=16)

    result = asyncio.run(
        execute_logical_requests(
            plan=plan,
            inputs_by_request={request.ordinal: ("public input",) for request in plan.requests},
            embedding_client=client,
            attempt_budget=budget,
            retry_policy=EmbeddingRetryPolicy(max_attempts=3, base_delay_seconds=0.001),
        )
    )

    assert result.actual_external_attempt_count == expected_attempts
    assert result.retry_count == expected_retries
    assert result.completed_logical_request_count == 15
    assert client.call_count == expected_attempts


def test_seventeenth_attempt_is_rejected_before_provider_call(tmp_path: Path) -> None:
    plan = _plan()
    client = ScriptedEmbeddingClient(fail_calls={1, 3})
    budget = GlobalAttemptBudget(tmp_path / "run-state", max_external_attempts=16)

    with pytest.raises(CaptureFailure) as raised:
        asyncio.run(
            execute_logical_requests(
                plan=plan,
                inputs_by_request={request.ordinal: ("public input",) for request in plan.requests},
                embedding_client=client,
                attempt_budget=budget,
                retry_policy=EmbeddingRetryPolicy(max_attempts=3, base_delay_seconds=0.001),
            )
        )

    failure = raised.value
    assert failure.safe_failure_category == "external_attempt_budget_exhausted"
    assert failure.actual_external_attempt_count == 16
    assert failure.completed_logical_request_count == 14
    assert failure.failed_logical_request_ordinal == 14
    assert client.call_count == 16


def test_duration_budget_rejects_request_before_provider_call(tmp_path: Path) -> None:
    plan = _plan()
    client = ScriptedEmbeddingClient(fail_calls=set())
    budget = GlobalAttemptBudget(tmp_path / "run-state", max_external_attempts=16)

    with pytest.raises(CaptureFailure) as raised:
        asyncio.run(
            execute_logical_requests(
                plan=plan,
                inputs_by_request={request.ordinal: ("public input",) for request in plan.requests},
                embedding_client=client,
                attempt_budget=budget,
                deadline_monotonic=time.monotonic() + 1.0,
                provider_timeout_seconds=60.0,
                finalize_reserve_seconds=5.0,
            )
        )

    assert raised.value.safe_failure_category == "duration_budget_exhausted"
    assert raised.value.actual_external_attempt_count == 0
    assert client.call_count == 0


def test_provider_attempt_timeout_is_enforced(tmp_path: Path) -> None:
    plan = _plan()
    client = ScriptedEmbeddingClient(fail_calls=set(), delay_seconds=0.1)
    budget = GlobalAttemptBudget(tmp_path / "run-state", max_external_attempts=16)

    with pytest.raises(CaptureFailure) as raised:
        asyncio.run(
            execute_logical_requests(
                plan=plan,
                inputs_by_request={request.ordinal: ("public input",) for request in plan.requests},
                embedding_client=client,
                attempt_budget=budget,
                deadline_monotonic=time.monotonic() + 10.0,
                provider_timeout_seconds=0.01,
            )
        )

    assert raised.value.safe_failure_category == "provider_request_timeout"
    assert raised.value.actual_external_attempt_count == 1
    assert client.call_count == 1


def test_budget_exhaustion_writes_safe_immutable_failure_receipt(tmp_path: Path) -> None:
    run_dir = tmp_path / "step98-exp-002-capture-001"
    run_dir.mkdir()
    failure = CaptureFailure(
        safe_failure_category="external_attempt_budget_exhausted",
        failed_logical_request_ordinal=14,
        failed_variant="body_only_v1",
        failed_batch_ordinal=3,
        completed_logical_request_count=14,
        actual_external_attempt_count=16,
        retry_count=2,
    )

    receipt = write_failure_receipt(
        run_dir=run_dir,
        experiment_id="step98-exp-002",
        capture_run_id="step98-exp-002-capture-001",
        manifest_digest="manifest",
        request_plan_digest="plan",
        capture_source_digest="source",
        failure=failure,
        duration_seconds=1.25,
        timestamp="2026-08-06T00:00:00Z",
    )

    assert receipt["status"] == "failed"
    assert receipt["vectors_artifact_created"] is False
    assert not (run_dir / "vectors.json").exists()
    with pytest.raises(FileExistsError):
        write_failure_receipt(
            run_dir=run_dir,
            experiment_id="step98-exp-002",
            capture_run_id="step98-exp-002-capture-001",
            manifest_digest="manifest",
            request_plan_digest="plan",
            capture_source_digest="source",
            failure=failure,
            duration_seconds=1.25,
            timestamp="2026-08-06T00:00:00Z",
        )


def _generated_fixture(tmp_path: Path) -> Path:
    fixture_dir = tmp_path / "fixture"
    generate(fixture_dir)
    load_preregistration(fixture_dir, create_receipt=True)
    return fixture_dir


def test_preflight_missing_credential_fails_before_external_attempt(tmp_path: Path) -> None:
    fixture_dir = _generated_fixture(tmp_path)
    output_root = tmp_path / "dev_state" / "artifacts" / "step_98"

    with pytest.raises(Step98ContractError, match="required credential missing"):
        validate_preflight(
            fixture_dir=fixture_dir,
            output_root=output_root,
            capture_run_id="step98-exp-002-capture-001",
            credential="",
            repo_root=tmp_path,
        )

    assert not output_root.exists()


def test_preflight_rejects_non_gitignored_output_before_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_dir = _generated_fixture(tmp_path)
    output_root = tmp_path / "dev_state" / "artifacts" / "step_98"
    monkeypatch.setattr(
        "tests.evals.step98_phase_b_capture_v2.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=""),
    )

    with pytest.raises(Step98ContractError, match="output root is not gitignored"):
        validate_preflight(
            fixture_dir=fixture_dir,
            output_root=output_root,
            capture_run_id="step98-exp-002-capture-001",
            credential="present",
            repo_root=tmp_path,
        )

    assert not output_root.exists()


def test_preflight_rejects_existing_experiment_artifact_before_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_dir = _generated_fixture(tmp_path)
    output_root = tmp_path / "dev_state" / "artifacts" / "step_98"
    output_root.mkdir(parents=True)
    (output_root / "step98-exp-002-result.json").write_text("reserved\n", encoding="utf-8")
    monkeypatch.setattr(
        "tests.evals.step98_phase_b_capture_v2.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=""),
    )

    with pytest.raises(Step98ContractError, match="canonical capture or result already exists"):
        validate_preflight(
            fixture_dir=fixture_dir,
            output_root=output_root,
            capture_run_id="step98-exp-002-capture-001",
            credential="present",
            repo_root=tmp_path,
        )


def _plan() -> CapturePlan:
    requests = tuple(
        CaptureRequest(
            ordinal=index,
            role="query",
            variant_id="query_body_only_v1",
            batch_ordinal=index,
            item_ids=(f"q-{index}",),
            input_digests=(f"digest-{index}",),
        )
        for index in range(15)
    )
    return CapturePlan(
        experiment_id="step98-exp-002",
        manifest_digest="manifest",
        provider="openai",
        model="text-embedding-3-small",
        dimensions=1,
        query_builder_version="query_body_only_v1",
        requests=requests,
        request_plan_digest="plan",
    )
