from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.providers.embedding import (
    EmbeddingCapabilities,
    EmbeddingClient,
    EmbeddingRequest,
    EmbeddingResponse,
    OpenAIEmbeddingClient,
)
from src.services.cost_tracker import CostTracker
from src.services.embedding_batch_service import (
    EmbeddingBatchError,
    EmbeddingBatchLimits,
    EmbeddingBatchService,
    EmbeddingRetryPolicy,
    estimate_embedding_tokens_safely,
)

try:
    from .context_aware_embedding_input_eval import CapturePlan, CaptureRequest, Step98ContractError, VARIANTS, canonical_digest, file_digest
    from .step98_experiment_v2 import DEFAULT_FIXTURE_DIR, EXPERIMENT_ID, IMPLEMENTATION_SOURCE_ROLES, implementation_bundle_digest, load_preregistration, materialize_capture_inputs, plan_capture
except ImportError:
    from context_aware_embedding_input_eval import CapturePlan, CaptureRequest, Step98ContractError, VARIANTS, canonical_digest, file_digest  # type: ignore[no-redef]
    from step98_experiment_v2 import DEFAULT_FIXTURE_DIR, EXPERIMENT_ID, IMPLEMENTATION_SOURCE_ROLES, implementation_bundle_digest, load_preregistration, materialize_capture_inputs, plan_capture  # type: ignore[no-redef]


LIVE_ENV = "LEARNLOOP_STEP98_LIVE_CAPTURE"
APPROVAL_TEXT = "I_APPROVE_STEP98_PUBLIC_SAFE_CAPTURE"


class AttemptBudgetExhausted(Exception):
    pass


class CaptureDurationBudgetExhausted(Exception):
    pass


class ProviderAttemptTimeout(Exception):
    pass


class CaptureFailure(Exception):
    def __init__(
        self,
        *,
        safe_failure_category: str,
        failed_logical_request_ordinal: Optional[int],
        failed_variant: Optional[str],
        failed_batch_ordinal: Optional[int],
        completed_logical_request_count: int,
        actual_external_attempt_count: int,
        retry_count: int,
    ) -> None:
        super().__init__("Step 98 capture failed")
        self.safe_failure_category = safe_failure_category
        self.failed_logical_request_ordinal = failed_logical_request_ordinal
        self.failed_variant = failed_variant
        self.failed_batch_ordinal = failed_batch_ordinal
        self.completed_logical_request_count = completed_logical_request_count
        self.actual_external_attempt_count = actual_external_attempt_count
        self.retry_count = retry_count


@dataclass(frozen=True)
class RequestExecution:
    logical_request_ordinal: int
    role: str
    variant: str
    batch_ordinal: int
    input_count: int
    attempt_count: int
    provider_reported_provider: str
    provider_reported_model: str
    provider_reported_dimensions: int


@dataclass(frozen=True)
class CaptureExecutionResult:
    completed_logical_request_count: int
    actual_external_attempt_count: int
    retry_count: int
    token_input: Optional[int]
    query_vectors: Dict[str, List[float]]
    document_vectors: Dict[str, Dict[str, List[float]]]
    requests: Tuple[RequestExecution, ...]


@dataclass(frozen=True)
class CapturePreflight:
    estimated_token_bound: int
    estimated_cost_bound_usd: float


class GlobalAttemptBudget:
    def __init__(self, state_dir: Path, *, max_external_attempts: int) -> None:
        if max_external_attempts <= 0:
            raise ValueError("max_external_attempts must be positive")
        state_dir.mkdir(parents=True, exist_ok=False)
        self._state_dir = state_dir
        self._max_external_attempts = max_external_attempts
        self._count = 0
        self._lock: Optional[asyncio.Lock] = None

    @property
    def count(self) -> int:
        return self._count

    async def acquire(self, request: CaptureRequest) -> int:
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._count >= self._max_external_attempts:
                raise AttemptBudgetExhausted
            self._count += 1
            attempt = self._count
            marker = {
                "attempt_ordinal": attempt,
                "logical_request_ordinal": request.ordinal,
                "variant": request.variant_id,
                "batch_ordinal": request.batch_ordinal,
            }
            _write_json_create_only(
                self._state_dir / f"attempt-{attempt:03d}.json",
                marker,
            )
            return attempt


