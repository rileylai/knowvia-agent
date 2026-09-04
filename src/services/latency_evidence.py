from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Dict, Mapping


LATENCY_FIELDS = (
    "download_ms",
    "ocr_ms",
    "llm_ms",
    "persist_ms",
    "preview_delivery_ms",
    "total_business_ms",
)


def elapsed_ms(start: float) -> float:
    """Return bounded, redacted elapsed time suitable for workflow metadata."""

    return round(max(0.0, (perf_counter() - start) * 1000.0), 3)


@dataclass
class LatencyEvidence:
    """Stage timings only; this type intentionally carries no request content."""

    values: Dict[str, float] = field(
        default_factory=lambda: {field_name: 0.0 for field_name in LATENCY_FIELDS}
    )

    def add(self, **timings: float) -> None:
        for field_name, value in timings.items():
            if field_name not in LATENCY_FIELDS:
                raise ValueError(f"unsupported latency field: {field_name}")
            self.values[field_name] = round(
                max(0.0, self.values[field_name] + float(value)),
                3,
            )

    def update(self, timings: Mapping[str, float]) -> None:
        self.add(**dict(timings))

    def as_dict(self) -> Dict[str, float]:
        return {
            field_name: round(max(0.0, float(self.values.get(field_name, 0.0))), 3)
            for field_name in LATENCY_FIELDS
        }

