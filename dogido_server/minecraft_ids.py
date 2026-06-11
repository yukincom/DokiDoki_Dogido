from __future__ import annotations


def normalize_minecraft_id(value: str | None) -> str | None:
    normalized = (value or "").strip().lower()
    if not normalized:
        return None
    return normalized.split(":")[-1]
