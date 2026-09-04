from decimal import Decimal

import pytest

from src.services import CostTracker, EmbeddingTokenPricing, LLMTokenPricing


def test_cost_tracker_estimates_llm_cost_for_known_model() -> None:
    tracker = CostTracker(
        llm_pricing={
            ("openai", "gpt-test-mini"): LLMTokenPricing(
                input_per_million_usd=Decimal("0.10"),
                output_per_million_usd=Decimal("0.40"),
            )
        }
    )

    estimated_cost = tracker.estimate_llm_cost(
        provider_name="openai",
        model="gpt-test-mini",
        token_input=120,
        token_output=90,
    )

    assert estimated_cost == pytest.approx(0.000048)


def test_cost_tracker_estimates_embedding_cost_for_known_model() -> None:
    tracker = CostTracker(
        llm_pricing={},
        embedding_pricing={
            ("openai", "text-embedding-test"): EmbeddingTokenPricing(
                input_per_million_usd=Decimal("0.05")
            )
        },
    )

    estimated_cost = tracker.estimate_embedding_cost(
        provider_name="openai",
        model="text-embedding-test",
        token_input=250,
    )

    assert estimated_cost == pytest.approx(0.0000125)


def test_cost_tracker_returns_none_for_unknown_model() -> None:
    tracker = CostTracker(llm_pricing={}, embedding_pricing={})

    assert (
        tracker.estimate_llm_cost(
            provider_name="openai",
            model="unknown-model",
            token_input=10,
            token_output=5,
        )
        is None
    )
    assert (
        tracker.estimate_embedding_cost(
            provider_name="openai",
            model="unknown-embedding-model",
            token_input=10,
        )
        is None
    )


def test_cost_tracker_keeps_embedding_cost_unknown_when_usage_is_missing() -> None:
    tracker = CostTracker()

    assert (
        tracker.estimate_embedding_cost(
            provider_name="openai",
            model="text-embedding-3-small",
            token_input=None,
        )
        is None
    )
