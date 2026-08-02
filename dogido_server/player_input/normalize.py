# player_input/normalize.py
from __future__ import annotations

from dogido_server.player_input.asr_fixes import apply_asr_fixes


def normalize_player_text(raw_text: str | None) -> str:
    """空白正規化 + 既知 STT 誤変換の最小補正（#29）。"""
    text = (raw_text or "").replace("　", " ").strip()
    if not text:
        return ""
    text = " ".join(text.split())
    fixed, _applied = apply_asr_fixes(text)
    return fixed