class BudgetedEmbeddingClient(EmbeddingClient):
    def __init__(
        self,
        delegate: EmbeddingClient,
        budget: GlobalAttemptBudget,
        *,
        deadline_monotonic: Optional[float] = None,
        provider_timeout_seconds: float = 0.0,
        finalize_reserve_seconds: float = 0.0,
    ) -> None:
        self._delegate = delegate
        self._budget = budget
        self._deadline_monotonic = deadline_monotonic
        self._provider_timeout_seconds = provider_timeout_seconds
        self._finalize_reserve_seconds = finalize_reserve_seconds
        self._request: Optional[CaptureRequest] = None
        self.attempts_by_request: Dict[int, int] = {}
        self.responses_by_request: Dict[int, List[Tuple[str, str, int]]] = {}

    @property
    def name(self) -> str:
        return self._delegate.name

    def get_capabilities(self, *, model: str, dimensions: int) -> Optional[EmbeddingCapabilities]:
        return self._delegate.get_capabilities(model=model, dimensions=dimensions)

    def select_request(self, request: CaptureRequest) -> None:
        self._request = request

    @property
    def external_attempt_count(self) -> int:
        return self._budget.count

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        if self._request is None:
            raise Step98ContractError("logical request context missing")
        if self._deadline_monotonic is not None:
            required = self._provider_timeout_seconds + self._finalize_reserve_seconds
            if self._deadline_monotonic - time.monotonic() < required:
                raise CaptureDurationBudgetExhausted
        await self._budget.acquire(self._request)
        ordinal = self._request.ordinal
        self.attempts_by_request[ordinal] = self.attempts_by_request.get(ordinal, 0) + 1
        try:
            if self._provider_timeout_seconds > 0:
                response = await asyncio.wait_for(
                    self._delegate.embed(request),
                    timeout=self._provider_timeout_seconds,
                )
            else:
                response = await self._delegate.embed(request)
        except asyncio.TimeoutError:
            raise ProviderAttemptTimeout from None
        dimensions = len(response.embeddings[0]) if response.embeddings else 0
        self.responses_by_request.setdefault(ordinal, []).append(
            (response.provider, response.model, dimensions)
        )
        return response


async def execute_logical_requests(
    *,
    plan: CapturePlan,
    inputs_by_request: Mapping[int, Sequence[str]],
    embedding_client: EmbeddingClient,
    attempt_budget: GlobalAttemptBudget,
    retry_policy: Optional[EmbeddingRetryPolicy] = None,
    deadline_monotonic: Optional[float] = None,
    provider_timeout_seconds: float = 0.0,
    finalize_reserve_seconds: float = 0.0,
) -> CaptureExecutionResult:
    budgeted = BudgetedEmbeddingClient(
        embedding_client,
        attempt_budget,
        deadline_monotonic=deadline_monotonic,
        provider_timeout_seconds=provider_timeout_seconds,
        finalize_reserve_seconds=finalize_reserve_seconds,
    )
    service = EmbeddingBatchService(
        embedding_client=budgeted,
        model=plan.model,
        dimensions=plan.dimensions,
        limits=EmbeddingBatchLimits(max_inputs=32),
        retry_policy=retry_policy,
    )
    query_vectors: Dict[str, List[float]] = {}
    document_vectors: Dict[str, Dict[str, List[float]]] = {variant: {} for variant in VARIANTS}
    request_evidence: List[RequestExecution] = []
    completed = 0
    token_input = 0
    usage_complete = True
    current: Optional[CaptureRequest] = None
    try:
        for current in plan.requests:
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                raise CaptureFailure(
                    safe_failure_category="duration_budget_exhausted",
                    failed_logical_request_ordinal=current.ordinal,
                    failed_variant=current.variant_id,
                    failed_batch_ordinal=current.batch_ordinal,
                    completed_logical_request_count=completed,
                    actual_external_attempt_count=budgeted.external_attempt_count,
                    retry_count=_retry_count(budgeted.attempts_by_request),
                )
            budgeted.select_request(current)
            if deadline_monotonic is None:
                result = await service.embed(inputs_by_request[current.ordinal])
            else:
                remaining = deadline_monotonic - time.monotonic() - finalize_reserve_seconds
                if remaining <= 0:
                    raise CaptureDurationBudgetExhausted
                result = await asyncio.wait_for(
                    service.embed(inputs_by_request[current.ordinal]),
                    timeout=remaining,
                )
            if result.batch_count != 1:
                raise Step98ContractError("logical request was split after preregistration")
            identities = budgeted.responses_by_request.get(current.ordinal, [])
            if not identities or len(set(identities)) != 1:
                raise Step98ContractError("provider identity changed within logical request")
            provider, model, dimensions = identities[0]
            target = query_vectors if current.role == "query" else document_vectors[current.variant_id]
            for item_id, vector in zip(current.item_ids, result.embeddings):
                target[item_id] = vector
            if result.token_input is None:
                usage_complete = False
            else:
                token_input += result.token_input
            request_evidence.append(
                RequestExecution(
                    logical_request_ordinal=current.ordinal,
                    role=current.role,
                    variant=current.variant_id,
                    batch_ordinal=current.batch_ordinal,
                    input_count=len(current.item_ids),
                    attempt_count=budgeted.attempts_by_request[current.ordinal],
                    provider_reported_provider=provider,
                    provider_reported_model=model,
                    provider_reported_dimensions=dimensions,
                )
            )
            completed += 1
    except AttemptBudgetExhausted:
        raise _capture_failure("external_attempt_budget_exhausted", current, completed, budgeted) from None
    except (CaptureDurationBudgetExhausted, asyncio.TimeoutError):
        raise _capture_failure("duration_budget_exhausted", current, completed, budgeted) from None
    except ProviderAttemptTimeout:
        raise _capture_failure("provider_request_timeout", current, completed, budgeted) from None
    except EmbeddingBatchError as exc:
        raise _capture_failure(_safe_batch_category(exc), current, completed, budgeted) from None
    except Step98ContractError:
        raise _capture_failure("capture_contract_mismatch", current, completed, budgeted) from None
    except CaptureFailure:
        raise
    except Exception:
        raise _capture_failure("unexpected_capture_failure", current, completed, budgeted) from None
    return CaptureExecutionResult(
        completed_logical_request_count=completed,
        actual_external_attempt_count=attempt_budget.count,
        retry_count=_retry_count(budgeted.attempts_by_request),
        token_input=token_input if usage_complete else None,
        query_vectors=query_vectors,
        document_vectors=document_vectors,
        requests=tuple(request_evidence),
    )


