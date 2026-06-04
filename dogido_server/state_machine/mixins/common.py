# state_machine/mixins/common.py
from __future__ import annotations

from datetime import datetime

from dogido_server.models import EventName, GameEvent
from dogido_server.state_machine.fallback_catalog import fallback_text
from dogido_server.state_machine.constants import *  # noqa: F403
from dogido_server.state_machine.response_catalog import response_text, selected_ushiro_call_text, special_biome_entry_lines


class CommonMixin:
    def _effective_time_phase(self, event: GameEvent) -> str | None:
        if not self._is_overworld_dimension(event):
            return None
        return getattr(event.world.time_phase, "value", event.world.time_phase)

    def _effective_time_of_day(self, event: GameEvent) -> int | None:
        if not self._is_overworld_dimension(event):
            return None
        return event.world.time_of_day

    def _player_call_name(self, event: GameEvent) -> str:
        call_name = (event.meta.call_name or "").strip()
        if call_name:
            return call_name
        default_call_name = (self.settings.default_call_name or "").strip()
        if default_call_name:
            return default_call_name
        player_name = (event.player.name or "").strip()
        if player_name:
            return player_name
        return "プレイヤー"

    def _player_call_prefix(self, event: GameEvent) -> str:
        name = self._player_call_name(event)
        if not name or name == "プレイヤー":
            return ""
        return f"{name}、"

    def _ushiro_call_text(self, event: GameEvent) -> str:
        seed = "|".join(
            [
                getattr(event.observed_at, "isoformat", lambda: str(event.observed_at))(),
                str(event.sequence or ""),
                self._player_call_name(event),
                ",".join(
                    threat.entity_id or threat.type
                    for threat in event.visual_threats
                ),
            ]
        )
        return selected_ushiro_call_text(self._player_call_name(event), seed)

    def _weather_value(self, weather: object) -> str | None:
        return getattr(weather, "value", weather) if weather is not None else None

    def _weather_transition(self, event: GameEvent) -> tuple[str, str] | None:
        current = self._weather_value(event.world.weather)
        previous = self.state.last_weather
        if not current or not previous or current == previous:
            return None
        return previous, current

    def _has_pending_weather_transition(self) -> bool:
        return (
            self.state.pending_weather_transition_from is not None
            and self.state.pending_weather_transition_to is not None
            and self.state.pending_weather_transition_from != self.state.pending_weather_transition_to
        )

    def _is_cold_weather_biome(self, biome: str | None) -> bool:
        return self._normalized_biome(biome) in COLD_WEATHER_BIOMES

    def _normalized_biome(self, biome: str | None) -> str:
        return (biome or "").strip().lower()

    def _update_special_biome_context(self, event: GameEvent) -> None:
        if event.event.name != EventName.STATUS_SNAPSHOT:
            return
        normalized_biome = self._normalized_biome(event.world.biome) or None
        if normalized_biome == self.state.current_biome:
            return
        self.state.current_biome = normalized_biome
        if normalized_biome is not None:
            recent_ms = self._recent_ms(
                event.observed_at,
                self.state.last_special_biome_comment_at.get(normalized_biome),
            )
            if (
                recent_ms is not None
                and recent_ms < self.settings.special_biome_comment_cooldown_ms
            ):
                self.state.pending_special_biome_line = None
                return
        self.state.pending_special_biome_line = self._resolve_special_biome_entry_line(
            normalized_biome,
            self._effective_time_phase(event),
        )

    def _resolve_special_biome_entry_line(self, biome: str | None, time_phase: object) -> str | None:
        if biome is None:
            return None
        phase_key = "night" if time_phase == "night" else "day"
        lines = special_biome_entry_lines(biome, phase_key)
        if not lines:
            return None
        return self._select_deterministic_line(f"{biome}:{phase_key}", lines)

    def _select_deterministic_line(self, seed: str, lines: tuple[str, ...]) -> str:
        if len(lines) == 1:
            return lines[0]
        return lines[sum(ord(ch) for ch in seed) % len(lines)]

    def _emit_pending_special_biome_line(self, now: datetime | None = None) -> str | None:
        line = self.state.pending_special_biome_line
        if line is None:
            return None
        self.state.pending_special_biome_line = None
        if self.state.current_biome is not None and now is not None:
            self.state.last_special_biome_comment_at[self.state.current_biome] = now
        return line

    def _is_overworld_dimension(self, event: GameEvent) -> bool:
        dimension = self._normalized_dimension(event)
        if not dimension:
            return True
        return dimension in {"overworld", "minecraft:overworld"}

    def _is_other_realm_swarm_scene(
        self,
        event: GameEvent,
        *,
        visual_count: int | None = None,
        auditory_count: int | None = None,
    ) -> bool:
        if self._is_overworld_dimension(event):
            return False
        resolved_visual_count = len(event.visual_threats) if visual_count is None else visual_count
        resolved_auditory_count = len(event.auditory_threats) if auditory_count is None else auditory_count
        if resolved_visual_count >= self.settings.other_realm_swarm_visual_threshold:
            return True
        return (
            resolved_visual_count > 0
            and resolved_auditory_count >= self.settings.other_realm_audio_generic_threshold
        )

    def _should_genericize_other_realm_auditory_presence(
        self,
        event: GameEvent,
        auditory_count: int,
    ) -> bool:
        if self._is_overworld_dimension(event):
            return False
        if len(event.visual_threats) >= self.settings.other_realm_swarm_visual_threshold:
            return True
        return auditory_count >= self.settings.other_realm_audio_generic_threshold

    def _normalized_dimension(self, event: GameEvent) -> str:
        return (event.player.dimension or "").strip().lower()

    def _did_change_dimension(self, event: GameEvent) -> bool:
        current_dimension = self._normalized_dimension(event) or None
        previous_dimension = self.state.current_dimension
        return (
            previous_dimension is not None
            and current_dimension is not None
            and current_dimension != previous_dimension
        )

    def _is_cave_biome(self, biome: str | None) -> bool:
        normalized = self._normalized_biome(biome)
        return normalized == "deep_dark" or normalized.endswith("_caves")

    def _is_night_warning_suppressed_biome(self, biome: str | None) -> bool:
        normalized = self._normalized_biome(biome)
        return normalized in NIGHT_WARNING_SUPPRESSED_BIOMES

    def _is_rest_time(self, event: GameEvent) -> bool:
        time_phase = self._effective_time_phase(event)
        return time_phase in {"evening", "night"}

    def _is_near_respawn_bed(self, event: GameEvent) -> bool:
        if not self._respawn_point_set(event):
            return False
        respawn_distance = event.world.respawn_distance
        return respawn_distance is not None and respawn_distance <= self.settings.home_bed_prompt_distance

    def _has_nearby_sleepable_bed(self, event: GameEvent) -> bool:
        return (event.world.nearby_bed_count or 0) > 0

    def _should_emit_sleep_prompt(self, event: GameEvent, now: datetime) -> bool:
        if event.world.is_submerged or not self._is_rest_time(event):
            return False
        if (event.world.nearby_sleeping_people_count or 0) > 0:
            return False
        if (
            self.state.last_sleep_prompt_at is not None
            and self._recent_ms(now, self.state.last_sleep_prompt_at) is not None
            and self._recent_ms(now, self.state.last_sleep_prompt_at) < self.settings.sleep_prompt_cooldown_ms
        ):
            return False
        return self._is_near_respawn_bed(event) or self._has_nearby_sleepable_bed(event)

    def _emit_sleep_prompt(self, event: GameEvent, now: datetime) -> str | None:
        if not self._should_emit_sleep_prompt(event, now):
            return None
        self.state.last_sleep_prompt_at = now
        if self._is_near_respawn_bed(event):
            return response_text("darkness", "sleep", "near_respawn_bed")
        return response_text("darkness", "sleep", "nearby_bed")

    def _should_emit_sleeping_neighbor_comment(self, event: GameEvent, now: datetime) -> bool:
        if event.world.is_submerged or not self._is_rest_time(event):
            return False
        if self._should_emit_sleep_prompt(event, now):
            return False
        if (event.world.nearby_sleeping_people_count or 0) <= 0:
            return False
        if (
            self.state.last_sleeping_neighbor_comment_at is not None
            and self._recent_ms(now, self.state.last_sleeping_neighbor_comment_at) is not None
            and self._recent_ms(now, self.state.last_sleeping_neighbor_comment_at)
            < self.settings.sleeping_neighbor_comment_cooldown_ms
        ):
            return False
        return True

    def _render_sleeping_neighbor_line(self, event: GameEvent, now: datetime) -> str | None:
        if not self._should_emit_sleeping_neighbor_comment(event, now):
            return None
        self.state.last_sleeping_neighbor_comment_at = now
        return self._generate_leaf_text(
            kind="sleeping_neighbor",
            fallback_text=fallback_text("general", "sleep", "sleeping_neighbor"),
            details={
                "player_name": self._player_call_name(event),
                "biome": self._biome_label(event.world.biome),
                "time_phase": self._effective_time_phase(event) or "unknown",
                "sleeping_people_count": event.world.nearby_sleeping_people_count or 0,
            },
            temperature=0.35,
        )

    def _is_surface_evening_warning_context(self, event: GameEvent) -> bool:
        time_phase = self._effective_time_phase(event)
        if time_phase != "evening":
            return False
        if self._is_night_warning_suppressed_biome(event.world.biome):
            return False
        if self._weather_value(event.world.weather) == "thunder":
            return False
        if not bool(event.world.sky_visible):
            return False
        if bool(event.world.is_submerged):
            return False
        if self._is_cave_biome(event.world.biome):
            return False
        if self._is_safe_zone_with_door_event(event):
            return False
        return True

    def _is_cave_or_submerged_night_warning_context(self, event: GameEvent) -> bool:
        if not self._is_overworld_dimension(event):
            return False
        if self._is_night_warning_suppressed_biome(event.world.biome):
            return False
        if bool(event.world.is_submerged):
            return True
        return self._is_cave_biome(event.world.biome)

    def _should_schedule_night_warning(self, event: GameEvent) -> bool:
        time_phase = self._effective_time_phase(event)
        if time_phase == "evening":
            return (
                self._is_surface_evening_warning_context(event)
                or self._is_cave_or_submerged_night_warning_context(event)
            )
        if time_phase == "night":
            return self._is_cave_or_submerged_night_warning_context(event)
        return False

    def _should_consider_night_warning(self, event: GameEvent) -> bool:
        if self.state.night_warning_emitted_this_cycle:
            return False
        return self.state.night_warning_pending or self._should_schedule_night_warning(event)

    def _render_night_warning_line(self, event: GameEvent) -> str | None:
        if self.player_input.should_block_ambient:
            return None
        if self._is_surface_evening_warning_context(event):
            return EVENING_SURFACE_WARNING_CALL
        if not self._is_cave_or_submerged_night_warning_context(event):
            return None
        time_phase = self._effective_time_phase(event)
        if time_phase == "evening":
            phase_label = "夕方"
        elif time_phase == "night":
            phase_label = "夜"
        else:
            return None
        return response_text("darkness", "night_warning", "cave_or_submerged", phase_label=phase_label)

    def _emit_pending_night_warning(self, event: GameEvent) -> str | None:
        if not self._should_consider_night_warning(event):
            return None
        line = self._render_night_warning_line(event)
        if line is None:
            return None
        self.state.night_warning_pending = False
        self.state.night_warning_emitted_this_cycle = True
        return line
