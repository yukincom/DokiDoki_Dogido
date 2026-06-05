# player_input/routing.py
from __future__ import annotations

from dogido_server.player_input.guardrails import asks_hostile_count, should_block_ambient, wants_quiet
from dogido_server.player_input.normalize import normalize_player_text
from dogido_server.player_input.types import PlayerInputContext


def route_player_input(raw_text: str | None) -> PlayerInputContext:
    normalized_text = normalize_player_text(raw_text)
    blocks_ambient = should_block_ambient(normalized_text)
    return PlayerInputContext(
        raw_text=raw_text or "",
        normalized_text=normalized_text,
        breaks_silence=blocks_ambient,
        wants_quiet=wants_quiet(normalized_text),
        should_block_ambient=blocks_ambient,
        asks_hostile_count=asks_hostile_count(normalized_text),
    )
