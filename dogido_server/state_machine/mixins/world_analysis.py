# state_machine/mixins/world_analysis.py
from __future__ import annotations

from datetime import datetime

from dogido_server.models import GameEvent
from dogido_server.state_machine.constants import *  # noqa: F403
from dogido_server.state_machine.types import DerivedSignals

MATERIAL_TOKEN_LABELS = {
    "oak": "オーク",
    "spruce": "トウヒ",
    "birch": "シラカバ",
    "jungle": "ジャングル",
    "acacia": "アカシア",
    "dark_oak": "ダークオーク",
    "mangrove": "マングローブ",
    "cherry": "サクラ",
    "bamboo": "竹",
    "crimson": "クリムゾン",
    "warped": "ワープド",
    "pale_oak": "ペールオーク",
    "white": "白",
    "orange": "オレンジ",
    "magenta": "マゼンタ",
    "light_blue": "水色",
    "yellow": "黄",
    "lime": "黄緑",
    "pink": "ピンク",
    "gray": "灰",
    "light_gray": "薄灰",
    "cyan": "シアン",
    "purple": "紫",
    "blue": "青",
    "brown": "茶",
    "green": "緑",
    "red": "赤",
    "black": "黒",
}


class WorldAnalysisMixin:
    def _is_foliage_shade_context(self, event: GameEvent) -> bool:
        if event.world.is_submerged:
            return False
        if (event.world.overhead_cover_type or "").lower() != "foliage":
            return False
        if not event.world.sky_visible:
            return False
        if self._normalized_biome(event.world.biome) not in FOLIAGE_SHADE_BIOMES:
            return False
        local_light = event.world.local_light if event.world.local_light is not None else 15
        if local_light > 8:
            return False
        return (event.world.danger_darkness_score or 0.0) >= 0.58

    def _respawn_point_set(self, event: GameEvent) -> bool:
        return bool(event.world.respawn_point_set)

    def _is_emergency_shelter_event(self, event: GameEvent) -> bool:
        if event.world.is_submerged:
            return False
        if self._is_safe_zone_with_door_event(event):
            return False
        wall_count = event.world.cardinal_wall_count or 0
        ceiling_height = event.world.ceiling_height or 24.0
        return wall_count >= 4 and ceiling_height <= self.settings.emergency_shelter_max_ceiling_height

    def _is_emergency_shelter_night(self, event: GameEvent) -> bool:
        time_phase = getattr(event.world.time_phase, "value", event.world.time_phase)
        time_of_day = event.world.time_of_day
        return time_phase == "night" or (
            time_of_day is not None and time_of_day >= self.settings.emergency_shelter_night_start
        )

    def _is_emergency_shelter_morning(self, event: GameEvent) -> bool:
        time_phase = getattr(event.world.time_phase, "value", event.world.time_phase)
        time_of_day = event.world.time_of_day
        return time_phase == "morning" or (
            time_of_day is not None and time_of_day < self.settings.emergency_shelter_morning_cutoff
        )

    def _has_surface_hostile_spawn_started(self, event: GameEvent) -> bool:
        time_of_day = event.world.time_of_day
        if time_of_day is None:
            return False
        weather = self._weather_value(event.world.weather)
        threshold = SURFACE_HOSTILE_SPAWN_TICK_RAIN if weather in {"rain", "thunder"} else SURFACE_HOSTILE_SPAWN_TICK_CLEAR
        return time_of_day >= threshold

    def _home_or_respawn_return_is_unrealistic(self, event: GameEvent) -> bool:
        if self._is_safe_zone_with_door_event(event):
            return False
        if not self._respawn_point_set(event):
            return True
        respawn_distance = event.world.respawn_distance
        if respawn_distance is None:
            return True
        return respawn_distance >= self.settings.emergency_shelter_respawn_distance

    def _is_open_visibility_environment(self, event: GameEvent) -> bool:
        if event.world.is_submerged:
            return True
        enclosure_score = event.world.enclosure_score or 0.0
        cover_type = (event.world.overhead_cover_type or "unknown").lower()
        ceiling_height = event.world.ceiling_height or 0.0
        if event.world.sky_visible and ceiling_height >= 12.0:
            return True
        if not event.world.sky_visible:
            if ceiling_height >= 12.0 and enclosure_score < 0.12:
                return True
            return cover_type in {"foliage", "fluid"} and enclosure_score < 0.18
        if enclosure_score >= 0.18:
            return False
        return True

    def _is_occluded_environment(self, event: GameEvent) -> bool:
        return not self._is_open_visibility_environment(event)

    def _is_occluded_dark_zone_event(self, event: GameEvent) -> bool:
        if event.world.is_submerged:
            return False
        if self._is_emergency_shelter_event(event):
            return False
        if self._is_safe_zone_with_door_event(event):
            return False
        local_light = event.world.local_light if event.world.local_light is not None else 15
        return self._is_occluded_environment(event) and (
            (event.world.danger_darkness_score or 0.0) >= self.settings.occluded_entry_darkness_threshold
            or local_light <= self.settings.occluded_entry_light_threshold
        )

    def _is_submerged_dark_zone_event(self, event: GameEvent) -> bool:
        if not event.world.is_submerged:
            return False
        if (event.world.submerged_depth_blocks or 0) < self.settings.submerged_darkness_depth_threshold:
            return False
        local_light = event.world.local_light if event.world.local_light is not None else 15
        return (
            (event.world.danger_darkness_score or 0.0) >= self.settings.occluded_entry_darkness_threshold
            or local_light <= self.settings.occluded_entry_light_threshold
        )

    def _is_safe_zone_with_door_event(self, event: GameEvent) -> bool:
        if event.world.is_submerged:
            return False
        local_light = event.world.local_light if event.world.local_light is not None else 0
        nearby_doors = event.world.nearby_door_count or 0
        enclosure_score = event.world.enclosure_score or 0.0
        ceiling_height = event.world.ceiling_height or 24.0
        sky_visible = bool(event.world.sky_visible)
        if event.world.safe_zone_with_door is not None:
            if not bool(event.world.safe_zone_with_door):
                return False
            if nearby_doors <= 0 or local_light < 8:
                return False
            if sky_visible and ceiling_height >= 8.0 and enclosure_score < 0.45:
                return False
            return True
        return nearby_doors > 0 and local_light >= 8 and (
            enclosure_score >= 0.18 or ceiling_height <= 5.0 or not bool(event.world.sky_visible)
        )

    def _is_close_audio_ambush(self, event: GameEvent) -> bool:
        if not self._is_occluded_environment(event) or not event.auditory_threats:
            return False
        if not self._prior_audio_gap_exceeded(3000):
            return False
        return any(self._distance_band_rank(threat.distance_band) <= 1 for threat in event.auditory_threats)

    def _is_close_visual_spawn_ambush(self, event: GameEvent) -> bool:
        if not self._is_occluded_environment(event) or not event.visual_threats:
            return False
        if not (self._prior_audio_gap_exceeded(3000) and self._prior_visual_gap_exceeded(3000)):
            return False
        nearest = self._nearest_visual(event.visual_threats)
        return nearest is not None and nearest.distance is not None and nearest.distance <= 4.0

    def _is_skeleton_damage_ambush(self, event: GameEvent, signals: DerivedSignals) -> bool:
        if signals.recent_damage_ms is None or signals.recent_damage_ms > 1000:
            return False
        if not self._prior_audio_gap_exceeded(30000):
            return False
        return any(threat.type == "skeleton" for threat in event.visual_threats)

    def _should_interrupt_dark_push_for_front_ambush(self, event: GameEvent, now: datetime) -> bool:
        return self._peek_dark_push_forward_ambush_target(event, now) is not None

    def _prior_audio_gap_exceeded(self, threshold_ms: int) -> bool:
        return self.state.prior_recent_audio_ms is None or self.state.prior_recent_audio_ms >= threshold_ms

    def _prior_visual_gap_exceeded(self, threshold_ms: int) -> bool:
        return self.state.prior_recent_visual_ms is None or self.state.prior_recent_visual_ms >= threshold_ms

    def _hostile_label(self, hostile_type: str) -> str:
        return HOSTILE_LABELS.get(hostile_type, hostile_type)

    def _mob_label(self, mob_type: str) -> str:
        return MOB_LABELS.get(mob_type, mob_type)

    def _item_label(self, item_id: str | None) -> str:
        if not item_id:
            return ""
        normalized = item_id.split(":")[-1].strip().lower()
        if not normalized or normalized == "air":
            return ""
        mapped = ITEM_LABELS.get(normalized)
        if mapped is not None:
            return mapped
        return self._derived_entry_label(normalized, item_id)

    def _block_label(self, block_id: str | None) -> str:
        if not block_id:
            return ""
        normalized = block_id.split(":")[-1].strip().lower()
        if not normalized or normalized == "air":
            return ""
        mapped = BLOCK_LABELS.get(normalized)
        if mapped is not None:
            return mapped
        return self._derived_entry_label(normalized, block_id)

    def _derived_entry_label(self, normalized: str, original_id: str) -> str:
        if normalized.endswith("_log"):
            return f"{self._material_label(normalized[:-4])}の原木"
        if normalized.endswith("_planks"):
            return f"{self._material_label(normalized[:-7])}の板材"
        if normalized.endswith("_wool"):
            return f"{self._material_label(normalized[:-5])}の羊毛"
        if normalized.endswith("_bed"):
            return f"{self._material_label(normalized[:-4])}のベッド"
        if normalized.endswith("_leaves"):
            return f"{self._material_label(normalized[:-7])}の葉"
        if normalized.isascii():
            return normalized.replace("_", " ")
        return original_id

    def _material_label(self, token: str) -> str:
        normalized = token.strip().lower()
        if not normalized:
            return "その"
        return MATERIAL_TOKEN_LABELS.get(normalized, normalized.replace("_", " "))

    def _biome_label(self, biome: str | None) -> str:
        if not biome:
            return "そのへん"
        normalized = biome.strip().lower()
        if not normalized:
            return "そのへん"
        mapped = BIOME_LABELS.get(normalized)
        if mapped is not None:
            return mapped
        if normalized.isascii():
            return "そのへん"
        return biome

    def _biome_entry(self, biome: str | None) -> dict[str, object] | None:
        if not biome:
            return None
        normalized = biome.strip().lower()
        if not normalized:
            return None
        entry = BIOME_ENTRIES.get(normalized)
        if entry is None:
            return None
        return dict(entry)

    def _biome_group_label(self, biome: str | None) -> str | None:
        entry = self._biome_entry(biome)
        if entry is None:
            return None
        value = entry.get("group_label")
        return str(value) if value else None

    def _is_dry_weather_biome(self, biome: str | None) -> bool:
        entry = self._biome_entry(biome)
        if entry is None:
            return False
        return entry.get("group_id") == "dry"

    def _biome_temperature(self, biome: str | None) -> float | None:
        entry = self._biome_entry(biome)
        if entry is None:
            return None
        return self._biome_metric_value(entry.get("temperature"))

    def _biome_downfall(self, biome: str | None) -> float | None:
        entry = self._biome_entry(biome)
        if entry is None:
            return None
        return self._biome_metric_value(entry.get("downfall"))

    def _biome_snow_start_y(self, biome: str | None) -> int | None:
        entry = self._biome_entry(biome)
        if entry is None:
            return None
        value = entry.get("snow_starts_at_y")
        if value is None:
            return None
        return int(value)

    def _biome_metric_value(self, value: object) -> float | None:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, dict):
            java_value = value.get("java")
            if isinstance(java_value, (int, float)):
                return float(java_value)
            for candidate in value.values():
                if isinstance(candidate, (int, float)):
                    return float(candidate)
        return None

    def _direction_label(self, threat: VisualThreat | AuditoryThreat) -> str:
        return DIRECTION_LABELS.get(threat.direction.horizontal, "近く")
