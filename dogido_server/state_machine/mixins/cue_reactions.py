# state_machine/mixins/cue_reactions.py
from __future__ import annotations

from datetime import datetime, timedelta

from dogido_server.models import EventName, GameEvent, VisualThreat
from dogido_server.state_machine.constants import *  # noqa: F403
from dogido_server.state_machine.types import AudioAction, DerivedSignals


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
        if self._should_suppress_panic_cues(event) or not self._can_emit_panic_cue(now):
            return None
        if self._consume_ushiro_ambush_target(event, now) is not None:
            return self._build_cue_action("ushiro_scream", "ぎゃー！", now, protect_ms=2000)
        if self._consume_dark_push_forward_ambush_target(event, now) is not None:
            return self._build_cue_action("front_spawn_scream", "ぎゃー！", now, protect_ms=1600)
        if self._consume_new_close_visual_ambush_target(event, now) is not None:
            return self._build_cue_action("panic_scream_start", "きゃー！", now)
        if self._should_emit_scream_only(event, signals):
            return self._build_cue_action("panic_scream_start", "きゃー！", now)
        if has_callout:
            return self._build_cue_action("spot_hostile_gasp", "ハッ", now, interrupt=False)
        return None

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
        return (
            self._is_close_audio_ambush(event)
            or self._is_skeleton_damage_ambush(event, signals)
        )

    def _should_suppress_panic_cues(self, event: GameEvent) -> bool:
        return self._normalized_biome(event.world.biome) == "deep_dark"

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
        if self._peek_ushiro_ambush_target(event, now) is not None:
            return True, self._ushiro_call_text(event)

        dark_push_forward = self._peek_dark_push_forward_ambush_target(event, now)
        if dark_push_forward is not None:
            return True, self._render_hostile_visual_callout(dark_push_forward, mode=mode)

        if silence_new_close_ambush and self._peek_new_close_visual_ambush_target(event, now) is not None:
            return True, None

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
            return True, self._auditory_comment(
                event,
                self._unseen_auditory_threats(event.visual_threats, event.auditory_threats),
                now=now,
                style=auditory_style,
            )

        overwhelmed = self._overwhelmed_callout(event.visual_threats, now=now, suppressed=softened_visuals)
        if overwhelmed is not None:
            return True, overwhelmed

        species = self._multi_species_callout(event.visual_threats, now=now, suppressed=softened_visuals)
        if species is not None:
            return True, species

        surge = self._swarm_callout(event, now=now)
        if surge is not None:
            return True, surge

        multi = self._multi_hostile_callout(event, signals, now=now, suppressed=softened_visuals)
        if multi is not None:
            return True, multi

        return False, None

    def _render_terminal_visual_callout(
        self,
        threat: VisualThreat,
        mode: str,
        direction_only: bool,
    ) -> str:
        if direction_only:
            return f"{self._direction_label(threat)}……"
        return self._render_hostile_visual_callout(threat, mode=mode)
