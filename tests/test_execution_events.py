from __future__ import annotations

import pytest

from src.services.execution_events import iter_answer_deltas


def test_answer_deltas_reconstruct_multilingual_text_without_byte_corruption() -> None:
    answer = "繁體中文 mixed English，還有 emoji 🚀。"

    deltas = list(iter_answer_deltas(answer, max_chars=3))

    assert "".join(deltas) == answer
    assert all(delta for delta in deltas)


def test_default_answer_delta_chunks_are_bounded_and_progressive() -> None:
    answer = "A bounded answer chunk. " * 8

    deltas = list(iter_answer_deltas(answer))

    assert len(deltas) > 1
    assert max(map(len, deltas)) <= 32
    assert "".join(deltas) == answer


def test_answer_delta_chunk_size_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_chars"):
        list(iter_answer_deltas("answer", max_chars=0))
