from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import py_trees

from dogido_server.models import EventName


@dataclass(slots=True)
class PolicyContext:
    machine: Any
    event: Any
    previous_mode: str
    next_mode: str
    signals: Any
    now: datetime


class _BlackboardBehaviour(py_trees.behaviour.Behaviour):
    def __init__(self, name: str) -> None:
        super().__init__(name=name)
        self.blackboard = self.attach_blackboard_client(name=name)
        self.blackboard.register_key("context", access=py_trees.common.Access.READ)
        self.blackboard.register_key("actions", access=py_trees.common.Access.WRITE)

    def context(self) -> PolicyContext:
        return self.blackboard.context

    def actions(self) -> list[Any]:
        return list(self.blackboard.actions)

    def store(self, actions: list[Any]) -> None:
        self.blackboard.actions = actions


class _Condition(_BlackboardBehaviour):
    def update(self) -> py_trees.common.Status:
        return py_trees.common.Status.SUCCESS if self.check(self.context()) else py_trees.common.Status.FAILURE

    def check(self, context: PolicyContext) -> bool:
        raise NotImplementedError


class _Action(_BlackboardBehaviour):
    def update(self) -> py_trees.common.Status:
        actions = self.actions()
        self.run(self.context(), actions)
        self.store(actions)
        return py_trees.common.Status.SUCCESS

    def run(self, context: PolicyContext, actions: list[Any]) -> None:
        raise NotImplementedError


class EventIs(_Condition):
    def __init__(self, event_name: EventName) -> None:
        super().__init__(name=f"EventIs[{event_name.value}]")
        self.event_name = event_name

    def check(self, context: PolicyContext) -> bool:
        return context.event.event.name == self.event_name


class ModeIs(_Condition):
    def __init__(self, mode: str) -> None:
        super().__init__(name=f"ModeIs[{mode}]")
        self.mode = mode

    def check(self, context: PolicyContext) -> bool:
        return context.next_mode == self.mode


class AmbientMobEvent(_Condition):
    def __init__(self) -> None:
        super().__init__(name="AmbientMobEvent")

    def check(self, context: PolicyContext) -> bool:
        return context.next_mode == "normal" and context.machine._should_emit_ambient_mob_comment(context.event, context.now)


class DimensionChanged(_Condition):
    def __init__(self) -> None:
        super().__init__(name="DimensionChanged")

    def check(self, context: PolicyContext) -> bool:
        return bool(context.signals.dimension_changed)


class NormalEnvironmentEvent(_Condition):
    def __init__(self) -> None:
        super().__init__(name="NormalEnvironmentEvent")

    def check(self, context: PolicyContext) -> bool:
        if context.next_mode != "normal":
            return False
        if context.event.event.name == EventName.AMBIENT_MOB_DETECTED:
            return False
        if context.event.visual_threats or context.event.auditory_threats:
            return False
        return (
            context.machine.player_input.asks_hostile_count
            or context.machine.player_input.asks_dragon_direction
            or context.machine._has_pending_player_chat(context.event)
            or context.machine._dragon_special_pending(context.event, context.now)
            or context.machine.state.pending_overworld_return_line
            or context.signals.light_source_crafted
            or context.machine.state.pending_special_biome_line is not None
            or context.machine.state.pending_structure_entry_key is not None
            or context.machine._has_recent_ender_eye_launch(context.event)
            or context.machine._has_nearby_end_portal_frame(context.event)
            or context.machine.state.pending_portal_type is not None
            or context.signals.entered_safe_zone_with_door
            or context.signals.exited_safe_zone_with_door
            or context.signals.safe_zone_with_door
            or context.signals.emergency_shelter
            or context.signals.entered_submerged_dark_zone
            or context.signals.entered_occluded_dark_zone
            or context.signals.weather_transition_to is not None
            or context.machine._has_pending_weather_transition()
            or context.machine._is_foliage_shade_context(context.event)
            or context.signals.entered_mining_fatigue
            or context.machine._boss_omen_kind(context.event) is not None
            or context.machine._ominous_sound_kind(context.event) is not None
            or context.machine._should_consider_magma_block_comment(context.event, context.now)
            or context.machine._should_consider_damaging_light_warning(context.event, context.now)
            or context.machine._has_recent_nearby_lightning(context.event)
            or (
                getattr(context.event.world.time_phase, "value", context.event.world.time_phase) == "night"
                and (context.event.world.nearby_firefly_bush_count or 0) > 0
            )
            or context.machine._should_consider_night_warning(context.event)
            # 発句済みの川柳の完了を取りこぼさない
            or context.machine.state.pending_haiku_after_preface
            or context.machine._should_emit_haiku(context.event, context.now)
            or context.machine._should_emit_emergency_shelter_advice(context.event, context.signals)
            or context.machine._should_emit_emergency_shelter_morning_call(context.event, context.signals)
            or context.machine.state.pending_dark_push_after_breath_until is not None
            or context.machine._should_warn_dark_push_no_light(context.event, context.signals, context.now)
            or context.machine._should_continue_dark_push_breath(context.event, context.signals, context.now)
            or context.machine._should_stop_dark_push_audio(context.event, context.signals)
        )


