from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Optional, Tuple


MICRO_TOKEN_DENOMINATOR = Decimal("1000000")
COST_PRECISION = Decimal("0.000000000001")


@dataclass(frozen=True)
class LLMTokenPricing:
    input_per_million_usd: Decimal
    output_per_million_usd: Decimal


@dataclass(frozen=True)
class EmbeddingTokenPricing:
    input_per_million_usd: Decimal


def _normalize_provider_model(provider_name: str, model: str) -> Tuple[str, str]:
    normalized_provider = provider_name.strip().lower()
    normalized_model = model.strip().lower()
    return normalized_provider, normalized_model


class CostTracker:
    def __init__(
        self,
        *,
        llm_pricing: Optional[Dict[Tuple[str, str], LLMTokenPricing]] = None,
        embedding_pricing: Optional[
            Dict[Tuple[str, str], EmbeddingTokenPricing]
        ] = None,
    ) -> None:
        self._llm_pricing = (
            llm_pricing if llm_pricing is not None else self._build_default_llm_pricing()
        )
        self._embedding_pricing = (
            embedding_pricing
            if embedding_pricing is not None
            else self._build_default_embedding_pricing()
        )

    def estimate_llm_cost(
        self,
        *,
        provider_name: str,
        model: str,
        token_input: Optional[int],
        token_output: Optional[int],
    ) -> Optional[float]:
        pricing = self._llm_pricing.get(
            _normalize_provider_model(provider_name, model)
        )
        if pricing is None:
            return None

        normalized_token_input = max(token_input or 0, 0)
        normalized_token_output = max(token_output or 0, 0)
        total_cost = (
            (Decimal(normalized_token_input) * pricing.input_per_million_usd)
            + (Decimal(normalized_token_output) * pricing.output_per_million_usd)
        ) / MICRO_TOKEN_DENOMINATOR
        return self._to_json_float(total_cost)

    def estimate_embedding_cost(
        self,
        *,
        provider_name: str,
        model: str,
        token_input: Optional[int],
    ) -> Optional[float]:
        pricing = self._embedding_pricing.get(
            _normalize_provider_model(provider_name, model)
        )
        if pricing is None or token_input is None:
            return None

        normalized_token_input = max(token_input, 0)
        total_cost = (
            Decimal(normalized_token_input) * pricing.input_per_million_usd
        ) / MICRO_TOKEN_DENOMINATOR
        return self._to_json_float(total_cost)

    def _build_default_llm_pricing(self) -> Dict[Tuple[str, str], LLMTokenPricing]:
        # Rates are USD per 1M tokens for the repo's default OpenAI chat model.
        return {
            ("openai", "gpt-4o-mini"): LLMTokenPricing(
                input_per_million_usd=Decimal("0.15"),
                output_per_million_usd=Decimal("0.60"),
            ),
        }

    def _build_default_embedding_pricing(
        self,
    ) -> Dict[Tuple[str, str], EmbeddingTokenPricing]:
        return {
            ("openai", "text-embedding-3-small"): EmbeddingTokenPricing(
                input_per_million_usd=Decimal("0.02"),
            ),
            ("openai", "text-embedding-3-large"): EmbeddingTokenPricing(
                input_per_million_usd=Decimal("0.13"),
            ),
        }

    def _to_json_float(self, value: Decimal) -> float:
        return float(value.quantize(COST_PRECISION, rounding=ROUND_HALF_UP))
