# player_input/types.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class PlayerInputContext:
    raw_text: str = ""
    normalized_text: str = ""
    breaks_silence: bool = False
    wants_quiet: bool = False
    should_block_ambient: bool = False
