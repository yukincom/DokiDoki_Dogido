# llm/haiku.py
from __future__ import annotations

import re


def is_haiku_line_usable(
    text: str,
    line_index: int,
    details: dict[str, object] | None = None,
) -> bool:
    """出典検証前の一行を、かな・音数・hard制約だけで判定する。"""

    if line_index not in (0, 1, 2) or not text:
        return False
    if "\n" in text or "\r" in text or not _is_haiku_script_ok(text):
        return False
    if _contains_forbidden_gibberish_sequence(text):
        return False
    target = (5, 7, 5)[line_index]
    if abs(count_japanese_sounds(text) - target) > 1:
        return False
    return _respects_haiku_constraints(text, details)


def _is_haiku_script_ok(text: str) -> bool:
    if re.search(r"[A-Za-z0-9\u4e00-\u9fff]", text):
        return False
    return bool(re.fullmatch(r"[\u3041-\u309f\u30a1-\u30ffー\s／/|]+", text))


def _contains_forbidden_gibberish_sequence(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    forbidden = (
        "あいうえお",
        "かきくけこ",
        "さしすせそ",
        "たちつてと",
        "なにぬねの",
        "はひふへほ",
        "まみむめも",
        "やゆよ",
        "らりるれろ",
        "アイウエオ",
        "カキクケコ",
        "サシスセソ",
        "タチツテト",
        "ナニヌネノ",
        "ハヒフヘホ",
        "マミムメモ",
        "ヤユヨ",
        "ラリルレロ",
    )
    return any(pattern in compact for pattern in forbidden)


def count_japanese_sounds(text: str) -> int:
    compact = re.sub(r"\s+", "", text)
    count = 0
    for index, ch in enumerate(compact):
        count += haiku_char_sound(ch, index)
    return count


def haiku_char_sound(ch: str, index: int) -> int:
    if ch in {"ゃ", "ゅ", "ょ", "ャ", "ュ", "ョ", "ぁ", "ぃ", "ぅ", "ぇ", "ぉ", "ァ", "ィ", "ゥ", "ェ", "ォ", "ゎ", "ヮ"}:
        return 0 if index > 0 else 1
    return 1


def _respects_haiku_constraints(text: str, details: dict[str, object] | None) -> bool:
    if not isinstance(details, dict):
        return True
    constraints = details.get("haiku_constraints")
    if not isinstance(constraints, dict):
        return True
    forbidden_terms = [
        _normalize_haiku_term(str(term))
        for term in constraints.get("forbidden_terms", [])
        if term
    ]
    if not forbidden_terms:
        return True
    normalized = _normalize_haiku_term(text)
    return not any(term and term in normalized for term in forbidden_terms)


def _normalize_haiku_term(text: str) -> str:
    compact = re.sub(r"\s+", "", text)
    chars: list[str] = []
    for ch in compact:
        code = ord(ch)
        if 0x30A1 <= code <= 0x30F6:
            chars.append(chr(code - 0x60))
            continue
        chars.append(ch)
    return "".join(chars)