def validate_preflight(
    *,
    fixture_dir: Path,
    output_root: Path,
    capture_run_id: str,
    credential: str,
    repo_root: Path = _REPO_ROOT,
) -> Tuple[Any, CapturePlan, Dict[int, Tuple[str, ...]], CapturePreflight]:
    preregistration = load_preregistration(fixture_dir)
    plan = plan_capture(preregistration)
    capture = preregistration.manifest["capture"]
    if plan.request_plan_digest != capture["request_plan_digest"]:
        raise Step98ContractError("request plan digest mismatch")
    if len(plan.requests) != int(capture["logical_request_count"]):
        raise Step98ContractError("logical request count mismatch")
    if capture_run_id != f"{EXPERIMENT_ID}-capture-001":
        raise Step98ContractError("capture run id mismatch")
    expected_output_root = repo_root / capture["output_root"]
    if output_root.resolve() != expected_output_root.resolve():
        raise Step98ContractError("capture output root mismatch")
    if not credential.strip():
        raise Step98ContractError("required credential missing")
    if (output_root / capture_run_id).exists():
        raise Step98ContractError("capture run directory already exists")
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", str(output_root)],
        cwd=repo_root,
        check=False,
    )
    if ignored.returncode != 0:
        raise Step98ContractError("output root is not gitignored")
    if output_root.exists() and any(EXPERIMENT_ID in path.name for path in output_root.iterdir()):
        raise Step98ContractError("canonical capture or result already exists")
    managed_paths = [
        preregistration.manifest["implementation"][f"{role}_source_path"]
        for role in IMPLEMENTATION_SOURCE_ROLES
    ] + [dependency["path"] for dependency in preregistration.manifest["implementation_dependencies"]] + [str(fixture_dir.relative_to(repo_root))]
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all", "--", *managed_paths],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    if status.stdout.strip():
        raise Step98ContractError("managed capture sources are not committed and clean")

    inputs = materialize_capture_inputs(preregistration, plan)
    total_inputs = sum(len(request.item_ids) for request in plan.requests)
    if total_inputs != int(capture["total_inputs"]) or total_inputs > int(capture["max_inputs"]):
        raise Step98ContractError("capture input count mismatch")
    request_token_estimates = [
        sum(estimate_embedding_tokens_safely(value) for value in inputs[request.ordinal])
        for request in plan.requests
    ]
    retry_slots = int(capture["max_external_attempts"]) - len(plan.requests)
    estimated_token_bound = sum(request_token_estimates) + max(request_token_estimates) * retry_slots
    if estimated_token_bound > int(capture["max_estimated_tokens"]):
        raise Step98ContractError("capture estimated-token budget exceeded")
    estimated_cost = CostTracker().estimate_embedding_cost(
        provider_name=plan.provider,
        model=plan.model,
        token_input=estimated_token_bound,
    )
    if estimated_cost is None or estimated_cost > float(capture["max_cost_usd"]):
        raise Step98ContractError("capture cost budget exceeded")
    if int(capture["concurrency"]) != 1 or capture["schedule"] != "query_once_then_document_round_robin_v1":
        raise Step98ContractError("capture execution order mismatch")
    return preregistration, plan, inputs, CapturePreflight(estimated_token_bound, estimated_cost)


