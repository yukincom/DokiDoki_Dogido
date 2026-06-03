# state_machine/response_catalog.py
from __future__ import annotations

import json
from hashlib import sha1
from functools import lru_cache
from pathlib import Path
from typing import Any

RESPONSES_DIR = Path(__file__).resolve().parents[2] / "data" / "responses" / "ques"


@lru_cache(maxsize=None)
def load_response_catalog(topic: str) -> dict[str, Any]:
    with (RESPONSES_DIR / f"{topic}.json").open("r", encoding="utf-8") as handle:
        return json.load(handle)


def response_text(topic: str, *keys: str, **replacements: str) -> str:
    node: Any = load_response_catalog(topic)
    for key in keys:
        node = node[key]
    text = str(node)
    for name, value in replacements.items():
        text = text.replace(f"{{{name}}}", value)
    return text


def classic_ushiro_call_text() -> str:
    return response_text("combat", "calls", "classic_ushiro_call")


def named_ushiro_call_text(player_name: str | None) -> str:
    normalized = (player_name or "プレイヤー").strip() or "プレイヤー"
    return response_text("combat", "calls", "named_ushiro_call", player_name=normalized)


def selected_ushiro_call_text(player_name: str | None, seed_text: str) -> str:
    if _use_classic_ushiro_call(seed_text):
        return classic_ushiro_call_text()
    return named_ushiro_call_text(player_name)


def is_ushiro_call_text(text: str | None) -> bool:
    if not text:
        return False
    return text.endswith("うしろ！うしろ〜！")


def special_biome_entry_lines(biome: str, phase_key: str) -> tuple[str, ...] | None:
    catalog = load_response_catalog("biome")
    reactions = catalog.get("reactions", {})
    reaction = reactions.get(biome)
    if not isinstance(reaction, dict):
        return None
    payload = reaction.get(phase_key) or reaction.get("default")
    if not isinstance(payload, dict):
        return None
    lines = payload.get("lines")
    if isinstance(lines, list):
        return tuple(str(line) for line in lines)
    group_name = payload.get("line_group")
    if not isinstance(group_name, str):
        return None
    groups = catalog.get("line_groups", {})
    group_lines = groups.get(group_name)
    if not isinstance(group_lines, list):
        return None
    return tuple(str(line) for line in group_lines)


def response_prewarm_texts(player_name: str | None) -> list[str]:
    return [
        classic_ushiro_call_text(),
        named_ushiro_call_text(player_name),
        response_text("combat", "calls", "charged_creeper"),
        response_text("combat", "pressure", "stalled_visual_suppressed"),
        response_text("combat", "pressure", "stalled_visual"),
        response_text("combat", "pressure", "hostile_massive"),
        response_text("combat", "pressure", "hostile_massive_suppressed"),
        response_text("combat", "daylight", "water_generic"),
        response_text("combat", "daylight", "rain_started"),
        response_text("combat", "daylight", "dry_overcast"),
        response_text("combat", "daylight", "dry_thunder"),
        response_text("darkness", "emergency_shelter", "advice"),
        response_text("darkness", "emergency_shelter", "morning_release"),
        response_text("darkness", "sleep", "near_respawn_bed"),
        response_text("darkness", "sleep", "nearby_bed"),
        response_text("darkness", "night_warning", "surface_evening"),
        response_text("darkness", "night_warning", "cave_or_submerged", phase_label="夕方"),
        response_text("darkness", "night_warning", "cave_or_submerged", phase_label="夜"),
        response_text("darkness", "darkness", "submerged_entry"),
        *special_biome_entry_lines("mushroom_fields", "day"),
        *special_biome_entry_lines("mushroom_fields", "night"),
        *special_biome_entry_lines("forest", "day"),
        *special_biome_entry_lines("old_growth_birch_forest", "day"),
        *special_biome_entry_lines("old_growth_pine_taiga", "day"),
        *special_biome_entry_lines("old_growth_spruce_taiga", "day"),
        *special_biome_entry_lines("dark_forest", "default"),
        *special_biome_entry_lines("grove", "day"),
        *special_biome_entry_lines("bamboo_jungle", "day"),
        *special_biome_entry_lines("jungle", "day"),
        *special_biome_entry_lines("dripstone_caves", "default"),
        *special_biome_entry_lines("lush_caves", "default"),
        *special_biome_entry_lines("deep_dark", "default"),
        *special_biome_entry_lines("pale_garden", "day"),
        *special_biome_entry_lines("pale_garden", "night"),
    ]


def _use_classic_ushiro_call(seed_text: str) -> bool:
    return sha1(seed_text.encode("utf-8")).digest()[0] < 26
