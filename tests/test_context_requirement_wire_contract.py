from __future__ import annotations

from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from src.agent import (
    ContextRequirementWireDecision,
    map_context_requirement_wire_decision,
)


FACET = {"id": "c1", "text": "company size"}
SECOND_FACET = {"id": "c2", "text": "development preferences"}


def _schema_nodes(value: object) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    if isinstance(value, dict):
        nodes.append(value)
        for child in value.values():
            nodes.extend(_schema_nodes(child))
    elif isinstance(value, list):
        for child in value:
            nodes.extend(_schema_nodes(child))
    return nodes


@pytest.mark.parametrize(
    "payload",
    [
        {"selection": {"mode": "knowledge_only"}},
        {"selection": {"mode": "mixed", "contextual_facets": [FACET]}},
        {
            "selection": {
                "mode": "mixed",
                "contextual_facets": [FACET, SECOND_FACET],
            }
        },
        {"selection": {"mode": "memory_only", "memory_query": "company size"}},
        {"selection": {"mode": "neither"}},
    ],
)
def test_context_requirement_wire_accepts_only_valid_branch_shapes(
    payload: dict[str, Any],
) -> None:
    decision = ContextRequirementWireDecision.model_validate(payload)

    assert decision.model_dump() == payload
    schema = ContextRequirementWireDecision.response_format()["json_schema"]["schema"]
    assert list(Draft202012Validator(schema).iter_errors(payload)) == []


@pytest.mark.parametrize(
    "payload",
    [
        {
            "selection": {
                "mode": "knowledge_only",
                "contextual_facets": [FACET],
            }
        },
        {"selection": {"mode": "knowledge_only", "memory_query": "company size"}},
        {"selection": {"mode": "mixed", "contextual_facets": []}},
        {
            "selection": {
                "mode": "mixed",
                "contextual_facets": [FACET, SECOND_FACET, FACET],
            }
        },
        {
            "selection": {
                "mode": "mixed",
                "contextual_facets": [FACET],
                "memory_query": "company size",
            }
        },
        {"selection": {"mode": "memory_only", "memory_query": ""}},
        {
            "selection": {
                "mode": "memory_only",
                "memory_query": "company size",
                "contextual_facets": [FACET],
            }
        },
        {"selection": {"mode": "neither", "contextual_facets": [FACET]}},
        {"selection": {"mode": "neither", "memory_query": "company size"}},
        {"selection": {"mode": "unknown"}},
        {"selection": {"mode": "neither", "confidence": 1}},
    ],
)
def test_context_requirement_wire_rejects_invalid_branch_shapes(
    payload: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        ContextRequirementWireDecision.model_validate(payload)
    schema = ContextRequirementWireDecision.response_format()["json_schema"]["schema"]
    assert list(Draft202012Validator(schema).iter_errors(payload))


def test_effective_provider_schema_is_strict_root_with_nested_any_of() -> None:
    response_format = ContextRequirementWireDecision.response_format()
    schema = response_format["json_schema"]["schema"]

    assert response_format["json_schema"]["strict"] is True
    assert schema["type"] == "object"
    assert schema["required"] == ["selection"]
    assert schema["additionalProperties"] is False
    assert "anyOf" not in schema
    selection_schema = schema["properties"]["selection"]
    assert "anyOf" in selection_schema
    assert len(selection_schema["anyOf"]) == 4

    branches = {
        branch["properties"]["mode"]["enum"][0]: branch
        for branch in selection_schema["anyOf"]
    }
    assert set(branches) == {"knowledge_only", "mixed", "memory_only", "neither"}
    assert set(branches["knowledge_only"]["properties"]) == {"mode"}
    assert set(branches["mixed"]["properties"]) == {
        "mode",
        "contextual_facets",
    }
    assert set(branches["memory_only"]["properties"]) == {"mode", "memory_query"}
    assert set(branches["neither"]["properties"]) == {"mode"}
    assert all(branch["additionalProperties"] is False for branch in branches.values())
    assert branches["mixed"]["properties"]["contextual_facets"]["minItems"] == 1
    assert branches["mixed"]["properties"]["contextual_facets"]["maxItems"] == 2
    assert branches["memory_only"]["properties"]["memory_query"]["minLength"] == 1
    nodes = _schema_nodes(schema)
    unsupported_keywords = {
        "allOf",
        "dependentSchemas",
        "else",
        "if",
        "not",
        "oneOf",
        "then",
    }
    assert not any(unsupported_keywords & set(node) for node in nodes)
    assert all(
        node.get("additionalProperties") is False
        for node in nodes
        if node.get("type") == "object"
    )

    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize(
    ("wire", "expected"),
    [
        (
            {"selection": {"mode": "knowledge_only"}},
            {
                "needs_knowledge": True,
                "needs_memory": False,
                "contextual_facets": [],
                "memory_query": None,
            },
        ),
        (
            {"selection": {"mode": "mixed", "contextual_facets": [FACET]}},
            {
                "needs_knowledge": True,
                "needs_memory": True,
                "contextual_facets": [FACET],
                "memory_query": None,
            },
        ),
        (
            {
                "selection": {
                    "mode": "memory_only",
                    "memory_query": "company size",
                }
            },
            {
                "needs_knowledge": False,
                "needs_memory": True,
                "contextual_facets": [],
                "memory_query": "company size",
            },
        ),
        (
            {"selection": {"mode": "neither"}},
            {
                "needs_knowledge": False,
                "needs_memory": False,
                "contextual_facets": [],
                "memory_query": None,
            },
        ),
    ],
)
def test_wire_mapper_produces_exact_domain_decision(
    wire: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    provider_decision = ContextRequirementWireDecision.model_validate(wire)

    domain_decision = map_context_requirement_wire_decision(provider_decision)

    assert domain_decision.model_dump() == expected


def test_known_8_6_cross_field_failure_is_not_in_wire_valid_state_space() -> None:
    old_failure_state = {
        "needs_knowledge": True,
        "needs_memory": False,
        "contextual_facets": [FACET],
        "memory_query": None,
    }
    schema = ContextRequirementWireDecision.response_format()["json_schema"]["schema"]

    assert list(Draft202012Validator(schema).iter_errors(old_failure_state))
    with pytest.raises(ValidationError):
        ContextRequirementWireDecision.model_validate(old_failure_state)


def test_wire_structure_does_not_replace_backend_facet_semantic_validation() -> None:
    provider_decision = ContextRequirementWireDecision.model_validate(
        {
            "selection": {
                "mode": "mixed",
                "contextual_facets": [
                    {"id": "c1", "text": "search saved memory"},
                ],
            }
        }
    )

    with pytest.raises(ValidationError, match="context, not retrieval"):
        map_context_requirement_wire_decision(provider_decision)
