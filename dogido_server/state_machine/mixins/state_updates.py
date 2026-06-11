# state_machine/mixins/state_updates.py
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from math import inf

from dogido_server.models import EventName, GameEvent, HorizontalDirection
from dogido_server.state_machine.types import AuditoryPresenceState, DerivedSignals

LOGGER = logging.getLogger("uvicorn.error")


class StateUpdatesMixin:
    def _update_memory(self, event: GameEvent, now: datetime) -> None:
        self._prune_comment_memory(now)
        self._handle_dimension_change(event)
        self._update_special_biome_context(event)
        self._update_portal_context(event)
        time_phase = self._effective_time_phase(event)
        if self.state.last_non_silent_at is None:
            self.state.last_non_silent_at = now
        if self.state.last_haiku_emitted_at is None:
            # 初回イベントから川柳の10分周期を始める
            self.state.last_haiku_emitted_at = now
        # 平和な姿のモブを種ごとに記録する（中立モブの敵対化検知に使う）
        for mob in event.passive_mobs:
            mob_type = (mob.type or "").strip().lower()
            if mob_type:
                self.state.recent_passive_mob_seen_at_by_type[mob_type] = now
        # 優先イベント（脅威・プレイヤー入力）が来たら発句中の川柳はキャンセルする。
        # 周期 (last_haiku_emitted_at) はそのままなので、静けさが戻って
        # haiku_quiet_time_ms 経過後に再発句される。
        if self.state.pending_haiku_after_preface and (
            event.visual_threats
            or event.auditory_threats
            or self.player_input.breaks_silence
        ):
            self.state.pending_haiku_after_preface = False
        if time_phase == "morning" and self.state.last_time_phase != "morning":
            self.state.pending_haiku_after_preface = False
        self.state.last_time_phase = time_phase
        if time_phase != "night":
            self.state.firefly_reacted_this_night = False
        if time_phase in {"morning", "day"}:
            self.state.night_warning_pending = False
            self.state.night_warning_emitted_this_cycle = False
            self.state.pending_night_warning_detail = False
        elif (
            not self.state.night_warning_emitted_this_cycle
            and self._should_schedule_night_warning(event)
        ):
            self.state.night_warning_pending = True
        if self._is_emergency_shelter_morning(event):
            self.state.emergency_shelter_reset_ready = True
        if time_phase in {"evening", "night"} and self._is_emergency_shelter_event(event):
            self.state.emergency_shelter_seen_this_cycle = True
        if time_phase == "night" and self.state.emergency_shelter_reset_ready:
            self.state.emergency_shelter_advised_this_cycle = False
            self.state.emergency_shelter_seen_this_cycle = False
            self.state.emergency_shelter_morning_announced = False
            self.state.emergency_shelter_reset_ready = False
        self.state.prior_recent_visual_ms = self._recent_ms(now, self.state.last_visual_threat_at)
        self.state.prior_recent_audio_ms = self._recent_ms(now, self.state.last_audio_threat_at)

        if event.visual_threats:
            self.state.last_non_silent_at = now
            self.state.last_visual_threat_at = now
            self.state.last_confirmed_hostiles = [threat.type for threat in event.visual_threats]
            self.state.last_known_hostile_directions = [
                threat.direction.horizontal.value
                for threat in event.visual_threats
                if threat.direction.horizontal is not None
            ]
            signature = self._visual_group_signature(event.visual_threats)
            if signature != self.state.stalled_visual_signature:
                self.state.stalled_visual_signature = signature
                self.state.stalled_visual_started_at = now
        else:
            self.state.stalled_visual_signature = ""
            self.state.stalled_visual_started_at = None

        unseen_auditory_threats = self._unseen_auditory_threats(event.visual_threats, event.auditory_threats)
        if unseen_auditory_threats:
            self.state.last_non_silent_at = now
            self.state.last_audio_threat_at = now
            for threat in unseen_auditory_threats:
                key = self._auditory_comment_key(threat)
                state = self.state.auditory_presence_states.get(key)
                recent_ms = self._recent_ms(now, state.last_seen_at) if state is not None else None
                if state is None or recent_ms is None or recent_ms >= self.settings.hostile_comment_cooldown_ms:
                    state = AuditoryPresenceState(
                        count=0,
                        last_seen_at=None,
                        first_x=event.player.position.x,
                        first_z=event.player.position.z,
                    )
                    self.state.auditory_presence_states[key] = state
                state.count += 1
                state.last_seen_at = now

        if event.combat.recent_damage_ms is not None:
            self.state.last_damage_at = now - timedelta(milliseconds=event.combat.recent_damage_ms)
        if event.player.health is None or event.player.health > self.settings.low_health_warning_threshold:
            self.state.low_health_warning_armed = True

        if self.player_input.wants_quiet:
            self.state.shut_up_count += 1
        if self.player_input.breaks_silence:
            self.state.last_player_input_at = now
            self.state.last_non_silent_at = now

        current_effects = self._active_status_effects(event)
        self.state.last_active_status_effects = current_effects

        ominous_kind = self._ominous_sound_kind(event)
        if ominous_kind is None:
            raw_ominous_kind = (event.world.ominous_sound_kind or "").strip().lower()
            if raw_ominous_kind and not self._ominous_sound_context_allows(event, raw_ominous_kind):
                self.state.last_ominous_sound_seen_at = None
                self.state.last_ominous_sound_kind = None
                self.state.last_ominous_sound_severity = 0
                self.state.ominous_sound_stage = 0
            recent_ms = self._recent_ms(now, self.state.last_ominous_sound_seen_at)
            if recent_ms is None or recent_ms >= self.settings.ominous_sound_reset_ms:
                self.state.last_ominous_sound_kind = None
                self.state.last_ominous_sound_severity = 0
                self.state.ominous_sound_stage = 0
        else:
            if self.state.last_ominous_sound_seen_at is not None:
                recent_ms = self._recent_ms(now, self.state.last_ominous_sound_seen_at)
                if recent_ms is None or recent_ms >= self.settings.ominous_sound_reset_ms:
                    self.state.ominous_sound_stage = 0
                    self.state.last_ominous_sound_severity = 0
            self.state.last_ominous_sound_seen_at = now
            self.state.last_ominous_sound_kind = ominous_kind
            self.state.last_ominous_sound_severity = self._ominous_sound_severity(ominous_kind)

        if self._ominous_sound_presence_active(now):
            self.state.night_warning_pending = False
            self.state.pending_night_warning_detail = False
            if self.state.current_biome != "deep_dark":
                self.state.pending_special_biome_line = None
                self.state.pending_structure_entry_key = None
        if self._boss_presence_active(now):
            self.state.night_warning_pending = False
            self.state.pending_night_warning_detail = False
            self.state.pending_special_biome_line = None
            self.state.pending_structure_entry_key = None

        if event.event.name in {EventName.COMBAT_ENDED, EventName.PLAYER_DIED}:
            self.state.last_combat_end_at = now
            self.state.seen_boss_visual_keys.clear()
            self._reset_warden_combat_comment_state()
        if event.event.name == EventName.COMBAT_ENDED:
            self.state.pending_safe_aftermath = self._boss_defeat_confirmed(event)
            if any(self._is_boss_type(hostile) for hostile in self.state.last_confirmed_hostiles):
                LOGGER.warning(
                    "boss_aftermath_decision pending=%s defeat_confirmed_flag=%s hostiles=%s",
                    self.state.pending_safe_aftermath,
                    event.combat.warden_defeat_confirmed,
                    list(self.state.last_confirmed_hostiles),
                )
        if event.event.name == EventName.PLAYER_DIED:
            self.state.pending_safe_aftermath = False
        pending_aftermath_age_ms = self._recent_ms(now, self.state.last_combat_end_at)
        if (
            self.state.pending_safe_aftermath
            and (
                pending_aftermath_age_ms is None
                or pending_aftermath_age_ms > self.settings.pending_safe_aftermath_window_ms
            )
        ):
            self.state.pending_safe_aftermath = False
            self.state.last_confirmed_hostiles = []
            self.state.last_known_hostile_directions = []

        self.state.burning_visual_keys = {
            self._visual_identity_key(threat)
            for threat in event.visual_threats
            if threat.on_fire
        }
        self.state.last_occluded_dark_zone = self._is_occluded_dark_zone_event(event)
        self.state.last_safe_zone_with_door = self._is_safe_zone_with_door_event(event)
        self.state.last_emergency_shelter = self._is_emergency_shelter_event(event)
        self.state.last_submerged_dark_zone = self._is_submerged_dark_zone_event(event)
        self.state.last_light_source_count = self._light_source_count(event.inventory)
        self.state.last_weather = self._weather_value(event.world.weather)
        self.state.inventory_initialized = True
        ground_hostile_count = self._ground_hostile_count_within_query_range(event)
        authoritative_ground_count_event = event.event.name in {
            EventName.STATUS_SNAPSHOT,
            EventName.THREAT_APPROACHING,
            EventName.COMBAT_ENDED,
            EventName.PLAYER_DIED,
        }
        if (
            authoritative_ground_count_event
            and ground_hostile_count <= 0
            and self.state.last_ground_hostile_count_within_query_range > 0
        ):
            self.state.mass_hostile_callout_latched = False
            self.state.last_multi_hostile_report_at = None
            self.state.last_multi_hostile_count = 0
            self.state.last_mass_hostile_callout_at = None
            self.state.last_multi_species_report_at = None
            self.state.last_multi_species_signature = ""
            self.state.last_overwhelmed_report_at = None
            self.state.last_overwhelmed_signature = ""
            self.state.last_visual_priority_callout_at = None
            self.state.last_single_visual_type = None
            self.state.last_single_visual_at = None
            self.state.announced_hostile_counts.clear()
        if authoritative_ground_count_event:
            self.state.last_ground_hostile_count_within_query_range = ground_hostile_count
        self.state.active_close_flying_visual_keys = self._current_close_flying_visual_keys(event)

    def _handle_dimension_change(self, event: GameEvent) -> None:
        current_dimension = self._normalized_dimension(event) or None
        previous_dimension = self.state.current_dimension
        if current_dimension == previous_dimension:
            return
        self.state.current_dimension = current_dimension
        # 初観測が非オーバーワールドの場合もワープ到着直後とみなす
        if current_dimension is not None and (
            previous_dimension is not None
            or not self._is_overworld_dimension(event)
        ):
            self.state.last_dimension_change_at = event.observed_at
        if previous_dimension is None or current_dimension is None:
            return
        returning_to_overworld = (
            previous_dimension in {"minecraft:the_nether", "the_nether", "minecraft:the_end", "the_end"}
            and current_dimension in {"minecraft:overworld", "overworld"}
        )
        self.state.last_time_phase = None
        self.state.last_visual_threat_at = None
        self.state.last_audio_threat_at = None
        self.state.last_damage_at = None
        self.state.low_health_warning_armed = True
        self.state.last_combat_end_at = None
        self.state.last_confirmed_hostiles = []
        self.state.last_known_hostile_directions = []
        self.state.last_occluded_hostile_presence_comment_at = None
        self.state.panic_scream_cooldown_until = None
        self.state.commented_visual_keys.clear()
        self.state.commented_auditory_keys.clear()
        self.state.auditory_presence_states.clear()
        self.state.announced_hostile_counts.clear()
        self.state.burning_visual_keys.clear()
        self.state.daylight_water_comment_keys.clear()
        self.state.screamed_visual_keys.clear()
        self.state.seen_visual_keys.clear()
        self.state.seen_boss_visual_keys.clear()
        self.state.stalled_visual_signature = ""
        self.state.stalled_visual_started_at = None
        self.state.last_stalled_visual_comment_at = None
        self.state.last_multi_hostile_report_at = None
        self.state.last_multi_hostile_count = 0
        self.state.last_mass_hostile_callout_at = None
        self.state.last_ground_hostile_count_within_query_range = 0
        self.state.mass_hostile_callout_latched = False
        self.state.last_multi_species_report_at = None
        self.state.last_multi_species_signature = ""
        self.state.last_overwhelmed_report_at = None
        self.state.last_overwhelmed_signature = ""
        self.state.last_visual_priority_callout_at = None
        self.state.last_single_visual_type = None
        self.state.last_single_visual_at = None
        self.state.last_ushiro_call_at = None
        self.state.active_close_flying_visual_keys.clear()
        self.state.pending_dark_push_after_breath_until = None
        self.state.dark_push_active = False
        self.state.pending_safe_aftermath = False
        self.state.dark_push_stage = 0
        self.state.dark_push_reference_light = None
        self.state.dark_push_reference_darkness = None
        self.state.dark_push_breath_ready_at = None
        self.state.last_dark_push_breath_at = None
        self.state.dark_push_entry_x = None
        self.state.dark_push_entry_z = None
        self.state.last_occluded_dark_zone = None
        self.state.last_safe_zone_with_door = None
        self.state.last_emergency_shelter = None
        self.state.last_submerged_dark_zone = None
        self.state.last_foliage_shade_context = None
        self.state.pending_special_biome_line = None
        self.state.current_structure = None
        self.state.pending_structure_entry_key = None
        self.state.last_weather = None
        self.state.pending_weather_transition_from = None
        self.state.pending_weather_transition_to = None
        self.state.night_warning_pending = False
        self.state.pending_night_warning_detail = False
        self.state.pending_overworld_return_line = returning_to_overworld
        self.state.last_active_status_effects.clear()
        self.state.last_mining_fatigue_comment_at = None
        self.state.last_boss_omen_comment_at = None
        self.state.last_boss_omen_kind = None
        self.state.last_ominous_sound_seen_at = None
        self.state.last_ominous_sound_comment_at = None
        self.state.last_ominous_sound_kind = None
        self.state.last_ominous_sound_severity = 0
        self.state.ominous_sound_stage = 0
        self._reset_warden_combat_comment_state()
        if returning_to_overworld:
            self.state.pending_overworld_return_ready_at = event.observed_at + timedelta(
                milliseconds=self.settings.overworld_return_line_delay_ms
            )
        else:
            self.state.pending_overworld_return_ready_at = None

    def _derive_signals(self, event: GameEvent, now: datetime) -> DerivedSignals:
        visual_distances = [threat.distance for threat in event.visual_threats if threat.distance is not None]
        nearest_visual = min(visual_distances, default=inf)

        visual_within_7 = sum(
            1 for threat in event.visual_threats if threat.distance is not None and threat.distance <= 7.0
        )
        visual_within_10 = sum(
            1 for threat in event.visual_threats if threat.distance is not None and threat.distance <= 10.0
        )
        ground_hostile_count = self._ground_hostile_count_within_query_range(event)
        flying_hostile_count = self._flying_hostile_count_within_query_range(event)
        has_approaching = any(threat.approaching for threat in event.visual_threats)

        recent_audio_ms = self._recent_ms(now, self.state.last_audio_threat_at)
        recent_visual_ms = self._recent_ms(now, self.state.last_visual_threat_at)
        recent_damage_ms = self._recent_ms(now, self.state.last_damage_at)

        if event.combat.recent_hostile_audio_ms is not None:
            recent_audio_ms = event.combat.recent_hostile_audio_ms
        if event.combat.recent_hostile_visual_ms is not None:
            recent_visual_ms = event.combat.recent_hostile_visual_ms
        if event.combat.recent_damage_ms is not None:
            recent_damage_ms = event.combat.recent_damage_ms
        if event.combat.hostiles_within_7 is not None:
            visual_within_7 = event.combat.hostiles_within_7
        if event.combat.hostiles_within_10 is not None:
            visual_within_10 = event.combat.hostiles_within_10

        rear_high_risk = any(
            threat.distance is not None
            and threat.distance <= self.settings.rear_warning_distance
            and threat.direction.horizontal in {
                HorizontalDirection.BACK,
                HorizontalDirection.BACK_LEFT,
                HorizontalDirection.BACK_RIGHT,
            }
            for threat in event.visual_threats
        )

        combat_active_hint = bool(event.combat.combat_active_hint)
        if not combat_active_hint:
            combat_active_hint = bool(event.visual_threats or event.auditory_threats)
            combat_active_hint = combat_active_hint or (
                recent_damage_ms is not None and recent_damage_ms <= self.settings.recent_damage_window_ms
            )

        combat_end_candidate = event.event.name == EventName.COMBAT_ENDED
        if not combat_end_candidate:
            combat_end_candidate = (
                visual_within_10 == 0
                and self._older_than(recent_damage_ms, self.settings.combat_clear_time_ms)
                and self._older_than(recent_visual_ms, self.settings.combat_clear_time_ms)
                and self._older_than(recent_audio_ms, self.settings.combat_clear_time_ms)
            )

        occluded_dark_zone = self._is_occluded_dark_zone_event(event)
        safe_zone_with_door = self._is_safe_zone_with_door_event(event)
        submerged = bool(event.world.is_submerged)
        submerged_dark_zone = self._is_submerged_dark_zone_event(event)

        return DerivedSignals(
            nearest_visual_threat_distance=nearest_visual,
            visual_threat_count_within_7=visual_within_7,
            visual_threat_count_within_10=visual_within_10,
            ground_hostile_count_within_query_range=ground_hostile_count,
            flying_hostile_count_within_query_range=flying_hostile_count,
            has_approaching_visual_threat=has_approaching,
            recent_hostile_audio_ms=recent_audio_ms,
            recent_hostile_visual_ms=recent_visual_ms,
            recent_damage_ms=recent_damage_ms,
            combat_active_hint=combat_active_hint,
            combat_end_candidate=combat_end_candidate,
            danger_darkness_score=event.world.danger_darkness_score or 0.0,
            torch_available=self._has_torch(event.inventory),
            torch_craftable=self._torch_craftable(event.inventory),
            bed_available=self._has_bed(event.inventory),
            bed_craftable=self._bed_craftable(event.inventory),
            torch_near_craftable=self._torch_near_craftable(event.inventory, event.nearby_resources),
            bed_near_craftable=self._bed_near_craftable(event.inventory, event.nearby_resources),
            torch_materials_nearby=self._torch_materials_nearby(event.nearby_resources),
            bed_materials_nearby=self._bed_materials_nearby(event.nearby_resources),
            high_cost_material_owned=self._has_high_cost_shelter_materials(event.inventory),
            home_or_respawn_return_is_unrealistic=self._home_or_respawn_return_is_unrealistic(event),
            rear_high_risk=rear_high_risk,
            occluded_dark_zone=occluded_dark_zone,
            submerged=submerged,
            emergency_shelter=self._is_emergency_shelter_event(event),
            safe_zone_with_door=safe_zone_with_door,
            submerged_dark_zone=submerged_dark_zone,
        )

    def _resolve_mode(self, event: GameEvent, signals: DerivedSignals, now: datetime) -> str:
        if event.event.name == EventName.PLAYER_DIED:
            return "aftermath"

        if (
            self.state.pending_safe_aftermath
            and self._recent_ms(now, self.state.last_combat_end_at) is not None
            and self._recent_ms(now, self.state.last_combat_end_at)
            <= self.settings.pending_safe_aftermath_window_ms
            and not event.visual_threats
            # 討伐確認済みのボス戦後は、激しい戦闘音の残響（auditory_threats）が
            # 残っていても討伐ラインを待たせない
            and any(self._is_boss_type(hostile) for hostile in self.state.last_confirmed_hostiles)
            and self._boss_defeat_confirmed(event)
        ):
            return "aftermath"

        if (
            self.state.pending_safe_aftermath
            and self._recent_ms(now, self.state.last_combat_end_at) is not None
            and self._recent_ms(now, self.state.last_combat_end_at)
            <= self.settings.pending_safe_aftermath_window_ms
            and signals.entered_safe_zone_with_door
            and not event.visual_threats
            and not event.auditory_threats
        ):
            return "aftermath"

        panic_condition = (
            signals.nearest_visual_threat_distance <= self.settings.panic_distance
            or signals.visual_threat_count_within_10 >= 2
            or (
                signals.recent_damage_ms is not None
                and signals.recent_damage_ms <= self.settings.recent_damage_window_ms
            )
            or signals.rear_high_risk
        )

        local_light = event.world.local_light
        darkness_mode_condition = (
            signals.entered_occluded_dark_zone
            or signals.occluded_dark_zone
            or (
                signals.danger_darkness_score >= self.settings.darkness_alert_threshold
                and (
                    local_light is None
                    or local_light <= self.settings.darkness_advice_light_threshold
                )
            )
        )

        alert_condition = (
            bool(event.visual_threats)
            or bool(event.auditory_threats)
            or (
                not signals.submerged
                and darkness_mode_condition
            )
        )

        if panic_condition:
            if self.state.shut_up_count >= 3 and signals.combat_active_hint:
                return "suppressed_panic"
            return "panic"

        if alert_condition:
            return "alert"

        if self.state.mode == "aftermath" and self.state.aftermath_until and now < self.state.aftermath_until:
            return "aftermath"

        return "normal"

    def _apply_mode_transition(self, previous_mode: str, next_mode: str, now: datetime) -> None:
        self.state.mode = next_mode

        if previous_mode != next_mode and next_mode == "suppressed_panic":
            self.state.suppression_started_at = now
            self.state.suppression_until = now + timedelta(milliseconds=self.settings.suppression_time_ms)

        if previous_mode != next_mode and next_mode == "aftermath":
            self.state.aftermath_until = now + timedelta(milliseconds=self.settings.aftermath_time_ms)
            self.state.last_combat_end_at = now
            self.state.pending_safe_aftermath = False

        if previous_mode == "aftermath" and next_mode == "normal":
            self.state.shut_up_count = 0
            self.state.suppression_started_at = None
            self.state.suppression_until = None

    def _update_portal_context(self, event: GameEvent) -> None:
        if event.event.name != EventName.STATUS_SNAPSHOT:
            return
        portal_type = (event.world.nearby_portal_type or "").strip().lower() or None
        if not self.state.portal_state_initialized:
            self.state.portal_state_initialized = True
            if portal_type is not None:
                self.state.reacted_portal_types.add(portal_type)
            return
        if portal_type is None:
            return
        if portal_type in self.state.reacted_portal_types:
            return
        self.state.pending_portal_type = portal_type
