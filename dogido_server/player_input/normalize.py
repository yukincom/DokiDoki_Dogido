# player_input/normalize.py
from __future__ import annotations

from dogido_server.player_input.asr_fixes import apply_asr_fixes


VOICE_SHORT_TEXT_MAX_CHARS = 3
VOICE_SHORT_TEXT_ALLOWLIST = frozenset({"ドギド", "おーい"})


def normalize_player_text(raw_text: str | None) -> str:
    """空白正規化 + 既知 STT 誤変換の最小補正（#29）。"""
    text = (raw_text or "").replace("　", " ").strip()
    if not text:
        return ""
    text = " ".join(text.split())
    fixed, _applied = apply_asr_fixes(text)
    return fixed


def is_too_short_voice_text(raw_text: str | None) -> bool:
    """音声認識の短い誤検出を落とす。呼びかけだけは短くても通す。"""
    normalized = normalize_player_text(raw_text)
    compact = "".join(normalized.split())
    return (
        bool(compact)
        and len(compact) <= VOICE_SHORT_TEXT_MAX_CHARS
        and compact not in VOICE_SHORT_TEXT_ALLOWLIST
    )
