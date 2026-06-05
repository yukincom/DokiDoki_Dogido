# state_machine/mixins/threat_interrupts.py
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from dogido_server.models import GameEvent
from dogido_server.state_machine.constants import HOSTILE_EFFECTIVE_RANGE, RANGED_HOSTILES
from dogido_server.state_machine.types import AudioAction, DerivedSignals

LOGGER = logging.getLogger("uvicorn.error")


class ThreatInterruptsMixin:
    def _should_suppress_dark_push_relief_line(
        self,
        event: GameEvent,
        signals: DerivedSignals,
    ) -> bool:
        if self._boss_recently_seen(event.observed_at):
            return True
        if signals.safe_zone_with_door or signals.emergency_shelter or self._is_cramped_dark_burrow_event(event):
            return True
        time_phase = getattr(event.world.time_phase, "value", event.world.time_phase)
        local_light = event.world.local_light if event.world.local_light is not None else 0
        return time_phase in {"morning", "day"} and bool(event.world.sky_visible) and local_light >= 10

    def _threat_dark_push_stop_actions(
        self,
        event: GameEvent,
        signals: DerivedSignals,
        now: datetime,
    ) -> list[AudioAction]:
        if self._should_interrupt_dark_push_for_front_ambush(event, now):
            return self._interrupt_dark_push_for_threat(event, signals)
        if not self._should_block_environmental_actions_for_threats(event, signals):
            return []
        if not (
            self._should_stop_dark_push_audio(event, signals)
            or self._should_stop_dark_push_stage_one(event, signals)
        ):
            return []
        return self._handle_dark_push_stop(event, signals, now, defer_speech=True)

    def _interrupt_dark_push_for_threat(
        self,
        event: GameEvent,
        signals: DerivedSignals,
    ) -> list[AudioAction]:
        self.state.pending_dark_push_after_breath_until = None
        self.state.dark_push_active = False
        self.state.last_dark_push_breath_at = None
        self.state.dark_push_breath_ready_at = None
        if signals.occluded_dark_zone and not signals.submerged:
            self.state.dark_push_stage = 1
            self._set_dark_push_entry_reference(event)
        else:
            self.state.dark_push_stage = 0
            self.state.dark_push_entry_x = None
            self.state.dark_push_entry_z = None
        self._log_darkness_decision("dark_push_stop_threat_interrupt", event, signals)
        return [AudioAction(layer="control", interrupt=True)]

    def _should_block_environmental_actions_for_threats(
        self,
        event: GameEvent,
        signals: DerivedSignals,
    ) -> bool:
        if any(
            threat.distance is not None
            and (
                threat.distance <= 6.0
                or threat.approaching
                or (
                    threat.type in RANGED_HOSTILES
                    and threat.distance <= HOSTILE_EFFECTIVE_RANGE.get(threat.type, 6.0) + 1.5
                )
            )
            for threat in event.visual_threats
        ):
            return True
        if signals.rear_high_risk:
            return True
        if any(self._distance_band_rank(threat.distance_band) <= 1 for threat in event.auditory_threats):
            return True
        return False

    def _handle_dark_push_stop(
        self,
        event: GameEvent,
        signals: DerivedSignals,
        now: datetime,
        *,
        defer_speech: bool,
    ) -> list[AudioAction]:
        if not (
            self._should_stop_dark_push_audio(event, signals)
            or self._should_stop_dark_push_stage_one(event, signals)
        ):
            return []

        self.state.dark_push_active = False
        self.state.dark_push_stage = 0
        self.state.dark_push_breath_ready_at = None
        self.state.dark_push_entry_x = None
        self.state.dark_push_entry_z = None

        if defer_speech:
            self.state.pending_dark_push_after_breath_until = now + timedelta(
                milliseconds=self.settings.dark_push_after_breath_defer_ms
            )
            self._log_darkness_decision("dark_push_stop_deferred", event, signals)
            return [AudioAction(layer="control", interrupt=True)]

        self.state.pending_dark_push_after_breath_until = None
        if self._should_suppress_dark_push_relief_line(event, signals):
            self._log_darkness_decision("dark_push_stop_safe_zone", event, signals)
            return [AudioAction(layer="control", interrupt=True)]
        self._log_darkness_decision("dark_push_stop", event, signals)
        return [
            AudioAction(layer="control", interrupt=True),
            AudioAction(
                layer="speech",
                interrupt=False,
                text=self._render_dark_push_after_breath_line(event),
                protect_ms=2000,
            ),
        ]

    def _emit_pending_dark_push_after_breath(
        self,
        event: GameEvent,
        signals: DerivedSignals,
        now: datetime,
    ) -> list[AudioAction]:
        pending_until = self.state.pending_dark_push_after_breath_until
        if pending_until is None:
            return []
        if now >= pending_until:
            self.state.pending_dark_push_after_breath_until = None
            return []
        if event.visual_threats or event.auditory_threats:
            return []
        if signals.entered_occluded_dark_zone or signals.entered_submerged_dark_zone:
            self.state.pending_dark_push_after_breath_until = None
            return []
        if self._should_warn_dark_push_no_light(event, signals, now):
            self.state.pending_dark_push_after_breath_until = None
            return []
        if signals.safe_zone_with_door:
            self.state.pending_dark_push_after_breath_until = None
            return []
        if signals.emergency_shelter:
            self.state.pending_dark_push_after_breath_until = None
            return []
        if self._should_suppress_dark_push_relief_line(event, signals):
            self.state.pending_dark_push_after_breath_until = None
            return []
        self.state.pending_dark_push_after_breath_until = None
        return [
            AudioAction(
                layer="speech",
                interrupt=False,
                text=self._render_dark_push_after_breath_line(event),
                protect_ms=2000,
            )
        ]

    def _log_darkness_decision(
        self,
        reason: str,
        event: GameEvent,
        signals: DerivedSignals,
    ) -> None:
        LOGGER.warning(
            "darkness_decision=%s event=%s sky_visible=%s enclosure=%.2f local_light=%s danger=%.2f biome=%s cover=%s ceiling=%s walls=%s open2h=%s drafty=%s dark_volume=%s light_sources=%s nearest_light=%s submerged=%s safe_door=%s shelter=%s occluded=%s entered=%s torch=%s",
            reason,
            getattr(event.event.name, "value", event.event.name),
            event.world.sky_visible,
            event.world.enclosure_score or 0.0,
            event.world.local_light,
            signals.danger_darkness_score,
            event.world.biome or "unknown",
            event.world.overhead_cover_type or "unknown",
            event.world.ceiling_height,
            event.world.cardinal_wall_count,
            event.world.double_height_open_side_count,
            event.world.drafty_opening_count,
            event.world.connected_dark_volume,
            event.world.nearby_light_source_count,
            event.world.nearest_light_source_distance,
            signals.submerged,
            signals.safe_zone_with_door,
            signals.emergency_shelter,
            signals.occluded_dark_zone,
            signals.entered_occluded_dark_zone,
            signals.torch_available,
        )
