from __future__ import annotations

from typing import List, Sequence

from src.agent.models import ReferenceBinding, ReferenceBindingDecision
from src.conversation_context import ReferenceResolverMessage


class ReferenceBindingValidationError(ValueError):
    """Raised when a provider binding cannot be proven within backend scope."""


def validate_reference_bindings(
    *,
    decision: ReferenceBindingDecision,
    current_message: str,
    current_sequence_number: int,
    resolver_history: Sequence[ReferenceResolverMessage],
) -> List[ReferenceBinding]:
    """Validate provider proposals against the exact bounded backend view."""

    if len(decision.reference_bindings) > 2:
        raise ReferenceBindingValidationError("reference binding count exceeds the limit")

    messages_by_id = {
        message.message_id: message
        for message in resolver_history
        if message.role in {"user", "assistant"}
    }
    validated: List[ReferenceBinding] = []
    for binding in decision.reference_bindings:
        if binding.current_span not in current_message:
            raise ReferenceBindingValidationError("current reference span is not present")
        source_message = messages_by_id.get(binding.source_message_id)
        if source_message is None:
            raise ReferenceBindingValidationError("source message is outside the resolver scope")
        if source_message.sequence_number >= current_sequence_number:
            raise ReferenceBindingValidationError("source message is not prior to the current request")
        if binding.source_span not in source_message.content:
            raise ReferenceBindingValidationError("source reference span is not present")
        validated.append(binding)
    return validated
