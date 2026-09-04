from __future__ import annotations

import json
from typing import Iterable, List, Optional

from sqlalchemy.types import UserDefinedType


class Vector(UserDefinedType):
    cache_ok = True

    def __init__(self, dimensions: int) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self.dimensions = dimensions

    def get_col_spec(self, **_: object) -> str:
        return f"VECTOR({self.dimensions})"

    def bind_processor(self, dialect: object) -> object:
        def process(value: Optional[object]) -> Optional[str]:
            normalized = _normalize_vector(value=value, dimensions=self.dimensions)
            if normalized is None:
                return None
            return json.dumps(normalized, separators=(",", ":"))

        return process

    def result_processor(self, dialect: object, coltype: object) -> object:
        def process(value: Optional[object]) -> Optional[List[float]]:
            return _normalize_vector(value=value, dimensions=self.dimensions)

        return process


def _normalize_vector(
    *,
    value: Optional[object],
    dimensions: int,
) -> Optional[List[float]]:
    if value is None:
        return None

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError("vector value must be valid JSON array text") from exc
        return _normalize_vector_iterable(values=parsed, dimensions=dimensions)

    if isinstance(value, Iterable):
        return _normalize_vector_iterable(values=value, dimensions=dimensions)

    raise ValueError("vector value must be a sequence of numbers")


def _normalize_vector_iterable(
    *,
    values: Iterable[object],
    dimensions: int,
) -> List[float]:
    normalized = [float(item) for item in values]
    if len(normalized) != dimensions:
        raise ValueError(
            f"vector length {len(normalized)} does not match expected {dimensions}"
        )
    return normalized
