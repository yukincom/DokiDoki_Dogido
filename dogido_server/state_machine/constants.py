# state_machine/constants.py
from __future__ import annotations

from dogido_server.entry_catalog import biome_entries, biome_labels, block_labels, item_labels
from dogido_server.entity_voice_catalog import MOB_VOICE_LABELS, RUNTIME_HOSTILE_LABELS
from dogido_server.models import HorizontalDirection
from dogido_server.state_machine.response_catalog import classic_ushiro_call_text, response_text

DAYLIGHT_BURN_HOSTILES = {"skeleton", "zombie", "drowned", "zombie_villager", "zombified_piglin", "phantom"}

HOSTILE_LABELS = dict(RUNTIME_HOSTILE_LABELS)

HOSTILE_EFFECTIVE_RANGE = {
    "charged_creeper": 4.5,
    "creeper": 4.5,
    "zombie": 2.2,
    "zombie_villager": 2.2,
    "drowned": 2.2,
    "spider": 2.5,
    "skeleton": 16.0,
    "witch": 10.0,
    "enderman": 3.0,
    "phantom": 4.0,
}

RANGED_HOSTILES = {
    "skeleton",
    "witch",
    "blaze",
    "ghast",
    "pillager",
    "drowned",
    "evoker",
}

HIGH_THREAT_SUPPORT_HOSTILES = {
    "charged_creeper",
    "creeper",
    "enderman",
    "warden",
    "ravager",
    "elder_guardian",
    "wither",
    "ender_dragon",
}

FLYING_HOSTILES = {
    "blaze",
    "ender_dragon",
    "ghast",
    "phantom",
    "vex",
    "wither",
}

BOSS_HOSTILES = {
    "warden",
    "wither",
    "ender_dragon",
    "elder_guardian",
    "ravager",
}

TACTICAL_BOSS_HOSTILES = {
    "ender_dragon",
}

REVEAL_ONLY_BOSS_HOSTILES = {
    "warden",
    "wither",
    "elder_guardian",
}

USHIRO_CALL = classic_ushiro_call_text()
DAYLIGHT_WATER_CALL = response_text("combat", "daylight", "water_generic")
DAYLIGHT_RAIN_CALL = response_text("combat", "daylight", "rain_started")
CHARGED_CREEPER_CALL = response_text("combat", "calls", "charged_creeper")
EMERGENCY_SHELTER_CALL = response_text("darkness", "emergency_shelter", "advice")
EMERGENCY_SHELTER_MORNING_CALL = response_text("darkness", "emergency_shelter", "morning_release")
EVENING_SURFACE_WARNING_CALL = response_text("darkness", "night_warning", "surface_evening")
SURFACE_HOSTILE_SPAWN_TICK_CLEAR = 13188
SURFACE_HOSTILE_SPAWN_TICK_RAIN = 12969

COLD_WEATHER_BIOMES = {
    "snowy_plains",
    "ice_spikes",
    "snowy_taiga",
    "snowy_slopes",
    "frozen_river",
    "snowy_beach",
    "frozen_ocean",
    "deep_frozen_ocean",
    "frozen_peaks",
    "jagged_peaks",
    "grove",
}

FOLIAGE_SHADE_BIOMES = {
    "forest",
    "flower_forest",
    "birch_forest",
    "old_growth_birch_forest",
    "dark_forest",
    "jungle",
    "bamboo_jungle",
    "sparse_jungle",
    "taiga",
    "old_growth_pine_taiga",
    "old_growth_spruce_taiga",
    "snowy_taiga",
    "grove",
    "mangrove_swamp",
}

NIGHT_WARNING_SUPPRESSED_BIOMES = {
    "dark_forest",
    "mushroom_fields",
    "pale_garden",
}

SURFACE_HOSTILE_SAFE_BIOMES = {
    "mushroom_fields",
}

MOB_LABELS = dict(MOB_VOICE_LABELS)

BIOME_ENTRIES = biome_entries()
BIOME_LABELS = biome_labels()
ITEM_LABELS = item_labels()
BLOCK_LABELS = block_labels()

WEATHER_LABELS = {
    "clear": "晴れ",
    "rain": "雨",
    "thunder": "雷",
}

TIME_PHASE_LABELS = {
    "morning": "朝",
    "day": "昼",
    "evening": "夕方",
    "night": "夜",
}

DIRECTION_LABELS = {
    HorizontalDirection.FRONT: "前",
    HorizontalDirection.FRONT_RIGHT: "右前",
    HorizontalDirection.RIGHT: "右",
    HorizontalDirection.BACK_RIGHT: "右後ろ",
    HorizontalDirection.BACK: "後ろ",
    HorizontalDirection.BACK_LEFT: "左後ろ",
    HorizontalDirection.LEFT: "左",
    HorizontalDirection.FRONT_LEFT: "左前",
    None: "近く",
}
