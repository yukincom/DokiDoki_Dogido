# state_machine/mixins/action_builder.py
from __future__ import annotations

from datetime import datetime

from dogido_server.llm import LeafGenerationRequest
from dogido_server.models import EventName, GameEvent
from dogido_server.py_tree_policy import PolicyContext
from dogido_server.state_machine.constants import CHARGED_CREEPER_CALL, DAYLIGHT_RAIN_CALL, DAYLIGHT_WATER_CALL
from dogido_server.state_machine.response_catalog import is_ushiro_call_text
from dogido_server.state_machine.types import AudioAction, DerivedSignals


class ActionBuilderMixin:
    def _build_actions(
        self,
        event: GameEvent,
        previous_mode: str,
        next_mode: str,
        signals: DerivedSignals,
        now: datetime,
    ) -> list[AudioAction]:
        if self.policy_tree is not None:
            return self.policy_tree.decide(
                PolicyContext(
                    machine=self,
                    event=event,
                    previous_mode=previous_mode,
                    next_mode=next_mode,
                    signals=signals,
                    now=now,
                )
            )
        return self._build_actions_legacy(event, previous_mode, next_mode, signals, now)

    def _build_actions_legacy(
        self,
        event: GameEvent,
        previous_mode: str,
        next_mode: str,
        signals: DerivedSignals,
        now: datetime,
    ) -> list[AudioAction]:
        actions: list[AudioAction] = []

        if event.event.name == EventName.PLAYER_DIED:
            actions.append(AudioAction(layer="speech", interrupt=True, text=self._render_death_message(event)))
            return actions

        if next_mode == "panic":
            actions.extend(self._threat_dark_push_stop_actions(event, signals, now))
            callout = self._panic_callout(event, signals)
            entry_cue = self._panic_entry_cue(event, signals, now, has_callout=callout is not None)
            if entry_cue is not None:
                actions.append(entry_cue)
                if entry_cue.cue_id == "panic_scream_start":
                    return actions
            if callout:
                actions.append(
                    AudioAction(
                        layer="callout",
                        interrupt=False,
                        text=callout,
                        protect_ms=self._callout_protect_ms(callout),
                    )
                )
            return actions

        if next_mode == "suppressed_panic":
            actions.extend(self._threat_dark_push_stop_actions(event, signals, now))
            cue = self._suppressed_entry_cue(event, previous_mode, now)
            if cue is not None:
                actions.append(cue)
                return actions
            callout = self._suppressed_callout(event, signals, now)
            if callout:
                actions.append(
                    AudioAction(
                        layer="callout",
                        interrupt=False,
                        text=callout,
                        protect_ms=self._callout_protect_ms(callout),
                    )
                )
            return actions

        if next_mode == "alert":
            actions.extend(self._threat_dark_push_stop_actions(event, signals, now))
            threat_callout = self._alert_callout(event, signals)
            cue = self._alert_entry_cue(event, signals, now, has_callout=threat_callout is not None)
            if cue is not None:
                actions.append(cue)
                if cue.cue_id == "panic_scream_start":
                    return actions
            if threat_callout:
                actions.append(
                    AudioAction(
                        layer="callout",
                        interrupt=False,
                        text=threat_callout,
                        protect_ms=self._callout_protect_ms(threat_callout),
                    )
                )
            else:
                actions.extend(self._environmental_actions(event, signals, previous_mode, now))
            return actions

        if next_mode == "aftermath":
            if previous_mode != "aftermath" or event.event.name == EventName.COMBAT_ENDED:
                actions.append(
                    AudioAction(
                        layer="speech",
                        interrupt=False,
                        text=self._render_aftermath_line(event),
                        cue_id="aftermath_relief",
                    )
                )
            return actions

        if next_mode == "normal" and event.event.name == EventName.AMBIENT_MOB_DETECTED:
            line = self._render_ambient_mob_line(event, event.peaceful_mobs)
            if line:
                actions.append(AudioAction(layer="speech", interrupt=False, text=line))
            return actions

        if next_mode == "normal":
            actions.extend(self._environmental_actions(event, signals, previous_mode, now))

        return actions

    def _update_silence_break_state(
        self,
        event: GameEvent,
        actions: list[AudioAction],
        now: datetime,
    ) -> None:
        if event.visual_threats or event.auditory_threats or self.player_input.breaks_silence:
            self.state.last_non_silent_at = now
            return
        if any(action.layer in {"speech", "callout", "panic_cue"} for action in actions):
            self.state.last_non_silent_at = now

    def _audio_action(
        self,
        layer: str,
        interrupt: bool,
        text: str | None = None,
        cue_id: str | None = None,
        protect_ms: int = 0,
    ) -> AudioAction:
        return AudioAction(
            layer=layer,
            interrupt=interrupt,
            text=text,
            cue_id=cue_id,
            protect_ms=protect_ms,
        )

    def _callout_protect_ms(self, text: str | None) -> int:
        if is_ushiro_call_text(text):
            return 2000
        if text == CHARGED_CREEPER_CALL:
            return 2500
        if text and DAYLIGHT_RAIN_CALL in text:
            return 3500
        if text and self._is_daylight_water_call(text):
            return 5000
        return 0

    def _is_daylight_water_call(self, text: str) -> bool:
        if DAYLIGHT_WATER_CALL in text:
            return True
        return "水" in text and "燃え" in text

    def _generate_leaf_text(
        self,
        kind: str,
        fallback_text: str,
        details: dict[str, object],
        temperature: float = 0.2,
        route: str | None = None,
    ) -> str:
        if self.llm is None:
            return fallback_text
        return self.llm.generate_leaf_text(
            LeafGenerationRequest(
                kind=kind,
                fallback_text=fallback_text,
                details=details,
                temperature=temperature,
                route=route,
            )
        )
