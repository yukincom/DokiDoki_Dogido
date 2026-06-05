# state_machine/fallback_catalog.py
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from dogido_server.entity_voice_catalog import PASSIVE_MOB_VOICE_LABELS
from dogido_server.state_machine.ambient_mob_catalog import AmbientMobReactionContext, ambient_mob_fallback_candidates

FALLBACKS_DIR = Path(__file__).resolve().parents[2] / "data" / "fallbacks"


@lru_cache(maxsize=None)
def load_fallback_catalog(topic: str) -> dict[str, Any]:
    with (FALLBACKS_DIR / f"{topic}.json").open("r", encoding="utf-8") as handle:
        return json.load(handle)


def fallback_text(topic: str, *keys: str, **replacements: str) -> str:
    node: Any = load_fallback_catalog(topic)
    for key in keys:
        node = node[key]
    text = str(node)
    for name, value in replacements.items():
        text = text.replace(f"{{{name}}}", value)
    return text


def death_fallback_text(death_cause: str | None) -> str:
    cause = (death_cause or "").lower()
    if any(name in cause for name in ("zombie", "creeper", "skeleton", "witch", "spider", "enderman")):
        return fallback_text("death", "hostile")
    if any(word in cause for word in ("fall", "fell", "void", "accident")):
        return fallback_text("death", "fall")
    return fallback_text("death", "default")


def dark_push_after_breath_fallback(time_phase: str | None, *, prefix: str = "") -> str:
    if time_phase == "evening":
        return fallback_text("general", "darkness", "dark_push_after_breath_evening", prefix=prefix)
    if time_phase == "night":
        return fallback_text("general", "darkness", "dark_push_after_breath_night", prefix=prefix)
    return fallback_text("general", "darkness", "dark_push_after_breath_default", prefix=prefix)


def fallback_prewarm_texts(call_name: str | None) -> list[str]:
    prefix = _call_name_prefix(call_name)
    texts = [
        fallback_text("aftermath", "line"),
        fallback_text("death", "default"),
        fallback_text("death", "hostile"),
        fallback_text("death", "fall"),
        fallback_text("general", "darkness", "darkness_escape", prefix=prefix),
        fallback_text("general", "darkness", "occluded_entry_with_light", prefix=prefix),
        fallback_text("general", "darkness", "occluded_entry_no_light", prefix=prefix),
        fallback_text("general", "darkness", "dark_push_no_light", prefix=prefix),
        fallback_text("general", "darkness", "dark_push_after_breath_default", prefix=prefix),
        fallback_text("general", "darkness", "dark_push_after_breath_evening", prefix=prefix),
        fallback_text("general", "darkness", "dark_push_after_breath_night", prefix=prefix),
        fallback_text("general", "darkness", "emergency_shelter_relief", prefix=prefix),
        fallback_text("general", "darkness", "light_crafted", prefix=prefix),
        fallback_text("general", "combat", "daylight_water_skeleton"),
        fallback_text("general", "combat", "newly_burning_visual"),
        fallback_text("general", "weather_transition", "clear_after_bad_weather"),
        fallback_text("general", "weather_transition", "overcast_after_thunder"),
        fallback_text("general", "weather_transition", "overcast_started"),
        fallback_text("general", "weather_transition", "snow_after_thunder"),
        fallback_text("general", "weather_transition", "snow_started"),
        fallback_text("general", "weather_transition", "rain_after_thunder"),
        fallback_text("general", "weather_transition", "rain_started"),
        fallback_text("general", "weather_transition", "dry_thunder_after_overcast"),
        fallback_text("general", "weather_transition", "dry_thunder_started"),
        fallback_text("general", "weather_transition", "blizzard_after_snow"),
        fallback_text("general", "weather_transition", "blizzard_started"),
        fallback_text("general", "weather_transition", "thunder_after_rain"),
        fallback_text("general", "weather_transition", "thunder_started"),
    ]
    texts.extend(_ambient_mob_prewarm_texts())
    return _dedupe(texts)


def _ambient_mob_prewarm_texts() -> list[str]:
    texts: list[str] = []
    for mob_type, mob_label in PASSIVE_MOB_VOICE_LABELS.items():
        context = AmbientMobReactionContext(
            mob_type=mob_type,
            mob_label=mob_label,
            inventory_item_ids=frozenset(),
        )
        texts.extend(ambient_mob_fallback_candidates(context))
    return texts


def _call_name_prefix(call_name: str | None) -> str:
    normalized = (call_name or "プレイヤー").strip() or "プレイヤー"
    if normalized == "プレイヤー":
        return ""
    return f"{normalized}、"


def _dedupe(texts: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for text in texts:
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return deduped
