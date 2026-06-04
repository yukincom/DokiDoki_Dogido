# llm/haiku.py
from __future__ import annotations

import re


def clean_haiku_output(text: str | None) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    cleaned = cleaned.replace("<|im_end|>", "").replace("<|endoftext|>", "").strip()
    cleaned = re.sub(r"(?is)^here'?s a thinking process:.*?(?:final answer:|answer:|返答:|出力:|セリフ:)", "", cleaned).strip()
    raw_lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    candidates: list[str] = []
    for raw_line in raw_lines:
        line = re.sub(r"^(Final answer|Answer|返答|出力|セリフ)\s*[:：]\s*", "", raw_line, flags=re.IGNORECASE).strip()
        if not line:
            continue
        if line.startswith(("Here's a thinking process", "Here is a thinking process", "Let's think")):
            continue
        if re.match(r"^(Role|Persona|Analyze User Input)\s*[:：]", line, flags=re.IGNORECASE):
            continue
        candidates.append(line.strip("「」\"' "))
    if not candidates:
        return ""
    if len(candidates) == 1:
        merged = re.sub(r"[／/|]", "\n", candidates[0])
        return "\n".join(part.strip() for part in merged.splitlines() if part.strip())
    return "\n".join(candidates[-3:])


def is_haiku_usable_output(text: str) -> bool:
    if not text:
        return False
    if re.search(r"[A-Za-z0-9\u4e00-\u9fff]", text):
        return False
    if not re.fullmatch(r"[\u3041-\u309f\u30a1-\u30ffー\s／/|]+", text):
        return False
    if _contains_forbidden_gibberish_sequence(text):
        return False
    phrases = split_haiku_phrases(text)
    if phrases is None:
        return False
    counts = [count_japanese_sounds(phrase) for phrase in phrases]
    targets = (5, 7, 5)
    return all(abs(count - target) <= 1 for count, target in zip(counts, targets))


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


def split_haiku_phrases(text: str) -> list[str] | None:
    explicit_parts = [part.strip() for part in re.split(r"[\n／/|]+", text) if part.strip()]
    if len(explicit_parts) == 3:
        return explicit_parts
    whitespace_parts = [part.strip() for part in re.split(r"\s+", text.strip()) if part.strip()]
    if len(whitespace_parts) == 3:
        return whitespace_parts

    compact = re.sub(r"\s+", "", text)
    if not compact:
        return None

    cumulative: list[int] = []
    count = 0
    for index, ch in enumerate(compact):
        count += haiku_char_sound(ch, index)
        cumulative.append(count)

    if len(cumulative) < 3:
        return None

    for left in range(len(cumulative) - 2):
        first = cumulative[left]
        if abs(first - 5) > 1:
            continue
        for right in range(left + 1, len(cumulative) - 1):
            second = cumulative[right] - first
            third = cumulative[-1] - cumulative[right]
            if abs(second - 7) <= 1 and abs(third - 5) <= 1:
                return [
                    compact[: left + 1],
                    compact[left + 1 : right + 1],
                    compact[right + 1 :],
                ]
    return None


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
