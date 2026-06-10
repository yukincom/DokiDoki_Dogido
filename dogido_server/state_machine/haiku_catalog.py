# state_machine/haiku_catalog.py
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).resolve().parents[2] / "data" / "fallbacks" / "haiku.json"


@dataclass(frozen=True, slots=True)
class HaikuFallbackContext:
    biome: str
    time_phase: str | None
    weather: str | None
    player_y: float | None
    danger_darkness_score: float | None
    visual_threat_types: frozenset[str]
    passive_mob_types: frozenset[str]
    nearby_resources: tuple[tuple[str, float | None], ...]


@lru_cache(maxsize=1)
def load_haiku_catalog() -> dict[str, Any]:
    with CATALOG_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def resolve_fallback_haiku(context: HaikuFallbackContext) -> str:
    catalog = load_haiku_catalog()
    biome_groups = catalog.get("biome_groups", {})
    for rule in catalog.get("rules", []):
        if _matches_rule(context, rule, biome_groups):
            return str(rule["line"])

    defaults = catalog.get("defaults", {})
    if context.biome in defaults:
        return str(defaults[context.biome])

    for rule in catalog.get("group_defaults", []):
        if _matches_rule(context, rule, biome_groups):
            return str(rule["line"])
    return str(catalog.get("under_construction_line", "今、考え中やねん…"))


def resolve_llm_failed_haiku() -> str:
    catalog = load_haiku_catalog()
    return str(catalog.get("llm_failed_line", "まとまらんかった。。。"))


def _matches_rule(
    context: HaikuFallbackContext,
    rule: dict[str, Any],
    biome_groups: dict[str, list[str]],
) -> bool:
    if not _matches_biome(context.biome, rule, biome_groups):
        return False
    if not _matches_exact(context.weather, rule.get("weather")):
        return False
    if not _matches_exact(context.time_phase, rule.get("time_phase")):
        return False
    if not _matches_player_y(context.player_y, rule):
        return False
    if not _matches_danger_darkness_score(context.danger_darkness_score, rule):
        return False
    if not _matches_any(context.visual_threat_types, rule.get("visual_threat_types_any")):
        return False
    if not _matches_any(context.passive_mob_types, rule.get("passive_mob_types_any")):
        return False
    if not _matches_nearby_resources(context.nearby_resources, rule):
        return False
    return True


def _matches_biome(biome: str, rule: dict[str, Any], biome_groups: dict[str, list[str]]) -> bool:
    biomes = rule.get("biomes") or []
    if biomes and biome not in biomes:
        return False

    group_names = rule.get("biome_groups") or []
    if not group_names:
        return True

    grouped_biomes: set[str] = set()
    for name in group_names:
        grouped_biomes.update(biome_groups.get(name, []))
    return biome in grouped_biomes


def _matches_exact(actual: str | None, expected: Any) -> bool:
    if expected is None:
        return True
    return actual == expected


def _matches_player_y(player_y: float | None, rule: dict[str, Any]) -> bool:
    min_y = rule.get("player_y_min")
    if min_y is not None and (player_y is None or player_y < float(min_y)):
        return False
    max_y = rule.get("player_y_max")
    if max_y is not None and (player_y is None or player_y > float(max_y)):
        return False
    return True


def _matches_danger_darkness_score(danger_darkness_score: float | None, rule: dict[str, Any]) -> bool:
    min_score = rule.get("danger_darkness_score_min")
    if min_score is not None and (danger_darkness_score is None or danger_darkness_score < float(min_score)):
        return False
    max_score = rule.get("danger_darkness_score_max")
    if max_score is not None and (danger_darkness_score is None or danger_darkness_score > float(max_score)):
        return False
    return True


def _matches_any(actual_values: frozenset[str], expected_values: Any) -> bool:
    if not expected_values:
        return True
    return bool(actual_values.intersection(str(value) for value in expected_values))


def _matches_nearby_resources(
    actual_resources: tuple[tuple[str, float | None], ...],
    rule: dict[str, Any],
) -> bool:
    expected_names = {_normalize_name(value) for value in rule.get("nearby_resource_names_any") or []}
    expected_suffixes = tuple(str(value) for value in rule.get("nearby_resource_suffixes_any") or [])
    distance_max = rule.get("nearby_resource_distance_max")
    if not expected_names and not expected_suffixes:
        return True

    for name, distance in actual_resources:
        if distance_max is not None and (distance is None or distance > float(distance_max)):
            continue
        if expected_names and name in expected_names:
            return True
        if expected_suffixes and any(name.endswith(suffix) for suffix in expected_suffixes):
            return True
    return False


def _normalize_name(name: Any) -> str:
    return str(name).split(":")[-1].strip().lower()
