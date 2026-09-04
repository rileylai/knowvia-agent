from __future__ import annotations

from typing import Optional

PROMPT_SAFETY_VERSION = "prompt_safety_v1"
AI_SUPPLEMENT_ZONE = "AI Supplement Zone"


def format_untrusted_prompt_block(*, label: str, value: str) -> str:
    """Mark source data as untrusted and prevent delimiter breakout."""
    normalized_label = label.strip().upper()
    if not normalized_label or not normalized_label.replace("_", "").isalnum():
        raise ValueError("label must contain only letters, digits, and underscores")
    normalized_value = str(value)
    end_marker = f"[END UNTRUSTED {normalized_label}]"
    escaped_value = normalized_value.replace(
        end_marker,
        f"[ESCAPED {end_marker}]",
    )
    return (
        f"[BEGIN UNTRUSTED {normalized_label}]\n"
        f"{escaped_value}\n"
        f"{end_marker}"
    )


def is_safe_supplement_target_path(
    *,
    target_path: str,
    target_page_path: Optional[str],
) -> bool:
    """Require the one backend-owned supplement target when a page is selected."""
    return (
        normalize_supplement_target_path(
            target_path=target_path,
            target_page_path=target_page_path,
        )
        is not None
    )


def normalize_notion_path(value: str) -> Optional[str]:
    """Normalize a Notion hierarchy path without resolving path traversal."""
    normalized = _normalize_path(value)
    return normalized or None


def build_supplement_target_path(*, target_page_path: str) -> Optional[str]:
    """Build the sole allowed target path for an indexed Notion page."""
    normalized_page = normalize_notion_path(target_page_path)
    if normalized_page is None:
        return None
    return f"{normalized_page}/{AI_SUPPLEMENT_ZONE}"


def normalize_supplement_target_path(
    *,
    target_path: str,
    target_page_path: Optional[str],
) -> Optional[str]:
    """Normalize safe formatting and reject targets outside the selected page."""
    normalized_target = normalize_notion_path(target_path)
    if normalized_target is None:
        return None
    if target_page_path is None:
        return normalized_target
    allowed_target = build_supplement_target_path(target_page_path=target_page_path)
    if allowed_target is None or normalized_target != allowed_target:
        return None
    return allowed_target


def _normalize_path(value: str) -> str:
    parts = [part.strip() for part in str(value).strip().split("/") if part.strip()]
    if any(part in {".", ".."} for part in parts):
        return ""
    return "/".join(parts)
