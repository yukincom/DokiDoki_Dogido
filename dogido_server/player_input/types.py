# player_input/types.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class ReadingCorrection:
    surface: str
    reading: str
    wrong_reading: str | None = None


@dataclass(slots=True)
class HaikuRecallQuery:
    """明示的な句の想起条件（壁時計・場所）。プロンプト常駐用ではない。"""

    biome_id: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    time_label: str | None = None


@dataclass(slots=True)
class PlayerInputContext:
    raw_text: str = ""
    normalized_text: str = ""
    breaks_silence: bool = False
    wants_quiet: bool = False
    should_block_ambient: bool = False
    asks_hostile_count: bool = False
    asks_dragon_direction: bool = False
    asks_save_last_haiku: bool = False
    asks_inventory: bool = False
    player_haiku_text: str | None = None
    # 川柳フィードバック（長期保存・読み修正）。player_chat には回さない。
    revised_haiku_text: str | None = None
    reading_correction: ReadingCorrection | None = None
    asks_haiku_recall: bool = False
    haiku_recall_biome_hint: str | None = None
    haiku_recall_query: HaikuRecallQuery | None = None
