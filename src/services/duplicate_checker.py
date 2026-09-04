from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import List, Optional

from src.repositories import ChunkRepository

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


@dataclass
class DuplicateMatch:
    chunk_id: int
    notion_path: str
    similarity_score: float
    match_type: str


@dataclass
class DuplicateCheckResult:
    is_duplicate: bool
    matched: Optional[DuplicateMatch]


class DuplicateKnowledgeChecker:
    def __init__(
        self,
        *,
        chunk_repository: ChunkRepository,
        similarity_threshold: float = 0.9,
    ) -> None:
        if similarity_threshold <= 0 or similarity_threshold > 1:
            raise ValueError("similarity_threshold must be in (0, 1]")
        self._chunk_repository = chunk_repository
        self._similarity_threshold = similarity_threshold

    def check_duplicate(
        self,
        *,
        candidate_text: str,
        page_ids: Optional[List[str]] = None,
        section_paths: Optional[List[str]] = None,
    ) -> DuplicateCheckResult:
        normalized_candidate = self._normalize_text(candidate_text)
        if not normalized_candidate:
            raise ValueError("candidate_text must not be empty")

        candidate_hash = self._compute_hash(normalized_candidate)
        candidate_tokens = set(self._tokenize(normalized_candidate))

        best_similarity_match: Optional[DuplicateMatch] = None
        candidates = self._chunk_repository.list_production_chunks(
            page_ids=page_ids,
            section_paths=section_paths,
            source_kinds=["notion"],
        )
        for chunk in candidates:
            chunk_path = chunk.notion_path.strip()
            if not chunk_path:
                continue

            normalized_chunk_text = self._normalize_text(chunk.chunk_text)
            if not normalized_chunk_text:
                continue

            chunk_hash = self._compute_hash(normalized_chunk_text)
            if chunk_hash == candidate_hash:
                return DuplicateCheckResult(
                    is_duplicate=True,
                    matched=DuplicateMatch(
                        chunk_id=chunk.chunk_id,
                        notion_path=chunk_path,
                        similarity_score=1.0,
                        match_type="hash_match",
                    ),
                )

            similarity_score = self._score_similarity(
                normalized_candidate=normalized_candidate,
                candidate_tokens=candidate_tokens,
                normalized_chunk_text=normalized_chunk_text,
            )
            if similarity_score < self._similarity_threshold:
                continue

            candidate_match = DuplicateMatch(
                chunk_id=chunk.chunk_id,
                notion_path=chunk_path,
                similarity_score=similarity_score,
                match_type="similarity_match",
            )
            if best_similarity_match is None:
                best_similarity_match = candidate_match
                continue
            if similarity_score > best_similarity_match.similarity_score:
                best_similarity_match = candidate_match
                continue
            if (
                similarity_score == best_similarity_match.similarity_score
                and chunk.chunk_id < best_similarity_match.chunk_id
            ):
                best_similarity_match = candidate_match

        if best_similarity_match is None:
            return DuplicateCheckResult(is_duplicate=False, matched=None)
        return DuplicateCheckResult(is_duplicate=True, matched=best_similarity_match)

    def _score_similarity(
        self,
        *,
        normalized_candidate: str,
        candidate_tokens: set[str],
        normalized_chunk_text: str,
    ) -> float:
        chunk_tokens = set(self._tokenize(normalized_chunk_text))
        if not chunk_tokens or not candidate_tokens:
            return 0.0

        overlap = len(candidate_tokens.intersection(chunk_tokens))
        if overlap == 0:
            return 0.0

        jaccard = overlap / len(candidate_tokens.union(chunk_tokens))
        sequence_ratio = SequenceMatcher(
            a=normalized_candidate,
            b=normalized_chunk_text,
        ).ratio()
        return (jaccard * 0.45) + (sequence_ratio * 0.55)

    def _normalize_text(self, text: str) -> str:
        return " ".join(text.lower().split())

    def _tokenize(self, text: str) -> List[str]:
        return _TOKEN_PATTERN.findall(text)

    def _compute_hash(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
