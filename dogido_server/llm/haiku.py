# llm/haiku.py
from __future__ import annotations

import re


def is_haiku_line_usable(
    text: str,
    line_index: int,
    details: dict[str, object] | None = None,
) -> bool:
    """出典検証前の一行を、かな・音数・hard制約だけで判定する。"""

    return not haiku_line_failure_reasons(text, line_index, details)


def haiku_line_failure_reasons(
    text: str,
    line_index: int,
    details: dict[str, object] | None = None,
) -> tuple[str, ...]:
    """採否と同じコード検査を、再生成へ返せる閉じた理由へ分解する。"""

    reasons: list[str] = []
    if line_index not in (0, 1, 2):
        return ("invalid_line_index",)
    if not text:
        return ("empty_line",)
    if "\n" in text or "\r" in text:
        reasons.append("multiline")
    if not _is_haiku_script_ok(text):
        reasons.append("invalid_script")
    if _contains_forbidden_gibberish_sequence(text):
        reasons.append("gibberish_sequence")
    target = (5, 7, 5)[line_index]
    sound_count = count_japanese_sounds(text)
    if sound_count < target - 1:
        reasons.append("meter_too_short")
    elif sound_count > target + 1:
        reasons.append("meter_too_long")
    if not _respects_haiku_constraints(text, details):
        reasons.append("hard_forbidden_term")
    return tuple(reasons)


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
