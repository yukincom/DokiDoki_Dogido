# player_input/normalize.py
from __future__ import annotations


def normalize_player_text(raw_text: str | None) -> str:
    text = (raw_text or "").replace("　", " ").strip()
    if not text:
        return ""
    return " ".join(text.split())
