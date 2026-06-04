# state_machine/mixins/auditory.py
from __future__ import annotations

from datetime import datetime
import logging

from dogido_server.llm.sanitize import summarize_for_log
from dogido_server.models import AuditoryThreat, GameEvent, VisualThreat
from dogido_server.state_machine.constants import *  # noqa: F403
from dogido_server.state_machine.fallback_catalog import fallback_text
from dogido_server.state_machine.response_catalog import response_text
from dogido_server.state_machine.types import AuditoryPresenceState, DerivedSignals

LOGGER = logging.getLogger("uvicorn.error")


class AuditoryMixin:
    def _unseen_auditory_threats(
        self,
        visual_threats: list[VisualThreat],
        auditory_threats: list[AuditoryThreat],
    ) -> list[AuditoryThreat]:
        if not auditory_threats:
            return []
        visible_ids = {
            threat.entity_id
            for threat in visual_threats
            if threat.entity_id
        }
        unseen: list[AuditoryThreat] = []
        for threat in auditory_threats:
            if threat.source_id and threat.source_id in visible_ids:
                continue
            unseen.append(threat)
        return unseen

    def _stalled_visual_callout(
        self,
        threats: list[VisualThreat],
        now: datetime,
        suppressed: bool,
    ) -> str | None:
        if not threats or self.state.stalled_visual_started_at is None:
            return None
        ongoing_ms = self._recent_ms(now, self.state.stalled_visual_started_at)
        if ongoing_ms is None or ongoing_ms < self.settings.stalled_visual_comment_delay_ms:
            return None
        recent_ms = self._recent_ms(now, self.state.last_stalled_visual_comment_at)
        if recent_ms is not None and recent_ms < self.settings.stalled_visual_comment_cooldown_ms:
            return None
        self.state.last_stalled_visual_comment_at = now
        self._mark_visual_priority_callout(now, single_type=None)
        if suppressed:
            return response_text("combat", "pressure", "stalled_visual_suppressed")
        return response_text("combat", "pressure", "stalled_visual")

    def _daylight_water_survivor_callout(
        self,
        event: GameEvent,
        threats: list[VisualThreat],
        now: datetime,
    ) -> str | None:
        if getattr(event.world.time_phase, "value", event.world.time_phase) not in {"morning", "day"}:
            return None
        if not event.world.sky_visible:
            return None
        recent_ms = self._recent_ms(now, self.state.last_daylight_water_comment_at)
        if recent_ms is not None and recent_ms < self.settings.daylight_water_comment_cooldown_ms:
            return None
        survivors = [
            threat for threat in threats
            if threat.type in DAYLIGHT_BURN_HOSTILES and threat.in_water and not threat.on_fire
        ]
        if not survivors:
            return None
        self.state.last_daylight_water_comment_at = now
        for threat in survivors:
            visual_key = self._visual_identity_key(threat)
            self.state.daylight_water_comment_keys[visual_key] = now
            self.state.commented_visual_keys[visual_key] = now
            self.state.seen_visual_keys[visual_key] = now

        line = self._render_daylight_water_survivor_line(event, survivors, threats)
        followup = self._daylight_water_followup_callout(threats, now)
        if followup:
            return f"{line} {followup}"
        return line

    def _render_daylight_water_survivor_line(
        self,
        event: GameEvent,
        survivors: list[VisualThreat],
        threats: list[VisualThreat],
    ) -> str:
        skeleton = next((threat for threat in survivors if threat.type == "skeleton"), None)
        if skeleton is None:
            return DAYLIGHT_WATER_CALL
        return self._generate_leaf_text(
            kind="daylight_water_skeleton",
            fallback_text=fallback_text("general", "combat", "daylight_water_skeleton"),
            details={
                "player_name": self._player_call_name(event),
                "biome": self._biome_label(event.world.biome),
                "time_phase": getattr(event.world.time_phase, "value", event.world.time_phase) or "unknown",
                "hostiles": [self._hostile_label(threat.type) for threat in threats],
                "count": len(threats),
            },
            temperature=0.6,
        )

    def _newly_burning_visual_callout(
        self,
        event: GameEvent,
        threat: VisualThreat,
        now: datetime,
    ) -> str | None:
        recent_ms = self._recent_ms(now, self.state.last_burning_visual_comment_at)
        if recent_ms is not None and recent_ms < self.settings.burning_visual_comment_cooldown_ms:
            return None
        self.state.last_burning_visual_comment_at = now
        return self._generate_leaf_text(
            kind="newly_burning_visual",
            fallback_text=fallback_text("general", "combat", "newly_burning_visual"),
            details={
                "player_name": self._player_call_name(event),
                "hostile": self._hostile_label(threat.type),
                "biome": self._biome_label(event.world.biome),
                "time_phase": getattr(event.world.time_phase, "value", event.world.time_phase) or "unknown",
                "distance": threat.distance,
            },
            temperature=0.72,
        )

    def _daylight_rain_callout(
        self,
        event: GameEvent,
        threats: list[VisualThreat],
        now: datetime,
    ) -> str | None:
        if getattr(event.world.time_phase, "value", event.world.time_phase) not in {"morning", "day"}:
            return None
        if not event.world.sky_visible:
            return None
        if getattr(event.world.weather, "value", event.world.weather) not in {"rain", "thunder"}:
            return None
        recent_ms = self._recent_ms(now, self.state.last_daylight_rain_comment_at)
        if recent_ms is not None and recent_ms < self.settings.daylight_water_comment_cooldown_ms:
            return None
        if not any(
            threat.type in DAYLIGHT_BURN_HOSTILES and not threat.in_water and not threat.on_fire
            for threat in threats
        ):
            return None
        self.state.last_daylight_rain_comment_at = now
        if self._is_dry_weather_biome(event.world.biome):
            weather = getattr(event.world.weather, "value", event.world.weather)
            if weather == "thunder":
                return response_text("combat", "daylight", "dry_thunder")
            return response_text("combat", "daylight", "dry_overcast")
        return DAYLIGHT_RAIN_CALL

    def _daylight_water_followup_callout(
        self,
        threats: list[VisualThreat],
        now: datetime,
    ) -> str | None:
        counts = self._hostile_counts(threats)
        if len(counts) >= 2:
            self.state.last_multi_species_signature = self._multi_species_signature(counts)
            self.state.last_multi_species_report_at = now
            self._mark_visual_priority_callout(now, single_type=None)
            return self._hostile_count_summary(counts, suppressed=False, threats=threats)

        count = len(threats)
        if count >= 2:
            self.state.last_multi_hostile_report_at = now
            self.state.last_multi_hostile_count = count
            self._mark_visual_priority_callout(now, single_type=None)
            return self._hostile_count_summary(counts, suppressed=False, threats=threats)

        return None

    def _weather_transition_callout(
        self,
        event: GameEvent,
        signals: DerivedSignals,
    ) -> str | None:
        weather_from = self.state.pending_weather_transition_from
        weather_to = self.state.pending_weather_transition_to
        if weather_from is None or weather_to is None or weather_from == weather_to:
            return None

        scene, fallback = self._weather_transition_scene(
            weather_from,
            weather_to,
            signals.cold_weather_biome,
            signals.dry_weather_biome,
        )
        if scene is None or fallback is None:
            self.state.pending_weather_transition_from = None
            self.state.pending_weather_transition_to = None
            return None
        line = self._generate_leaf_text(
            kind="weather_transition",
            fallback_text=fallback,
            details={
                "scene": scene,
                "player_name": self._player_call_name(event),
                "biome": self._biome_label(event.world.biome),
                "time_phase": getattr(event.world.time_phase, "value", event.world.time_phase) or "unknown",
                "weather_from": weather_from,
                "weather_to": weather_to,
                "cold_biome": signals.cold_weather_biome,
                "dry_biome": signals.dry_weather_biome,
            },
            temperature=0.66,
        )
        self.state.pending_weather_transition_from = None
        self.state.pending_weather_transition_to = None
        return line

    def _weather_transition_scene(
        self,
        weather_from: str,
        weather_to: str,
        cold_biome: bool,
        dry_biome: bool,
    ) -> tuple[str | None, str | None]:
        if weather_to == "clear" and weather_from in {"rain", "thunder"}:
            return (
                "clear_after_bad_weather",
                fallback_text("general", "weather_transition", "clear_after_bad_weather"),
            )
        if dry_biome:
            if weather_to == "rain":
                if weather_from == "thunder":
                    return (
                        "overcast_after_thunder",
                        fallback_text("general", "weather_transition", "overcast_after_thunder"),
                    )
                return (
                    "overcast_started",
                    fallback_text("general", "weather_transition", "overcast_started"),
                )
            if weather_to == "thunder":
                if weather_from == "rain":
                    return (
                        "dry_thunder_after_overcast",
                        fallback_text("general", "weather_transition", "dry_thunder_after_overcast"),
                    )
                return (
                    "dry_thunder_started",
                    fallback_text("general", "weather_transition", "dry_thunder_started"),
                )
        if weather_to == "rain":
            if cold_biome:
                if weather_from == "thunder":
                    return (
                        "snow_after_thunder",
                        fallback_text("general", "weather_transition", "snow_after_thunder"),
                    )
                return (
                    "snow_started",
                    fallback_text("general", "weather_transition", "snow_started"),
                )
            if weather_from == "thunder":
                return (
                    "rain_after_thunder",
                    fallback_text("general", "weather_transition", "rain_after_thunder"),
                )
            return (
                "rain_started",
                fallback_text("general", "weather_transition", "rain_started"),
            )
        if weather_to == "thunder":
            if cold_biome:
                if weather_from == "rain":
                    return (
                        "blizzard_after_snow",
                        fallback_text("general", "weather_transition", "blizzard_after_snow"),
                    )
                return (
                    "blizzard_started",
                    fallback_text("general", "weather_transition", "blizzard_started"),
                )
            if weather_from == "rain":
                return (
                    "thunder_after_rain",
                    fallback_text("general", "weather_transition", "thunder_after_rain"),
                )
            return (
                "thunder_started",
                fallback_text("general", "weather_transition", "thunder_started"),
            )
        return None, None

    def _hostile_count_summary(
        self,
        counts: dict[str, int],
        suppressed: bool,
        threats: list[VisualThreat],
    ) -> str:
        total = sum(counts.values())
        if total >= 9 and not self._contains_boss_hostile(threats):
            key = "hostile_massive_suppressed" if suppressed else "hostile_massive"
            return response_text("combat", "pressure", key)

        ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        parts = [
            f"{self._hostile_label(hostile)}{self._hostile_count_label(count)}"
            for hostile, count in ordered[:3]
        ]
        suffix = "おる……。" if suppressed else "おるで。"
        return f"{'、'.join(parts)}{suffix}"

    def _hostile_count_label(self, count: int) -> str:
        if count <= 0:
            return "0体"
        if count >= 9:
            return "9体"
        return f"{count}体"

    def _contains_boss_hostile(self, threats: list[VisualThreat]) -> bool:
        return any(threat.type in BOSS_HOSTILES for threat in threats)

    def _auditory_comment(
        self,
        event: GameEvent,
        threats: list[AuditoryThreat],
        now: datetime,
        style: str,
    ) -> str | None:
        target = self._next_auditory_comment_target(threats, now)
        if target is None:
            return None
        key = self._auditory_comment_key(target)
        presence = self.state.auditory_presence_states.get(key)
        if presence is None:
            return None

        label = self._auditory_hostile_label(target)
        if self._should_genericize_other_realm_auditory_presence(event, len(threats)):
            label = None
        direction = self._direction_label(target)
        count = presence.count
        distance_rank = self._distance_band_rank(target.distance_band)

        if self._is_occluded_hostile_presence_context(event, threats):
            return self._emit_occluded_hostile_presence_comment(
                event,
                target,
                key=key,
                distance_rank=distance_rank,
                now=now,
            )

        if count == 1:
            self.state.commented_auditory_keys[key] = (now, distance_rank)
            if label:
                return response_text("combat", "auditory_presence", "single_named", direction=direction, label=label)
            return response_text("combat", "auditory_presence", "single_unknown", direction=direction)

        if count == 4:
            self.state.commented_auditory_keys[key] = (now, distance_rank)
            if label:
                return response_text("combat", "auditory_presence", "persistent_named", label=label)
            return response_text("combat", "auditory_presence", "persistent_unknown")

        if count == 10:
            self.state.commented_auditory_keys[key] = (now, distance_rank)
            moved = self._player_distance_from_auditory_origin(event, presence)
            if moved <= self.settings.auditory_ignore_distance:
                if label:
                    return response_text("combat", "auditory_presence", "shadowing_named", label=label)
                return response_text("combat", "auditory_presence", "shadowing_unknown")
            if label:
                return response_text("combat", "auditory_presence", "chasing_named", label=label)
            return response_text("combat", "auditory_presence", "chasing_unknown")

        return None

    def _is_occluded_hostile_presence_context(
        self,
        event: GameEvent,
        threats: list[AuditoryThreat],
    ) -> bool:
        return not event.visual_threats and bool(threats) and self._is_occluded_environment(event)

    def _emit_occluded_hostile_presence_comment(
        self,
        event: GameEvent,
        threat: AuditoryThreat,
        *,
        key: str,
        distance_rank: int,
        now: datetime,
    ) -> str | None:
        recent_ms = self._recent_ms(now, self.state.last_occluded_hostile_presence_comment_at)
        if recent_ms is not None and recent_ms < self.settings.occluded_hostile_presence_comment_cooldown_ms:
            return None
        line = self._generate_leaf_text(
            kind="occluded_hostile_presence",
            fallback_text=fallback_text("general", "combat", "occluded_hostile_presence"),
            details={
                "player_name": self._player_call_name(event),
                "biome": self._biome_label(event.world.biome),
                "time_phase": getattr(event.world.time_phase, "value", event.world.time_phase) or "unknown",
                "direction": self._direction_label(threat),
                "hostile": self._auditory_hostile_label(threat) or "敵対モブ",
                "distance_band": getattr(threat.distance_band, "value", threat.distance_band) or "unknown",
                "certainty": getattr(threat.certainty, "value", threat.certainty) or "unknown",
            },
            temperature=0.42,
            route="chat",
        )
        self.state.last_occluded_hostile_presence_comment_at = now
        self.state.commented_auditory_keys[key] = (now, distance_rank)
        LOGGER.info(
            "auditory_presence_decision reason=occluded_hostile_presence event=%s sequence=%s direction=%s hostile=%s distance_band=%s text=%s",
            getattr(event.event.name, "value", event.event.name),
            event.sequence,
            self._direction_label(threat),
            self._auditory_hostile_label(threat) or "敵対モブ",
            getattr(threat.distance_band, "value", threat.distance_band),
            summarize_for_log(line),
        )
        return line

    def _next_auditory_comment_target(self, threats: list[AuditoryThreat], now: datetime) -> AuditoryThreat | None:
        ordered = sorted(threats, key=lambda threat: self._distance_band_rank(threat.distance_band))
        for threat in ordered:
            key = self._auditory_comment_key(threat)
            commented_visual_at = self.state.commented_visual_keys.get(key)
            if commented_visual_at is not None and self._recent_ms(now, commented_visual_at) < self.settings.hostile_comment_cooldown_ms:
                continue
            presence = self.state.auditory_presence_states.get(key)
            if presence is None:
                continue
            if presence.count in {1, 4, 10}:
                return threat
        return None

    def _auditory_comment_key(self, threat: AuditoryThreat) -> str:
        if threat.source_id:
            return threat.source_id
        return threat.direction.horizontal.value if threat.direction.horizontal is not None else "nearby"

    def _auditory_hostile_label(self, threat: AuditoryThreat) -> str | None:
        if not threat.spoken_name_allowed:
            return None
        return self._hostile_label(threat.label)

    def _player_distance_from_auditory_origin(
        self,
        event: GameEvent,
        presence: AuditoryPresenceState,
    ) -> float:
        current_x = event.player.position.x
        current_z = event.player.position.z
        if (
            current_x is None
            or current_z is None
            or presence.first_x is None
            or presence.first_z is None
        ):
            return 0.0
        dx = current_x - presence.first_x
        dz = current_z - presence.first_z
        return (dx * dx + dz * dz) ** 0.5

    def _distance_band_rank(self, band: object) -> int:
        value = getattr(band, "value", band)
        ranks = {
            "touching": 0,
            "very_close": 1,
            "close": 2,
            "mid": 3,
            "far": 4,
            None: 99,
        }
        return ranks.get(value, 99)
