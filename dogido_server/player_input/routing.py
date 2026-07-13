# player_input/routing.py
from __future__ import annotations

from dogido_server.player_input.guardrails import (
    asks_dragon_direction,
    asks_hostile_count,
    asks_inventory,
    asks_save_last_haiku,
    extract_player_haiku,
    should_block_ambient,
    wants_quiet,
)
from dogido_server.player_input.normalize import normalize_player_text
from dogido_server.player_input.types import PlayerInputContext


def route_player_input(raw_text: str | None) -> PlayerInputContext:
    normalized_text = normalize_player_text(raw_text)
    if normalized_text.startswith("/"):
        # スラッシュコマンドはドギドへの話しかけではないので、
        # 会話優先ミュート（player_input_priority_cooldown_ms）を発動しない
        return PlayerInputContext(
            raw_text=raw_text or "",
            normalized_text=normalized_text,
        )
    blocks_ambient = should_block_ambient(normalized_text)
    player_haiku_text = extract_player_haiku(raw_text)
    return PlayerInputContext(
        raw_text=raw_text or "",
        normalized_text=normalized_text,
        breaks_silence=blocks_ambient,
        wants_quiet=wants_quiet(normalized_text),
        should_block_ambient=blocks_ambient,
        asks_hostile_count=asks_hostile_count(normalized_text),
        asks_dragon_direction=asks_dragon_direction(normalized_text),
        asks_save_last_haiku=asks_save_last_haiku(normalized_text),
        asks_inventory=asks_inventory(normalized_text),
        player_haiku_text=player_haiku_text,
    )
