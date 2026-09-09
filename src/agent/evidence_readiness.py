from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from src.agent.models import (
    EvidenceReadinessDecision,
    ReferenceBinding,
)
from src.providers import LLMMessage, LLMRequest, LLMResponse, ProviderRouter
from src.services.prompt_safety import format_untrusted_prompt_block


EVIDENCE_READINESS_SYSTEM_MESSAGE = (
    "You are the bounded Knowvia Evidence Readiness evaluator. Return only the requested "
    "structured object. Set ready true when the supplied accepted Knowledge evidence is sufficient "
    "to produce at least one materially complete, correct, grounded answer to the exact current "
    "task without inventing unsupported Knowledge claims. Materially complete means sufficient "
    "to answer what the user actually asked. Accepted evidence count greater than zero is not "
    "sufficient by itself. It does not require exhaustive source coverage, support for every possible "
    "elaboration or optional detail, verbatim support for every query phrase, every accepted chunk "
    "to be individually necessary, or support for every conceivable interpretation. Set ready false "
    "only when producing that answer would require one or more material Knowledge-backed claims "
    "not supported by the accepted Knowledge evidence; related evidence alone is not enough. "
    "Separately designated Memory-side dependencies are supplemental context and must not be treated "
    "as missing Knowledge requirements or require Knowledge evidence. Evaluate only the material "
    "Knowledge-backed claims needed for the answer. Memory-side dependencies are optional "
    "supplemental context; their availability or absence does not change Knowledge readiness. "
    "Memory cannot satisfy or replace a missing Knowledge-backed claim. Do not use actual Memory "
    "content as Knowledge evidence. Do not retrieve "
    "more evidence, rewrite the task, request a retry, create citations, call tools, or emit any "
    "field other than ready."
)


class EvidenceReadinessContractError(ValueError):
    """Raised when a readiness provider response violates the frozen contract."""


def _bounded_memory_dependencies(
    dependencies: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if dependencies is None:
        return {"needs_memory": False, "facet_labels": []}
    unexpected = set(dependencies) - {"needs_memory", "facet_labels"}
    if unexpected:
        raise EvidenceReadinessContractError(
            "readiness memory dependencies contain unsupported fields"
        )
    needs_memory = dependencies.get("needs_memory", False)
    facet_labels = dependencies.get("facet_labels", [])
    if not isinstance(needs_memory, bool):
        raise EvidenceReadinessContractError(
            "readiness memory dependency flag is invalid"
        )
    if not isinstance(facet_labels, list) or any(
        not isinstance(label, str) or not label.strip() or len(label) > 120
        for label in facet_labels
    ):
        raise EvidenceReadinessContractError(
            "readiness memory dependency facets are invalid"
        )
    return {
        "needs_memory": needs_memory,
        "facet_labels": [label.strip() for label in facet_labels],
    }


def _bounded_evidence(evidence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    bounded: List[Dict[str, Any]] = []
    for index, item in enumerate(evidence, start=1):
        if not isinstance(item, dict):
            raise EvidenceReadinessContractError("accepted Knowledge evidence is invalid")
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            raise EvidenceReadinessContractError("accepted Knowledge evidence text is invalid")
        bounded.append(
            {
                "id": item.get("id") or f"K{index}",
                "text": text,
                "source_kind": item.get("source_kind"),
                "source_display_name": item.get("source_display_name"),
                "locator": item.get("locator"),
            }
        )
    return bounded


def build_evidence_readiness_messages(
    *,
    task: str,
    reference_bindings: List[ReferenceBinding],
    accepted_evidence: List[Dict[str, Any]],
    memory_dependencies: Optional[Dict[str, Any]] = None,
) -> List[LLMMessage]:
    if not task.strip():
        raise EvidenceReadinessContractError("readiness task must not be empty")
    bounded_dependencies = _bounded_memory_dependencies(memory_dependencies)
    user = format_untrusted_prompt_block(
        label="CURRENT_SUBSTANTIVE_TASK",
        value=task,
    )
    user += "\n\n" + format_untrusted_prompt_block(
        label="VALIDATED_REFERENCE_BINDINGS",
        value=json.dumps(
            [binding.model_dump() for binding in reference_bindings],
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    user += "\n\n" + format_untrusted_prompt_block(
        label="ACCEPTED_KNOWLEDGE_EVIDENCE",
        value=json.dumps(
            _bounded_evidence(accepted_evidence),
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    user += "\n\n" + format_untrusted_prompt_block(
        label="MEMORY_SIDE_DEPENDENCIES",
        value=json.dumps(bounded_dependencies, ensure_ascii=False, sort_keys=True),
    )
    return [
        LLMMessage(role="system", content=EVIDENCE_READINESS_SYSTEM_MESSAGE),
        LLMMessage(role="user", content=user),
    ]


async def assess_evidence_readiness(
    *,
    provider_router: ProviderRouter,
    provider_name: str,
    model: str,
    task: str,
    reference_bindings: List[ReferenceBinding],
    accepted_evidence: List[Dict[str, Any]],
    memory_dependencies: Optional[Dict[str, Any]],
    metadata: Dict[str, Any],
) -> Tuple[EvidenceReadinessDecision, LLMResponse]:
    response = await provider_router.route(
        provider_name,
        LLMRequest(
            model=model,
            messages=build_evidence_readiness_messages(
                task=task,
                reference_bindings=reference_bindings,
                accepted_evidence=accepted_evidence,
                memory_dependencies=memory_dependencies,
            ),
            temperature=0.0,
            max_tokens=50,
            response_format=EvidenceReadinessDecision.response_format(),
            metadata={**metadata, "operation": "evidence_readiness"},
        ),
    )
    if response.tool_calls:
        raise EvidenceReadinessContractError(
            "readiness provider returned tool calls"
        )
    try:
        decision = EvidenceReadinessDecision.model_validate(response.structured_output)
    except ValidationError as exc:
        raise EvidenceReadinessContractError(
            "readiness provider returned invalid structured output"
        ) from exc
    return decision, response
