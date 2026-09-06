from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence


CONVERSATION_CITATION_METADATA_VERSION = 1
MAX_CONVERSATION_CITATIONS = 20
MAX_CITATION_SOURCE_KIND_CHARS = 64
MAX_CITATION_SOURCE_DISPLAY_NAME_CHARS = 512
MAX_CITATION_LOCATOR_CHARS = 512
MAX_CITATION_SOURCE_URL_CHARS = 2048
MAX_CITATION_NOTION_PATH_CHARS = 2048
MAX_CITATION_ORIGINAL_FILENAME_CHARS = 512


@dataclass(frozen=True)
class ConversationCitation:
    """Backend-owned citation fields retained only for message disclosure."""

    notion_path: Optional[str]
    page_id: Optional[str]
    score: float
    source_kind: str
    source_display_name: Optional[str]
    locator: Optional[str]
    source_url: Optional[str]
    image_index: Optional[int]
    sequence_index: Optional[int]
    original_filename: Optional[str]

    def to_payload(self) -> dict[str, object]:
        return {
            "notion_path": self.notion_path,
            "page_id": self.page_id,
            "score": self.score,
            "source_kind": self.source_kind,
            "source_display_name": self.source_display_name,
            "locator": self.locator,
            "source_url": self.source_url,
            "image_index": self.image_index,
            "sequence_index": self.sequence_index,
            "original_filename": self.original_filename,
        }


def serialize_conversation_citations(
    citations: Sequence[ConversationCitation],
) -> Optional[str]:
    """Serialize only the typed, bounded citation projection from a QA result."""

    if not citations:
        return None

    bounded_citations = [
        _normalize_citation(citation)
        for citation in citations[:MAX_CONVERSATION_CITATIONS]
    ]
    return json.dumps(
        {
            "citation_metadata_version": CONVERSATION_CITATION_METADATA_VERSION,
            "citations": [citation.to_payload() for citation in bounded_citations],
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def deserialize_conversation_citations(value: Optional[str]) -> List[ConversationCitation]:
    """Read legacy or malformed rows without making session loading fail."""

    if not value:
        return []
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return []
    if not isinstance(payload, dict):
        return []
    if payload.get("citation_metadata_version") != CONVERSATION_CITATION_METADATA_VERSION:
        return []
    raw_citations = payload.get("citations")
    if not isinstance(raw_citations, list):
        return []

    citations: List[ConversationCitation] = []
    for raw_citation in raw_citations[:MAX_CONVERSATION_CITATIONS]:
        citation = _parse_citation(raw_citation)
        if citation is not None:
            citations.append(citation)
    return citations


def _normalize_citation(citation: ConversationCitation) -> ConversationCitation:
    if not isinstance(citation, ConversationCitation):
        raise TypeError("citations must contain ConversationCitation values")
    score = float(citation.score)
    if not math.isfinite(score):
        raise ValueError("citation score must be finite")
    return ConversationCitation(
        notion_path=_bound_optional_text(
            citation.notion_path,
            MAX_CITATION_NOTION_PATH_CHARS,
        ),
        page_id=_bound_optional_text(citation.page_id, MAX_CITATION_NOTION_PATH_CHARS),
        score=score,
        source_kind=_bound_text(
            citation.source_kind,
            MAX_CITATION_SOURCE_KIND_CHARS,
        ).lower(),
        source_display_name=_bound_optional_text(
            citation.source_display_name,
            MAX_CITATION_SOURCE_DISPLAY_NAME_CHARS,
        ),
        locator=_bound_optional_text(citation.locator, MAX_CITATION_LOCATOR_CHARS),
        source_url=_bound_optional_text(
            citation.source_url,
            MAX_CITATION_SOURCE_URL_CHARS,
        ),
        image_index=_optional_nonnegative_int(citation.image_index),
        sequence_index=_optional_nonnegative_int(citation.sequence_index),
        original_filename=_bound_optional_text(
            citation.original_filename,
            MAX_CITATION_ORIGINAL_FILENAME_CHARS,
        ),
    )


def _parse_citation(value: Any) -> Optional[ConversationCitation]:
    if not isinstance(value, dict):
        return None
    source_kind = value.get("source_kind")
    score = value.get("score")
    if not isinstance(source_kind, str) or isinstance(score, bool):
        return None
    try:
        normalized_score = float(score)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(normalized_score):
        return None

    return ConversationCitation(
        notion_path=_read_optional_text(value.get("notion_path"), MAX_CITATION_NOTION_PATH_CHARS),
        page_id=_read_optional_text(value.get("page_id"), MAX_CITATION_NOTION_PATH_CHARS),
        score=normalized_score,
        source_kind=_read_text(source_kind, MAX_CITATION_SOURCE_KIND_CHARS).lower(),
        source_display_name=_read_optional_text(
            value.get("source_display_name"),
            MAX_CITATION_SOURCE_DISPLAY_NAME_CHARS,
        ),
        locator=_read_optional_text(value.get("locator"), MAX_CITATION_LOCATOR_CHARS),
        source_url=_read_optional_text(value.get("source_url"), MAX_CITATION_SOURCE_URL_CHARS),
        image_index=_read_optional_nonnegative_int(value.get("image_index")),
        sequence_index=_read_optional_nonnegative_int(value.get("sequence_index")),
        original_filename=_read_optional_text(
            value.get("original_filename"),
            MAX_CITATION_ORIGINAL_FILENAME_CHARS,
        ),
    )


def _bound_text(value: str, limit: int) -> str:
    if not isinstance(value, str):
        raise TypeError("citation text fields must be strings")
    return value.strip()[:limit]


def _bound_optional_text(value: Optional[str], limit: int) -> Optional[str]:
    if value is None:
        return None
    normalized = _bound_text(value, limit)
    return normalized or None


def _read_text(value: Any, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _read_optional_text(value: Any, limit: int) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = value.strip()[:limit]
    return normalized or None


def _optional_nonnegative_int(value: Optional[int]) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, int) or value < 0:
        raise TypeError("citation indexes must be nonnegative integers")
    return value


def _read_optional_nonnegative_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    return value if isinstance(value, int) and value >= 0 else None