class EmitDeathActions(_Action):
    def __init__(self) -> None:
        super().__init__(name="EmitDeathActions")

    def run(self, context: PolicyContext, actions: list[Any]) -> None:
        actions.append(
            context.machine._audio_action(
                layer="speech",
                interrupt=True,
                text=context.machine._render_death_message(context.event),
            )
        )


class EmitFlushInterrupt(_Action):
    def __init__(self) -> None:
        super().__init__(name="EmitFlushInterrupt")

    def run(self, context: PolicyContext, actions: list[Any]) -> None:
        actions.append(context.machine._flush_interrupt_action())


class EmitPanicActions(_Action):
    def __init__(self) -> None:
        super().__init__(name="EmitPanicActions")

    def run(self, context: PolicyContext, actions: list[Any]) -> None:
        actions.extend(
            context.machine._threat_dark_push_stop_actions(
                context.event,
                context.signals,
                context.now,
            )
        )
        callout = context.machine._panic_callout(context.event, context.signals)
        entry_cue = context.machine._panic_entry_cue(
            context.event,
            context.signals,
            context.now,
            has_callout=callout is not None,
        )
        if entry_cue is not None:
            actions.append(entry_cue)
            if entry_cue.cue_id == "panic_scream_start":
                return

        if callout:
            actions.append(
                context.machine._audio_action(
                    layer="callout",
                    interrupt=False,
                    text=callout,
                    protect_ms=context.machine._callout_protect_ms(callout),
                )
            )


class EmitSuppressedPanicActions(_Action):
    def __init__(self) -> None:
        super().__init__(name="EmitSuppressedPanicActions")

    def run(self, context: PolicyContext, actions: list[Any]) -> None:
        actions.extend(
            context.machine._threat_dark_push_stop_actions(
                context.event,
                context.signals,
                context.now,
            )
        )
        cue = context.machine._suppressed_entry_cue(context.event, context.previous_mode, context.now)
        if cue is not None:
            actions.append(cue)
            return

        callout = context.machine._suppressed_callout(context.event, context.signals, context.now)
        if callout:
            actions.append(
                context.machine._audio_action(
                    layer="callout",
                    interrupt=False,
                    text=callout,
                    protect_ms=context.machine._callout_protect_ms(callout),
                )
            )


class EmitAlertActions(_Action):
    def __init__(self) -> None:
        super().__init__(name="EmitAlertActions")

    def run(self, context: PolicyContext, actions: list[Any]) -> None:
        actions.extend(
            context.machine._threat_dark_push_stop_actions(
                context.event,
                context.signals,
                context.now,
            )
        )
        threat_callout = context.machine._alert_callout(context.event, context.signals)
        cue = context.machine._alert_entry_cue(
            context.event,
            context.signals,
            context.now,
            has_callout=threat_callout is not None,
        )
        if cue is not None:
            actions.append(cue)
            if cue.cue_id == "panic_scream_start":
                return

        if threat_callout:
            actions.append(
                context.machine._audio_action(
                    layer="callout",
                    interrupt=False,
                    text=threat_callout,
                    protect_ms=context.machine._callout_protect_ms(threat_callout),
                )
            )
            return

        actions.extend(
            context.machine._environmental_actions(
                context.event,
                context.signals,
                context.previous_mode,
                context.now,
            )
        )


