"""音声専用の、全文が叫び声だけかを判定する閉じた規則。"""

from __future__ import annotations

import re
import unicodedata


_EDGE_PUNCTUATION = " \t\r\n、,。.!！?？〜~…()（）"
_PURE_VOCALIZATIONS = (
    re.compile(r"う[おぉォオー〜~]{2,}"),
    re.compile(r"う[わあぁァアー〜~]{2,}"),
    re.compile(r"わ[あぁァアー〜~]{2,}"),
    re.compile(r"[ぎひき][ゃャあぁァアー〜~]{2,}"),
    re.compile(r"[あぁァアうぅゥウ][あぁァアうぅゥウー〜~]{3,}"),
)


def is_pure_voice_vocalization(text: str | None) -> bool:
    """引用・typed入力・後続文を含めず、純粋な反復叫声だけを返す。"""

    normalized = unicodedata.normalize("NFKC", text or "").strip(_EDGE_PUNCTUATION)
    if not normalized or any(mark in normalized for mark in ("「", "」", "『", "』")):
        return False
    compact = re.sub(r"[\s、,。.!！?？…]+", "", normalized)
    return any(pattern.fullmatch(compact) for pattern in _PURE_VOCALIZATIONS)


__all__ = ["is_pure_voice_vocalization"]
