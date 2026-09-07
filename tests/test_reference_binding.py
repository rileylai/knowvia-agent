from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.agent import (
    ReferenceBinding,
    ReferenceBindingDecision,
    validate_reference_bindings,
)
from src.conversation_context import ReferenceResolverMessage


def test_reference_binding_decision_accepts_zero_to_two_bounded_bindings() -> None:
    empty = ReferenceBindingDecision.model_validate({"reference_bindings": []})
    assert empty.reference_bindings == []

    decision = ReferenceBindingDecision.model_validate(
        {
            "reference_bindings": [
                {
                    "current_span": "the second one",
                    "source_message_id": 41,
                    "source_span": "Deterministic Control Flow",
                },
                {
                    "current_span": "it",
                    "source_message_id": 42,
                    "source_span": "Tool Boundaries",
                },
            ]
        }
    )

    assert decision.reference_bindings[0] == ReferenceBinding(
        current_span="the second one",
        source_message_id=41,
        source_span="Deterministic Control Flow",
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"reference_bindings": [{"current_span": "x"}]},
        {"reference_bindings": None},
        {"reference_bindings": [{"current_span": "x", "source_message_id": "41", "source_span": "x"}]},
        {
            "reference_bindings": [
                {
                    "current_span": "x",
                    "source_message_id": 41,
                    "source_span": "x",
                    "extra": "forbidden",
                }
            ]
        },
        {
            "reference_bindings": [
                {
                    "current_span": "x",
                    "source_message_id": 41,
                    "source_span": "x" * 501,
                }
            ]
        },
        {"reference_bindings": [{"current_span": "x", "source_message_id": 41, "source_span": "x"}] * 3},
        {
            "reference_bindings": [
                {
                    "current_span": "x" * 201,
                    "source_message_id": 41,
                    "source_span": "x",
                }
            ]
        },
    ],
)
def test_reference_binding_decision_rejects_malformed_or_unbounded_output(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ReferenceBindingDecision.model_validate(payload)


def test_validate_reference_bindings_requires_exact_bounded_backend_matches() -> None:
    decision = ReferenceBindingDecision.model_validate(
        {
            "reference_bindings": [
                {
                    "current_span": "the second one",
                    "source_message_id": 41,
                    "source_span": "Tool Boundaries",
                }
            ]
        }
    )
    history = [
        ReferenceResolverMessage(
            message_id=41,
            sequence_number=2,
            role="assistant",
            content="1. Deterministic Control Flow\n2. Tool Boundaries",
        )
    ]

    validated = validate_reference_bindings(
        decision=decision,
        current_message="What about the second one?",
        current_sequence_number=3,
        resolver_history=history,
    )

    assert validated == decision.reference_bindings


@pytest.mark.parametrize(
    ("decision", "history", "current_message", "current_sequence_number"),
    [
        (
            {"reference_bindings": [{"current_span": "the second one", "source_message_id": 999, "source_span": "Tool Boundaries"}]},
            [],
            "What about the second one?",
            3,
        ),
        (
            {"reference_bindings": [{"current_span": "the second one", "source_message_id": 41, "source_span": "Tool Boundaries"}]},
            [ReferenceResolverMessage(41, 4, "assistant", "Tool Boundaries")],
            "What about the second one?",
            3,
        ),
        (
            {"reference_bindings": [{"current_span": "the second one", "source_message_id": 41, "source_span": "Tool Boundaries"}]},
            [ReferenceResolverMessage(41, 2, "system", "Tool Boundaries")],
            "What about the second one?",
            3,
        ),
        (
            {"reference_bindings": [{"current_span": "missing", "source_message_id": 41, "source_span": "Tool Boundaries"}]},
            [ReferenceResolverMessage(41, 2, "assistant", "Tool Boundaries")],
            "What about the second one?",
            3,
        ),
        (
            {"reference_bindings": [{"current_span": "the second one", "source_message_id": 41, "source_span": "missing"}]},
            [ReferenceResolverMessage(41, 2, "assistant", "Tool Boundaries")],
            "What about the second one?",
            3,
        ),
    ],
)
def test_validate_reference_bindings_fails_closed_for_invalid_scope_or_spans(
    decision: dict[str, object],
    history: list[ReferenceResolverMessage],
    current_message: str,
    current_sequence_number: int,
) -> None:
    parsed = ReferenceBindingDecision.model_validate(decision)

    with pytest.raises(ValueError):
        validate_reference_bindings(
            decision=parsed,
            current_message=current_message,
            current_sequence_number=current_sequence_number,
            resolver_history=history,
        )