class EmitAftermathActions(_Action):
    def __init__(self) -> None:
        super().__init__(name="EmitAftermathActions")

    def run(self, context: PolicyContext, actions: list[Any]) -> None:
        if context.machine._player_input_priority_active(context.now):
            return
        if context.previous_mode != "aftermath" or context.event.event.name == EventName.COMBAT_ENDED:
            boss_aftermath = any(
                context.machine._is_boss_type(hostile)
                for hostile in context.machine.state.last_confirmed_hostiles
            )
            actions.append(
                context.machine._audio_action(
                    layer="speech",
                    interrupt=boss_aftermath,
                    cue_id="aftermath_relief",
                    text=context.machine._render_aftermath_line(context.event),
                    protect_ms=2500 if boss_aftermath else 0,
                )
            )


class EmitAmbientMobActions(_Action):
    def __init__(self) -> None:
        super().__init__(name="EmitAmbientMobActions")

    def run(self, context: PolicyContext, actions: list[Any]) -> None:
        line = context.machine._emit_ambient_mob_comment_line(context.event, context.now)
        if line:
            actions.append(
                context.machine._audio_action(
                    layer="speech",
                    interrupt=False,
                    text=line,
                )
            )


class EmitNormalEnvironmentActions(_Action):
    def __init__(self) -> None:
        super().__init__(name="EmitNormalEnvironmentActions")

    def run(self, context: PolicyContext, actions: list[Any]) -> None:
        actions.extend(
            context.machine._environmental_actions(
                context.event,
                context.signals,
                context.previous_mode,
                context.now,
            )
        )


class PyTreeActionPolicy:
    def __init__(self) -> None:
        self.root = self._create_root()
        self.tree = py_trees.trees.BehaviourTree(self.root)
        self.blackboard = py_trees.blackboard.Client(name="DogidoPolicy")
        self.blackboard.register_key("context", access=py_trees.common.Access.WRITE)
        self.blackboard.register_key("actions", access=py_trees.common.Access.WRITE)
        self.blackboard.actions = []

    def decide(self, context: PolicyContext) -> list[Any]:
        self.blackboard.context = context
        self.blackboard.actions = []
        self.tree.tick()
        return list(self.blackboard.actions)

    def _create_root(self) -> py_trees.behaviour.Behaviour:
        root = py_trees.composites.Selector(name="DogidoPolicy", memory=False)
        root.add_children(
            [
                self._sequence("DimensionChanged", DimensionChanged(), EmitFlushInterrupt()),
                self._sequence("Death", EventIs(EventName.PLAYER_DIED), EmitDeathActions()),
                self._sequence("Panic", ModeIs("panic"), EmitPanicActions()),
                self._sequence("SuppressedPanic", ModeIs("suppressed_panic"), EmitSuppressedPanicActions()),
                self._sequence("Alert", ModeIs("alert"), EmitAlertActions()),
                self._sequence("Aftermath", ModeIs("aftermath"), EmitAftermathActions()),
                self._sequence("NormalEnvironment", NormalEnvironmentEvent(), EmitNormalEnvironmentActions()),
                self._sequence("AmbientMob", AmbientMobEvent(), EmitAmbientMobActions()),
            ]
        )
        return root

    def _sequence(
        self,
        name: str,
        condition: py_trees.behaviour.Behaviour,
        action: py_trees.behaviour.Behaviour,
    ) -> py_trees.behaviour.Behaviour:
        branch = py_trees.composites.Sequence(name=name, memory=False)
        branch.add_children([condition, action])
        return branch
