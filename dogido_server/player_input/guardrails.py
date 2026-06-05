# player_input/guardrails.py
from __future__ import annotations

HUSH_KEYWORDS = ("うるさい", "静かにして", "黙れ")
HOSTILE_QUERY_KEYWORDS = ("敵", "モンスター", "モブ", "敵対モブ")
HOSTILE_COUNT_QUERY_KEYWORDS = ("何体", "なんたい", "何匹", "残り", "何人", "何個体")


def wants_quiet(normalized_text: str) -> bool:
    return bool(normalized_text) and any(keyword in normalized_text for keyword in HUSH_KEYWORDS)


def should_block_ambient(normalized_text: str) -> bool:
    return bool(normalized_text)


def asks_hostile_count(normalized_text: str) -> bool:
    if not normalized_text:
        return False
    if not any(keyword in normalized_text for keyword in HOSTILE_COUNT_QUERY_KEYWORDS):
        return False
    if any(keyword in normalized_text for keyword in HOSTILE_QUERY_KEYWORDS):
        return True
    return "残り" in normalized_text or "あと" in normalized_text
