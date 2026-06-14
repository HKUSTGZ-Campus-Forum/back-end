from __future__ import annotations

import re

STATUS_WORDS = r"not\s*taken|in\s*progress|withdrawn|withdraw|dropped|drop|taken|completed|complete|registered|enrolled|planned|pending"
STATUS_TITLE_RE = re.compile(rf"\b({STATUS_WORDS})\b", re.IGNORECASE)


def status_from_text(value: str | None) -> str | None:
    if not value:
        return None
    normalized = " ".join(value.strip().lower().split())
    if normalized in {"taken", "completed", "complete"}:
        return "completed"
    if normalized in {"registered", "enrolled", "in progress"}:
        return "in_progress"
    if normalized in {"planned", "pending"}:
        return "planned"
    if normalized == "not taken":
        return "not_interested"
    if normalized in {"withdrawn", "withdraw", "dropped", "drop"}:
        return "withdrawn"
    return None


def status_from_text_fragment(value: str | None) -> str | None:
    if not value:
        return None
    match = STATUS_TITLE_RE.search(value)
    if not match:
        return None
    return status_from_text(match.group(1))


def clean_copied_status_text(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = STATUS_TITLE_RE.sub(" ", value)
    return " ".join(cleaned.split()) or None