def write_failure_receipt(
    *,
    run_dir: Path,
    experiment_id: str,
    capture_run_id: str,
    manifest_digest: str,
    request_plan_digest: str,
    capture_source_digest: str,
    implementation_source_digest: Optional[str] = None,
    failure: CaptureFailure,
    duration_seconds: float,
    timestamp: str,
) -> Dict[str, Any]:
    receipt = {
        "status": "failed",
        "experiment_id": experiment_id,
        "capture_run_id": capture_run_id,
        "manifest_digest": manifest_digest,
        "request_plan_digest": request_plan_digest,
        "capture_source_digest": capture_source_digest,
        "implementation_source_digest": implementation_source_digest or capture_source_digest,
        "failed_logical_request_ordinal": failure.failed_logical_request_ordinal,
        "failed_variant": failure.failed_variant,
        "failed_batch_ordinal": failure.failed_batch_ordinal,
        "completed_logical_request_count": failure.completed_logical_request_count,
        "actual_external_attempt_count": failure.actual_external_attempt_count,
        "retry_count": failure.retry_count,
        "safe_failure_category": failure.safe_failure_category,
        "vectors_artifact_created": False,
        "timestamp": timestamp,
        "duration_seconds": duration_seconds,
    }
    receipt["receipt_digest"] = canonical_digest(receipt)
    _write_json_create_only(run_dir / "receipt.json", receipt)
    return receipt


async def capture(
    *,
    fixture_dir: Path,
    output_root: Path,
    capture_run_id: str,
    api_key: str,
) -> Dict[str, Any]:
    preregistration, plan, inputs, preflight = validate_preflight(
        fixture_dir=fixture_dir,
        output_root=output_root,
        capture_run_id=capture_run_id,
        credential=api_key,
    )
    output_root.mkdir(parents=True, exist_ok=True)
    run_dir = output_root / capture_run_id
    pending_dir = output_root / f".{capture_run_id}.pending"
    pending_dir.mkdir(exist_ok=False)
    capture_contract = preregistration.manifest["capture"]
    started = time.monotonic()
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    budget: Optional[GlobalAttemptBudget] = None
    result: Optional[CaptureExecutionResult] = None
    try:
        budget = GlobalAttemptBudget(
            pending_dir / "run-state",
            max_external_attempts=int(capture_contract["max_external_attempts"]),
        )
        client = OpenAIEmbeddingClient(api_key=api_key, default_model=plan.model)
        result = await execute_logical_requests(
            plan=plan,
            inputs_by_request=inputs,
            embedding_client=client,
            attempt_budget=budget,
            deadline_monotonic=started + float(capture_contract["max_duration_seconds"]),
            provider_timeout_seconds=float(capture_contract["provider_request_timeout_seconds"]),
            finalize_reserve_seconds=float(capture_contract["artifact_finalize_reserve_seconds"]),
        )
        duration = time.monotonic() - started
        if duration > float(capture_contract["max_duration_seconds"]):
            raise CaptureFailure(
                safe_failure_category="duration_budget_exhausted",
                failed_logical_request_ordinal=None,
                failed_variant=None,
                failed_batch_ordinal=None,
                completed_logical_request_count=result.completed_logical_request_count,
                actual_external_attempt_count=result.actual_external_attempt_count,
                retry_count=result.retry_count,
            )
        receipt = _write_success_artifacts(pending_dir, preregistration, plan, result, preflight, duration, timestamp)
    except CaptureFailure as failure:
        (pending_dir / "vectors.json").unlink(missing_ok=True)
        receipt = write_failure_receipt(
            run_dir=pending_dir,
            experiment_id=EXPERIMENT_ID,
            capture_run_id=capture_run_id,
            manifest_digest=preregistration.manifest_digest,
            request_plan_digest=plan.request_plan_digest,
            capture_source_digest=preregistration.manifest["implementation"]["capture_source_digest"],
            implementation_source_digest=_implementation_source_digest(preregistration.manifest),
            failure=failure,
            duration_seconds=time.monotonic() - started,
            timestamp=timestamp,
        )
    except Exception:
        (pending_dir / "vectors.json").unlink(missing_ok=True)
        failure = CaptureFailure(
            safe_failure_category="capture_initialization_or_artifact_failure",
            failed_logical_request_ordinal=None,
            failed_variant=None,
            failed_batch_ordinal=None,
            completed_logical_request_count=(
                result.completed_logical_request_count if result is not None else 0
            ),
            actual_external_attempt_count=budget.count if budget is not None else 0,
            retry_count=result.retry_count if result is not None else 0,
        )
        receipt = write_failure_receipt(
            run_dir=pending_dir,
            experiment_id=EXPERIMENT_ID,
            capture_run_id=capture_run_id,
            manifest_digest=preregistration.manifest_digest,
            request_plan_digest=plan.request_plan_digest,
            capture_source_digest=preregistration.manifest["implementation"]["capture_source_digest"],
            implementation_source_digest=_implementation_source_digest(preregistration.manifest),
            failure=failure,
            duration_seconds=time.monotonic() - started,
            timestamp=timestamp,
        )
    pending_dir.rename(run_dir)
    return receipt


