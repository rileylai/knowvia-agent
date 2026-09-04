from __future__ import annotations

from typing import Iterable, List

from src.services.notion_hierarchy import TELEGRAM_MESSAGE_MAX_LENGTH

TELEGRAM_PREVIEW_NOTE_MAX_CHARS = 600


def format_bounded_note_lines(notes: Iterable[str]) -> List[str]:
    lines: List[str] = []
    for note in notes:
        normalized = " ".join(note.split())
        if len(normalized) > TELEGRAM_PREVIEW_NOTE_MAX_CHARS:
            normalized = normalized[: TELEGRAM_PREVIEW_NOTE_MAX_CHARS - 1].rstrip() + "…"
        lines.append(f"- {normalized}")
    return lines


def truncate_telegram_preview(text: str) -> str:
    if len(text) <= TELEGRAM_MESSAGE_MAX_LENGTH:
        return text
    suffix = "\n…"
    return text[: TELEGRAM_MESSAGE_MAX_LENGTH - len(suffix)].rstrip() + suffix
