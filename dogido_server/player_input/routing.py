# player_input/routing.py
from __future__ import annotations

import logging

from dogido_server.player_input.asr_fixes import apply_asr_fixes
from dogido_server.player_input.guardrails import (
    asks_about_sound,
    asks_dragon_direction,
    asks_haiku_recall,
    asks_hostile_count,
    asks_inventory,
    asks_save_last_haiku,
    extract_player_haiku,
    extract_reading_correction,
    extract_revised_haiku,
    haiku_recall_biome_hint,
    parse_haiku_time_range,
    should_block_ambient,
    wants_quiet,
)
from dogido_server.player_input.normalize import normalize_player_text
from dogido_server.player_input.types import HaikuRecallQuery, PlayerInputContext, ReadingCorrection

LOGGER = logging.getLogger("uvicorn.error")


def route_player_input(
    raw_text: str | None,
    *,
    interpreted_text: str | None = None,
) -> PlayerInputContext:
    # raw/normalized は明示操作用に保持し、文脈補正後は semantic 面だけに載せる。
    original = (raw_text or "").replace("　", " ").strip()
    original = " ".join(original.split()) if original else ""
    normalized_text = normalize_player_text(raw_text)
    normalized_interpreted = normalize_player_text(interpreted_text)
    if original and normalized_text and original != normalized_text:
        _, applied = apply_asr_fixes(original)
        if applied:
            LOGGER.warning(
                "asr_fix applied=%s original=%s fixed=%s",
                ",".join(f"{w}->{r}" for w, r in applied),
                original[:80],
                normalized_text[:80],
            )
    spoken = normalized_text if normalized_text else (raw_text or "")
    if normalized_text.startswith("/"):
        # スラッシュコマンドはドギドへの話しかけではないので、
        # 会話優先ミュート（player_input_priority_cooldown_ms）を発動しない
        return PlayerInputContext(
            raw_text=spoken,
            normalized_text=normalized_text,
            interpreted_text=normalized_interpreted or spoken,
        )
    blocks_ambient = should_block_ambient(normalized_text)
    player_haiku_text = extract_player_haiku(raw_text)
    revised_haiku_text = extract_revised_haiku(raw_text)
    reading_tuple = extract_reading_correction(raw_text)
    reading_correction = None
    if reading_tuple is not None:
        surface, reading, wrong = reading_tuple
        reading_correction = ReadingCorrection(
            surface=surface,
            reading=reading,
            wrong_reading=wrong,
        )
    recall = asks_haiku_recall(normalized_text)
    biome_hint = None
    recall_query = None
    if recall:
        from dogido_server.entry_catalog import resolve_biome_place_from_text

        place = resolve_biome_place_from_text(normalized_text)
        biome_hint = str(place["biome_id"]) if place.get("biome_id") else None
        biome_ids = tuple(sorted(str(x) for x in (place.get("biome_ids") or ())))
        group_ids = tuple(sorted(str(x) for x in (place.get("group_ids") or ())))
        place_label = str(place["place_label"]) if place.get("place_label") else None
        since, until, time_label = parse_haiku_time_range(normalized_text)
        recall_query = HaikuRecallQuery(
            biome_id=biome_hint,
            biome_ids=biome_ids,
            group_ids=group_ids,
            place_label=place_label,
            since=since,
            until=until,
            time_label=time_label,
        )
    return PlayerInputContext(
        raw_text=spoken,
        normalized_text=normalized_text,
        interpreted_text=normalized_interpreted or spoken,
        breaks_silence=blocks_ambient,
        wants_quiet=wants_quiet(normalized_text),
        should_block_ambient=blocks_ambient,
        asks_hostile_count=asks_hostile_count(normalized_text),
        asks_dragon_direction=asks_dragon_direction(normalized_text),
        asks_save_last_haiku=asks_save_last_haiku(normalized_text),
        asks_inventory=asks_inventory(normalized_text),
        asks_about_sound=asks_about_sound(normalized_text),
        player_haiku_text=player_haiku_text,
        revised_haiku_text=revised_haiku_text,
        reading_correction=reading_correction,
        asks_haiku_recall=recall,
        haiku_recall_biome_hint=biome_hint,
        haiku_recall_query=recall_query,
    )