def _write_success_artifacts(
    run_dir: Path,
    preregistration: Any,
    plan: CapturePlan,
    result: CaptureExecutionResult,
    preflight: CapturePreflight,
    duration_seconds: float,
    timestamp: str,
) -> Dict[str, Any]:
    if result.completed_logical_request_count != len(plan.requests):
        raise CaptureFailure(
            safe_failure_category="incomplete_capture",
            failed_logical_request_ordinal=None,
            failed_variant=None,
            failed_batch_ordinal=None,
            completed_logical_request_count=result.completed_logical_request_count,
            actual_external_attempt_count=result.actual_external_attempt_count,
            retry_count=result.retry_count,
        )
    expected_query_ids = {query["query_id"] for query in preregistration.queries}
    expected_chunk_ids = {chunk["chunk_id"] for chunk in preregistration.chunks}
    if set(result.query_vectors) != expected_query_ids or any(
        set(result.document_vectors[variant]) != expected_chunk_ids for variant in VARIANTS
    ):
        raise CaptureFailure(
            safe_failure_category="incomplete_vector_sets",
            failed_logical_request_ordinal=None,
            failed_variant=None,
            failed_batch_ordinal=None,
            completed_logical_request_count=result.completed_logical_request_count,
            actual_external_attempt_count=result.actual_external_attempt_count,
            retry_count=result.retry_count,
        )
    query_digest = canonical_digest(result.query_vectors)
    document_digests = {variant: canonical_digest(result.document_vectors[variant]) for variant in VARIANTS}
    identities = {
        (request.provider_reported_provider, request.provider_reported_model, request.provider_reported_dimensions)
        for request in result.requests
    }
    if identities != {(plan.provider, plan.model, plan.dimensions)}:
        raise CaptureFailure(
            safe_failure_category="provider_identity_mismatch",
            failed_logical_request_ordinal=None,
            failed_variant=None,
            failed_batch_ordinal=None,
            completed_logical_request_count=result.completed_logical_request_count,
            actual_external_attempt_count=result.actual_external_attempt_count,
            retry_count=result.retry_count,
        )
    capture_core = {
        "experiment_id": EXPERIMENT_ID,
        "capture_run_id": f"{EXPERIMENT_ID}-capture-001",
        "manifest_digest": preregistration.manifest_digest,
        "request_plan_digest": plan.request_plan_digest,
        "capture_source_digest": preregistration.manifest["implementation"]["capture_source_digest"],
        "implementation_source_digest": _implementation_source_digest(preregistration.manifest),
        "requested_provider": plan.provider,
        "requested_model_alias": plan.model,
        "dimensions": plan.dimensions,
        "provider_revision_id": None,
        "approval_id": preregistration.manifest["capture"]["approval_id"],
        "budget_id": preregistration.manifest["capture"]["budget_id"],
        "query_vector_set_digest": query_digest,
        "document_vector_set_digests": document_digests,
        "logical_execution_order": [asdict(request) for request in result.requests],
        "completed_logical_request_count": result.completed_logical_request_count,
        "actual_external_attempt_count": result.actual_external_attempt_count,
        "retry_count": result.retry_count,
        "provider_token_usage": result.token_input,
        "bounded_cost_estimate_usd": preflight.estimated_cost_bound_usd,
    }
    capture_run_digest = canonical_digest(capture_core)
    vectors_payload = {
        "metadata": {
            "experiment_id": EXPERIMENT_ID,
            "capture_run_digest": capture_run_digest,
            "query_vector_set_digest": query_digest,
            "document_vector_set_digests": document_digests,
        },
        "query_vectors": result.query_vectors,
        "document_vectors": result.document_vectors,
    }
    vectors_path = run_dir / "vectors.json"
    _write_json_create_only(vectors_path, vectors_payload)
    receipt = dict(
        capture_core,
        status="captured",
        capture_run_digest=capture_run_digest,
        vectors_file_digest=file_digest(vectors_path),
        vectors_artifact_created=True,
        timestamp=timestamp,
        duration_seconds=duration_seconds,
        safe_failure_category=None,
        failed_logical_request_ordinal=None,
        failed_variant=None,
        failed_batch_ordinal=None,
    )
    receipt["receipt_digest"] = canonical_digest(receipt)
    _write_json_create_only(run_dir / "receipt.json", receipt)
    return receipt


