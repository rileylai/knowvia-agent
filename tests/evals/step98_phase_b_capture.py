from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.providers import OpenAIEmbeddingClient
from src.services import CostTracker, EmbeddingBatchLimits, EmbeddingBatchService
from src.services.embedding_batch_service import estimate_embedding_tokens_safely

try:
    from .context_aware_embedding_input_eval import (
        CaptureArtifact,
        VARIANTS,
        canonical_digest,
        load_preregistration,
        materialize_capture_inputs,
        plan_capture,
        validate_capture_artifact,
    )
except ImportError:
    from context_aware_embedding_input_eval import (  # type: ignore[no-redef]
        CaptureArtifact,
        VARIANTS,
        canonical_digest,
        load_preregistration,
        materialize_capture_inputs,
        plan_capture,
        validate_capture_artifact,
    )


LIVE_ENV = "LEARNLOOP_STEP98_LIVE_CAPTURE"
APPROVAL_TEXT = "I_APPROVE_STEP98_PUBLIC_SAFE_CAPTURE"


async def capture(output_path: Path, *, capture_run_id: str) -> CaptureArtifact:
    preregistration = load_preregistration()
    plan = plan_capture(preregistration)
    inputs_by_request = materialize_capture_inputs(preregistration, plan)
    capture_contract = preregistration.manifest["capture"]
    if len(plan.requests) > int(capture_contract["max_requests"]):
        raise RuntimeError("capture request budget exceeded")
    if sum(len(request.item_ids) for request in plan.requests) > int(capture_contract["max_inputs"]):
        raise RuntimeError("capture input budget exceeded")
    estimated_tokens = sum(
        estimate_embedding_tokens_safely(value)
        for inputs in inputs_by_request.values()
        for value in inputs
    )
    if estimated_tokens > int(capture_contract["max_estimated_tokens"]):
        raise RuntimeError("capture estimated-token budget exceeded")
    estimated_preflight_cost = CostTracker().estimate_embedding_cost(
        provider_name=plan.provider,
        model=plan.model,
        token_input=estimated_tokens,
    )
    if estimated_preflight_cost is None or estimated_preflight_cost > float(capture_contract["max_cost_usd"]):
        raise RuntimeError("capture cost budget exceeded")
    if output_path.exists():
        raise RuntimeError("capture output already exists")

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required")
    client = OpenAIEmbeddingClient(api_key=api_key, default_model=plan.model)
    service = EmbeddingBatchService(
        embedding_client=client,
        model=plan.model,
        dimensions=plan.dimensions,
        limits=EmbeddingBatchLimits(max_inputs=int(capture_contract["batch_size"])),
    )

    query_vectors: Dict[str, List[float]] = {}
    document_vectors: Dict[str, Dict[str, List[float]]] = {
        variant: {} for variant in VARIANTS
    }
    retry_count = 0
    token_input = 0
    usage_complete = True
    started_at = time.monotonic()
    for request in plan.requests:
        if time.monotonic() - started_at > float(capture_contract["max_duration_seconds"]):
            raise RuntimeError("capture duration budget exceeded")
        result = await service.embed(
            inputs_by_request[request.ordinal],
            metadata={"experiment_id": plan.experiment_id, "capture_run_id": capture_run_id},
        )
        retry_count += result.retry_count
        if result.token_input is None:
            usage_complete = False
        else:
            token_input += result.token_input
        target = query_vectors if request.role == "query" else document_vectors[request.variant_id]
        for item_id, vector in zip(request.item_ids, result.embeddings):
            target[item_id] = vector

    query_digest = canonical_digest(query_vectors)
    document_digests = {
        variant: canonical_digest(vectors)
        for variant, vectors in document_vectors.items()
    }
    duration_seconds = time.monotonic() - started_at
    if duration_seconds > float(capture_contract["max_duration_seconds"]):
        raise RuntimeError("capture duration budget exceeded")
    estimated_cost = CostTracker().estimate_embedding_cost(
        provider_name=plan.provider,
        model=plan.model,
        token_input=token_input if usage_complete else None,
    )
    artifact_payload = {
        "experiment_id": plan.experiment_id,
        "manifest_digest": plan.manifest_digest,
        "capture_run_id": capture_run_id,
        "request_plan_digest": plan.request_plan_digest,
        "query_vector_set_digest": query_digest,
        "document_vector_set_digests": document_digests,
        "provider": plan.provider,
        "model": plan.model,
        "dimensions": plan.dimensions,
        "provider_revision_id": None,
        "batch_count": len(plan.requests),
        "retry_count": retry_count,
        "token_input": token_input if usage_complete else None,
        "estimated_cost_usd": estimated_cost,
        "duration_seconds": duration_seconds,
        "vectors_retained": True,
    }
    artifact = CaptureArtifact(
        capture_run_digest=canonical_digest(artifact_payload),
        **artifact_payload,
    )
    validate_capture_artifact(preregistration, plan, artifact)
    output = {
        "metadata": artifact.__dict__,
        "query_vectors": query_vectors,
        "document_vectors": document_vectors,
    }
    with output_path.open("x", encoding="utf-8") as output_file:
        json.dump(output, output_file, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        output_file.write("\n")
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser(description="Guarded Step 98 public-safe Phase B capture")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--approval", default="")
    parser.add_argument("--capture-run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.execute or os.getenv(LIVE_ENV) != "1" or args.approval != APPROVAL_TEXT:
        print(json.dumps({"status": "skipped", "external_requests": 0}, sort_keys=True))
        return
    artifact = asyncio.run(capture(args.output, capture_run_id=args.capture_run_id))
    print(json.dumps({"status": "captured", "capture_run_digest": artifact.capture_run_digest, "batch_count": artifact.batch_count, "retry_count": artifact.retry_count}, sort_keys=True))


if __name__ == "__main__":
    main()
