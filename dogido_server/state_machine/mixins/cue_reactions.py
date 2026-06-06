# state_machine/mixins/cue_reactions.py
from __future__ import annotations

from datetime import datetime, timedelta
import logging

from dogido_server.models import EventName, GameEvent, VisualThreat
from dogido_server.state_machine.constants import *  # noqa: F403
from dogido_server.state_machine.response_catalog import response_text
from dogido_server.state_machine.types import AudioAction, DerivedSignals

LOGGER = logging.getLogger("uvicorn.error")


class CueReactionsMixin:
    def _panic_entry_cue(
        self,
        event: GameEvent,
        signals: DerivedSignals,
        now: datetime,
        has_callout: bool,
    ) -> AudioAction | None:
        return self._threat_entry_cue(event, signals, now, has_callout)

    def _suppressed_entry_cue(self, event: GameEvent, previous_mode: str, now: datetime) -> AudioAction | None:
        if self._should_suppress_panic_cues(event):
            return None
        if not self._can_emit_panic_cue(now):
            return None
        cue_id, cue_text = self._suppressed_cue(previous_mode)
        self._log_panic_cue_decision(
            cue_id,
            "suppressed_panic_entry" if previous_mode != "suppressed_panic" else "suppressed_panic_breath_loop",
            event,
            interrupt=False,
        )
        return self._build_cue_action(cue_id, cue_text, now, interrupt=False)

    def _alert_entry_cue(
        self,
        event: GameEvent,
        signals: DerivedSignals,
        now: datetime,
        has_callout: bool,
    ) -> AudioAction | None:
        return self._threat_entry_cue(event, signals, now, has_callout)

    def _threat_entry_cue(
        self,
        event: GameEvent,
        signals: DerivedSignals,
        now: datetime,
        has_callout: bool,
    ) -> AudioAction | None:
        boss_reveal_target = self._boss_reveal_target(event)
        if (self._should_suppress_panic_cues(event) and boss_reveal_target is None) or not self._can_emit_panic_cue(now):
            return None
        ushiro_target = self._consume_ushiro_ambush_target(event, now)
        if ushiro_target is not None:
            self._log_panic_cue_decision("ushiro_scream", "ushiro_ambush", event, threat=ushiro_target)
            return self._build_cue_action("ushiro_scream", "ぎゃー！", now, protect_ms=2000)
        front_spawn_target = self._consume_dark_push_forward_ambush_target(event, now)
        if front_spawn_target is not None:
            self._log_panic_cue_decision(
                "front_spawn_scream",
                "dark_push_forward_ambush",
                event,
                threat=front_spawn_target,
            )
            return self._build_cue_action("front_spawn_scream", "ぎゃー！", now, protect_ms=1600)
        new_close_target = self._consume_new_close_visual_ambush_target(event, now)
        if new_close_target is not None:
            self._log_panic_cue_decision(
                "panic_scream_start",
                "new_close_visual_ambush",
                event,
                threat=new_close_target,
            )
            return self._build_cue_action("panic_scream_start", "きゃー！", now)
        boss_cue = self._boss_entry_cue(event, now, boss_reveal_target)
        if boss_cue is not None:
            return boss_cue
        if self._highest_priority_boss_visual(event.visual_threats) is not None:
            return None
        if has_callout and signals.entered_close_flying_visual is not None:
            self._log_panic_cue_decision(
                "spot_hostile_gasp",
                "flying_visual_warning",
                event,
                threat=signals.entered_close_flying_visual,
                interrupt=False,
            )
            return self._build_cue_action("spot_hostile_gasp", "ひいっ！", now, interrupt=False)
        scream_only_reason = self._scream_only_reason(event, signals)
        if scream_only_reason is not None:
            self._log_panic_cue_decision("panic_scream_start", scream_only_reason, event)
            return self._build_cue_action("panic_scream_start", "きゃー！", now)
        if (
            has_callout
            and not self.state.mass_hostile_callout_latched
            and signals.ground_hostile_count_within_query_range >= self.settings.hostile_mass_callout_threshold
        ):
            self._log_panic_cue_decision(
                "spot_hostile_gasp",
                "mass_hostile_gasp",
                event,
                threat=self._highest_priority_visual(event.visual_threats),
                interrupt=False,
            )
            return self._build_cue_action("spot_hostile_gasp", "ひいっ！", now, interrupt=False)
        if has_callout and self._is_other_realm_swarm_scene(
            event,
            visual_count=max(len(event.visual_threats), signals.visual_threat_count_within_10),
            auditory_count=len(event.auditory_threats),
        ):
            self._log_panic_cue_decision(
                "spot_hostile_gasp",
                "other_realm_swarm_gasp",
                event,
                threat=self._highest_priority_visual(event.visual_threats),
                interrupt=False,
            )
            return self._build_cue_action("spot_hostile_gasp", "ひいっ！", now, interrupt=False)
        if has_callout and signals.visual_threat_count_within_10 >= 2:
            self._log_panic_cue_decision(
                "spot_hostile_gasp",
                "multi_hostile_gasp",
                event,
                threat=self._highest_priority_visual(event.visual_threats),
                interrupt=False,
            )
            return self._build_cue_action("spot_hostile_gasp", "ひいっ！", now, interrupt=False)
        if has_callout and self._is_occluded_hostile_presence_context(event, event.auditory_threats):
            return None
        if has_callout and self._should_emit_spotted_hostile_gasp(event):
            self._log_panic_cue_decision(
                "spot_hostile_gasp",
                "hostile_spotted_gasp",
                event,
                threat=self._highest_priority_visual(event.visual_threats),
                interrupt=False,
            )
            return self._build_cue_action("spot_hostile_gasp", "ハッ", now, interrupt=False)
        return None

    def _boss_reveal_target(self, event: GameEvent) -> VisualThreat | None:
        threat = self._highest_priority_boss_visual(event.visual_threats)
        if threat is None or not self._is_new_visual_reveal(threat):
            return None
        return threat

    def _boss_entry_cue(
        self,
        event: GameEvent,
        now: datetime,
        threat: VisualThreat | None,
    ) -> AudioAction | None:
        if threat is None:
            return None
        if self._boss_panic_policy(threat.type) != "reveal_only":
            return None
        self._log_panic_cue_decision(
            "boss_reveal_scream",
            f"{threat.type}_boss_reveal",
            event,
            threat=threat,
            interrupt=False,
        )
        cue_text = "ひいっ！" if (threat.type or "").strip().lower() == "warden" else "ぎゃー！"
        return self._build_cue_action("boss_reveal_scream", cue_text, now, interrupt=False)

    def _build_cue_action(
        self,
        cue_id: str,
        text: str,
        now: datetime,
        protect_ms: int = 0,
        interrupt: bool = True,
    ) -> AudioAction:
        self.state.panic_scream_cooldown_until = now + timedelta(
            milliseconds=self.settings.panic_scream_cooldown_ms
        )
        return AudioAction(layer="panic_cue", interrupt=interrupt, text=text, cue_id=cue_id, protect_ms=protect_ms)

    def _should_emit_scream_only(self, event: GameEvent, signals: DerivedSignals) -> bool:
        return self._scream_only_reason(event, signals) is not None

    def _scream_only_reason(self, event: GameEvent, signals: DerivedSignals) -> str | None:
        reasons: list[str] = []
        if self._is_skeleton_damage_ambush(event, signals):
            reasons.append("skeleton_damage_ambush")
        if not reasons:
            return None
        return "+".join(reasons)

    def _should_suppress_panic_cues(self, event: GameEvent) -> bool:
        return self._normalized_biome(event.world.biome) == "deep_dark"

    def _log_panic_cue_decision(
        self,
        cue_id: str,
        reason: str,
        event: GameEvent,
        *,
        threat: VisualThreat | None = None,
        interrupt: bool = True,
    ) -> None:
        horizontal = None
        vertical = None
        if threat is not None and threat.direction is not None:
            horizontal = getattr(threat.direction.horizontal, "value", threat.direction.horizontal)
            vertical = getattr(threat.direction.vertical, "value", threat.direction.vertical)
        LOGGER.warning(
            "panic_cue_decision cue_id=%s reason=%s event=%s sequence=%s threat=%s entity_id=%s distance=%s horizontal=%s vertical=%s approaching=%s interrupt=%s visual_count=%s audio_count=%s",
            cue_id,
            reason,
            getattr(event.event.name, "value", event.event.name),
            event.sequence,
            threat.type if threat is not None else None,
            threat.entity_id if threat is not None else None,
            threat.distance if threat is not None else None,
            horizontal,
            vertical,
            threat.approaching if threat is not None else None,
            interrupt,
            len(event.visual_threats),
            len(event.auditory_threats),
        )

    def _panic_callout(self, event: GameEvent, signals: DerivedSignals) -> str | None:
        return self._threat_callout(
            event,
            signals,
            now=event.observed_at,
            mode="panic",
            auditory_style="panic",
            softened_visuals=False,
            direction_only=False,
            silence_new_close_ambush=not self._should_suppress_panic_cues(event),
        )

    def _suppressed_callout(
        self,
        event: GameEvent,
        signals: DerivedSignals,
        now: datetime,
    ) -> str | None:
        return self._threat_callout(
            event,
            signals,
            now=now,
            mode="alert",
            auditory_style="suppressed",
            softened_visuals=True,
            direction_only=True,
            silence_new_close_ambush=False,
        )

    def _alert_callout(self, event: GameEvent, signals: DerivedSignals) -> str | None:
        return self._threat_callout(
            event,
            signals,
            now=event.observed_at,
            mode="alert",
            auditory_style="alert",
            softened_visuals=False,
            direction_only=False,
            silence_new_close_ambush=not self._should_suppress_panic_cues(event),
        )

    def _threat_callout(
        self,
        event: GameEvent,
        signals: DerivedSignals,
        now: datetime,
        mode: str,
        auditory_style: str,
        softened_visuals: bool,
        direction_only: bool,
        silence_new_close_ambush: bool,
    ) -> str | None:
        crowded_other_realm = self._is_other_realm_swarm_scene(
            event,
            visual_count=max(len(event.visual_threats), signals.visual_threat_count_within_10),
            auditory_count=len(event.auditory_threats),
        )
        warden_special = self._next_warden_special_callout(event, now)
        if warden_special is not None:
            return warden_special
        if event.visual_threats:
            handled, line = self._priority_visual_callout(
                event,
                signals,
                now,
                mode=mode,
                auditory_style=auditory_style,
                softened_visuals=softened_visuals,
                silence_new_close_ambush=silence_new_close_ambush,
            )
            if handled:
                return line
            if self.player_input.asks_hostile_count:
                return self._render_hostile_query_line(event, signals.ground_hostile_count_within_query_range)
            if crowded_other_realm:
                return None

            urgent_new = self._new_priority_visual_target(event.visual_threats, now=now)
            if urgent_new is not None and (urgent_new.distance is None or urgent_new.distance > 3.0):
                return self._render_terminal_visual_callout(urgent_new, mode=mode, direction_only=direction_only)

            nearest = self._next_visual_comment_target(event.visual_threats, now=now)
            if nearest is not None:
                return self._render_terminal_visual_callout(nearest, mode=mode, direction_only=direction_only)

            auditory = self._auditory_comment(
                event,
                self._unseen_auditory_threats(event.visual_threats, event.auditory_threats),
                now=now,
                style=auditory_style,
            )
            if auditory is not None:
                return auditory

        low_health_warning = self._consume_low_health_warning(event, signals)
        if low_health_warning is not None:
            return low_health_warning

        if self.player_input.asks_hostile_count:
            return self._render_hostile_query_line(event, signals.ground_hostile_count_within_query_range)
        return self._auditory_comment(event, event.auditory_threats, now=now, style=auditory_style)

    def _priority_visual_callout(
        self,
        event: GameEvent,
        signals: DerivedSignals,
        now: datetime,
        mode: str,
        auditory_style: str,
        softened_visuals: bool,
        silence_new_close_ambush: bool,
    ) -> tuple[bool, str | None]:
        ushiro_target = self._peek_ushiro_ambush_target(event, now)
        if ushiro_target is not None:
            if self._should_suppress_panic_cues(event) or not self._can_emit_panic_cue(now):
                self.state.last_ushiro_call_at = now
                self._mark_visual_priority_callout(now, single_type=None)
            return True, self._ushiro_call_text(event)

        dark_push_forward = self._peek_dark_push_forward_ambush_target(event, now)
        if dark_push_forward is not None:
            return True, self._render_hostile_visual_callout(dark_push_forward, mode=mode)

        if silence_new_close_ambush and self._peek_new_close_visual_ambush_target(event, now) is not None:
            return True, None

        warden_special = self._next_warden_special_callout(event, now)
        if warden_special is not None:
            return True, warden_special

        boss_line = self._boss_visual_callout(event, now)
        if boss_line is not None:
            return True, boss_line

        if signals.entered_close_flying_visual is not None:
            target = signals.entered_close_flying_visual
            self.state.commented_visual_keys[self._visual_identity_key(target)] = now
            self._mark_visual_priority_callout(now, single_type=target.type)
            return True, self._render_flying_visual_callout(target)

        low_health_warning = self._consume_low_health_warning(event, signals)
        if low_health_warning is not None:
            return True, low_health_warning

        if signals.ground_hostile_count_within_query_range >= self.settings.hostile_mass_callout_threshold:
            if self.state.mass_hostile_callout_latched:
                if self.player_input.asks_hostile_count:
                    return False, None
                return True, None
            self.state.mass_hostile_callout_latched = True
            self.state.last_mass_hostile_callout_at = now
            self._mark_visual_priority_callout(now, single_type=None)
            return True, self._hostile_massive_callout(event, suppressed=softened_visuals)

        daylight_rain = self._daylight_rain_callout(event, event.visual_threats, now=now)
        if daylight_rain is not None:
            return True, daylight_rain

        daylight_water = self._daylight_water_survivor_callout(event, event.visual_threats, now=now)
        if daylight_water is not None:
            return True, daylight_water

        if signals.newly_burning_visual is not None:
            return True, self._newly_burning_visual_callout(event, signals.newly_burning_visual, now)

        stalled = self._stalled_visual_callout(event.visual_threats, now=now, suppressed=softened_visuals)
        if stalled is not None:
            return True, stalled

        increase = self._single_to_multi_increase_callout(event.visual_threats, now=now)
        if increase is not None:
            return True, increase

        if self._visual_priority_cooldown_active(now):
            if self._is_other_realm_swarm_scene(
                event,
                visual_count=max(len(event.visual_threats), signals.visual_threat_count_within_10),
                auditory_count=len(event.auditory_threats),
            ):
                return True, None
            return True, self._auditory_comment(
                event,
                self._unseen_auditory_threats(event.visual_threats, event.auditory_threats),
                now=now,
                style=auditory_style,
            )

        overwhelmed = self._overwhelmed_callout(event.visual_threats, event, now=now, suppressed=softened_visuals)
        if overwhelmed is not None:
            return True, overwhelmed

        species = self._multi_species_callout(event, event.visual_threats, now=now, suppressed=softened_visuals)
        if species is not None:
            return True, species

        surge = self._swarm_callout(event, now=now)
        if surge is not None:
            return True, surge

        multi = self._multi_hostile_callout(event, signals, now=now, suppressed=softened_visuals)
        if multi is not None:
            return True, multi

        return False, None

    def _boss_visual_callout(self, event: GameEvent, now: datetime) -> str | None:
        threat = self._highest_priority_boss_visual(event.visual_threats)
        if threat is None:
            return None
        visual_key = self._visual_identity_key(threat)
        if not self._visual_comment_allowed(visual_key, now):
            return None
        self.state.commented_visual_keys[visual_key] = now
        self.state.announced_hostile_counts[threat.type] = max(
            1,
            self.state.announced_hostile_counts.get(threat.type, 0),
        )
        self._mark_visual_priority_callout(now, single_type=threat.type)
        if self._is_new_visual_reveal(threat):
            if threat.type == "ender_dragon":
                return response_text("boss", "ender_dragon", "reveal")
            if threat.type == "wither":
                return response_text("boss", "wither", "reveal")
            if threat.type == "warden":
                return response_text("boss", "warden", "reveal")
            if threat.type == "elder_guardian":
                return response_text("boss", "elder_guardian", "reveal")
        return self._render_terminal_visual_callout(threat, mode="alert", direction_only=False)

    def _render_terminal_visual_callout(
        self,
        threat: VisualThreat,
        mode: str,
        direction_only: bool,
    ) -> str:
        if direction_only:
            return f"{self._direction_label(threat)}……"
        return self._render_hostile_visual_callout(threat, mode=mode)