def _capture_failure(
    category: str,
    request: Optional[CaptureRequest],
    completed: int,
    client: BudgetedEmbeddingClient,
) -> CaptureFailure:
    return CaptureFailure(
        safe_failure_category=category,
        failed_logical_request_ordinal=request.ordinal if request else None,
        failed_variant=request.variant_id if request else None,
        failed_batch_ordinal=request.batch_ordinal if request else None,
        completed_logical_request_count=completed,
        actual_external_attempt_count=client.external_attempt_count,
        retry_count=_retry_count(client.attempts_by_request),
    )


def _retry_count(attempts_by_request: Mapping[int, int]) -> int:
    return sum(max(0, attempts - 1) for attempts in attempts_by_request.values())


def _safe_batch_category(error: EmbeddingBatchError) -> str:
    allowed = {
        "RETRY_EXHAUSTED": "provider_retry_exhausted",
        "PROVIDER_REQUEST_FAILED": "provider_request_failed",
        "VECTOR_DIMENSION_MISMATCH": "vector_dimension_mismatch",
    }
    return allowed.get(error.reason, "embedding_batch_failed")


def _implementation_source_digest(manifest: Mapping[str, Any]) -> str:
    return implementation_bundle_digest(manifest)


def _write_json_create_only(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.pending")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Guarded Step 98 exp-002 Phase B capture")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--approval", default="")
    parser.add_argument("--capture-run-id", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--fixture-dir", type=Path, default=DEFAULT_FIXTURE_DIR)
    args = parser.parse_args()
    if not args.execute or os.getenv(LIVE_ENV) != "1" or args.approval != APPROVAL_TEXT:
        print(json.dumps({"status": "skipped", "external_request_attempts": 0}, sort_keys=True))
        return
    try:
        receipt = asyncio.run(
            capture(
                fixture_dir=args.fixture_dir,
                output_root=args.output_root,
                capture_run_id=args.capture_run_id,
                api_key=os.getenv("OPENAI_API_KEY", ""),
            )
        )
    except Step98ContractError as exc:
        print(json.dumps({"status": "preflight_failed", "safe_failure_category": str(exc), "external_request_attempts": 0, "vectors_artifact_created": False}, sort_keys=True))
        raise SystemExit(2)
    safe = {
        key: receipt.get(key)
        for key in (
            "status",
            "experiment_id",
            "capture_run_id",
            "manifest_digest",
            "request_plan_digest",
            "capture_source_digest",
            "actual_external_attempt_count",
            "retry_count",
            "safe_failure_category",
            "capture_run_digest",
            "receipt_digest",
        )
    }
    print(json.dumps(safe, sort_keys=True))


if __name__ == "__main__":
    main()
