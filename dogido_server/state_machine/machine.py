# state_machine/machine.py
from __future__ import annotations

from dogido_server.config import Settings
from dogido_server.llm import LLMFrontend
from dogido_server.memory_types import HaikuEmission
from dogido_server.models import GameEvent
from dogido_server.player_input import PlayerInputContext, route_player_input
from dogido_server.py_tree_policy import PyTreeActionPolicy
from dogido_server.state_machine.mixins.action_builder import ActionBuilderMixin
from dogido_server.state_machine.mixins.auditory import AuditoryMixin
from dogido_server.state_machine.mixins.common import CommonMixin
from dogido_server.state_machine.mixins.cue_reactions import CueReactionsMixin
from dogido_server.state_machine.mixins.environmental_reactions import EnvironmentalReactionsMixin
from dogido_server.state_machine.mixins.haiku import HaikuMixin
from dogido_server.state_machine.mixins.inventory import InventoryMixin
from dogido_server.state_machine.mixins.narration import NarrationMixin
from dogido_server.state_machine.mixins.state_updates import StateUpdatesMixin
from dogido_server.state_machine.mixins.threat_interrupts import ThreatInterruptsMixin
from dogido_server.state_machine.mixins.visual_reports import VisualReportsMixin
from dogido_server.state_machine.mixins.visual_targets import VisualTargetsMixin
from dogido_server.state_machine.mixins.world_analysis import WorldAnalysisMixin
from dogido_server.state_machine.types import RuntimeState, StateMachineResult


class DogidoStateMachine(
    StateUpdatesMixin,
    ActionBuilderMixin,
    CommonMixin,
    CueReactionsMixin,
    EnvironmentalReactionsMixin,
    HaikuMixin,
    ThreatInterruptsMixin,
    NarrationMixin,
    VisualTargetsMixin,
    VisualReportsMixin,
    AuditoryMixin,
    WorldAnalysisMixin,
    InventoryMixin,
):
    def __init__(self, settings: Settings, llm: LLMFrontend | None = None) -> None:
        self.settings = settings
        self.state = RuntimeState()
        self.llm = llm
        self.player_input = PlayerInputContext()
        self.policy_tree = PyTreeActionPolicy() if settings.decision_policy == "py_trees" else None
        self.emitted_haiku: HaikuEmission | None = None
        self._pending_haiku_interpretation: str | None = None

    def process(self, event: GameEvent) -> StateMachineResult:
        now = event.observed_at
        previous_mode = self.state.mode
        self.emitted_haiku = None
        self._pending_haiku_interpretation = None
        self.player_input = route_player_input(event.meta.user_text)
        dimension_changed = self._did_change_dimension(event)
        self._handle_dimension_change(event)
        newly_burning_visual = self._find_newly_burning_visual(event)
        weather_transition = None if dimension_changed else self._weather_transition(event)
        entered_occluded_dark_zone = False if dimension_changed else self._entered_occluded_dark_zone(event)
        entered_safe_zone_with_door = False if dimension_changed else self._entered_safe_zone_with_door(event)
        entered_emergency_shelter = False if dimension_changed else self._entered_emergency_shelter(event)
        entered_close_flying_visual = None if dimension_changed else self._entered_close_flying_visual(event)
        exited_safe_zone_with_door = False if dimension_changed else self._exited_safe_zone_with_door(event)
        entered_submerged_dark_zone = False if dimension_changed else self._entered_submerged_dark_zone(event)
        entered_mining_fatigue = False if dimension_changed else self._entered_status_effect(event, "mining_fatigue")
        light_source_crafted = self._light_source_crafted(event)

        self._update_memory(event, now)
        signals = self._derive_signals(event, now)
        signals.dimension_changed = dimension_changed
        signals.newly_burning_visual = newly_burning_visual
        signals.entered_occluded_dark_zone = entered_occluded_dark_zone
        signals.entered_safe_zone_with_door = entered_safe_zone_with_door
        signals.entered_emergency_shelter = entered_emergency_shelter
        signals.entered_close_flying_visual = entered_close_flying_visual
        signals.exited_safe_zone_with_door = exited_safe_zone_with_door
        signals.entered_submerged_dark_zone = entered_submerged_dark_zone
        signals.entered_mining_fatigue = entered_mining_fatigue
        signals.light_source_crafted = light_source_crafted
        signals.weather_transition_from = weather_transition[0] if weather_transition is not None else None
        signals.weather_transition_to = weather_transition[1] if weather_transition is not None else None
        if weather_transition is not None:
            self.state.pending_weather_transition_from = weather_transition[0]
            self.state.pending_weather_transition_to = weather_transition[1]
        signals.cold_weather_biome = self._is_cold_weather_biome(event.world.biome)
        signals.dry_weather_biome = self._is_dry_weather_biome(event.world.biome)
        self.state.emergency_shelter_active = signals.emergency_shelter
        next_mode = self._resolve_mode(event, signals, now)
        self._apply_mode_transition(previous_mode, next_mode, now)
        actions = self._build_actions(event, previous_mode, next_mode, signals, now)
        self._log_emitted_actions(event, previous_mode, next_mode, actions)
        self._update_silence_break_state(event, actions, now)
        for threat in event.visual_threats:
            visual_key = self._visual_identity_key(threat)
            self.state.seen_visual_keys[visual_key] = now
            if self._is_boss_type(threat.type):
                self.state.seen_boss_visual_keys.add(visual_key)
        self.state.last_foliage_shade_context = self._is_foliage_shade_context(event)

        combat_active = next_mode in {"panic", "suppressed_panic"} or signals.combat_active_hint
        return StateMachineResult(
            state=self.state,
            combat_active=combat_active,
            actions=actions,
            haiku_emission=self.emitted_haiku,
        )
