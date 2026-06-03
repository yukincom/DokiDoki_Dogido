# player_input/guardrails.py
from __future__ import annotations

HUSH_KEYWORDS = ("うるさい", "静かにして", "黙れ")


def wants_quiet(normalized_text: str) -> bool:
    return bool(normalized_text) and any(keyword in normalized_text for keyword in HUSH_KEYWORDS)


def should_block_ambient(normalized_text: str) -> bool:
    return bool(normalized_text)
