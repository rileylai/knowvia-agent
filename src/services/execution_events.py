from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, Optional, Protocol


ExecutionPhase = Literal[
    "searching_knowledge",
    "searching_memory",
    "saving_memory",
    "generating",
]
DEFAULT_ANSWER_DELTA_MAX_CHARS = 32


class ExecutionEventSink(Protocol):
    """Bounded, transport-neutral events emitted during one conversation run."""

    def emit_execution_status(self, *, phase: str) -> None:
        ...

    def emit_answer_delta(self, *, text: str) -> None:
        ...

    def emit_citations(self, *, citations: Sequence[Mapping[str, Any]]) -> None:
        ...

    def emit_done(self, *, payload: Mapping[str, Any]) -> None:
        ...

    def emit_error(self, *, error_code: str, message: str) -> None:
        ...


def emit_execution_status(
    sink: Optional[ExecutionEventSink],
    *,
    phase: ExecutionPhase,
) -> None:
    """Report a safe status without making business execution depend on transport."""

    if sink is None:
        return
    try:
        sink.emit_execution_status(phase=phase)
    except Exception:
        # A disconnected or failed event sink must not change the Agent result.
        return


def iter_answer_deltas(
    answer: str,
    *,
    max_chars: int = DEFAULT_ANSWER_DELTA_MAX_CHARS,
):
    """Yield bounded Unicode-safe chunks that reconstruct ``answer`` exactly."""

    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    for start in range(0, len(answer), max_chars):
        delta = answer[start : start + max_chars]
        if delta:
            yield delta
