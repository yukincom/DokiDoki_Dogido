from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from math import inf

from dogido_server.config import Settings
from dogido_server.llm import DogidoLLM, LeafGenerationRequest
from dogido_server.models import (
    AuditoryThreat,
    EventName,
    GameEvent,
    HorizontalDirection,
    NearbyResource,
    PeacefulMob,
    VisualThreat,
)
from dogido_server.py_tree_policy import PolicyContext, PyTreeActionPolicy

LOGGER = logging.getLogger("uvicorn.error")

HUSH_KEYWORDS = ("うるさい", "静かにして", "黙れ")

HOSTILE_LABELS = {
    "creeper": "クリーパー",
    "zombie": "ゾンビ",
    "skeleton": "スケルトン",
    "spider": "スパイダー",
    "witch": "ウィッチ",
    "enderman": "エンダーマン",
}

MOB_LABELS = {
    "rabbit": "うさぎ",
    "axolotl": "ウーパールーパー",
    "sheep": "羊",
    "cow": "牛",
    "pig": "ブタ",
    "chicken": "ニワトリ",
    "cat": "ネコ",
    "wolf": "オオカミ",
    "fox": "キツネ",
    "horse": "ウマ",
    "camel": "ラクダ",
    "villager": "村人",
}

DIRECTION_LABELS = {
    HorizontalDirection.FRONT: "前",
    HorizontalDirection.FRONT_RIGHT: "右前",
    HorizontalDirection.RIGHT: "右",
    HorizontalDirection.BACK_RIGHT: "右後ろ",
    HorizontalDirection.BACK: "後ろ",
    HorizontalDirection.BACK_LEFT: "左後ろ",
    HorizontalDirection.LEFT: "左",
    HorizontalDirection.FRONT_LEFT: "左前",
    None: "近く",
}

@dataclass(slots=True)
class AudioAction:
    layer: str
    interrupt: bool
    text: str | None = None
    cue_id: str | None = None
    protect_ms: int = 0


@dataclass(slots=True)
class RuntimeState:
    mode: str = "normal"
    shut_up_count: int = 0
    suppression_started_at: datetime | None = None
    suppression_until: datetime | None = None
    aftermath_until: datetime | None = None
    last_visual_threat_at: datetime | None = None
    last_audio_threat_at: datetime | None = None
    last_damage_at: datetime | None = None
    last_combat_end_at: datetime | None = None
    last_darkness_advice_at: datetime | None = None
    panic_scream_cooldown_until: datetime | None = None
    last_confirmed_hostiles: list[str] = field(default_factory=list)
    last_known_hostile_directions: list[str] = field(default_factory=list)
    prior_recent_visual_ms: int | None = None
    prior_recent_audio_ms: int | None = None
    burning_visual_keys: set[str] = field(default_factory=set)
    last_occluded_dark_zone: bool | None = None
    last_light_source_count: int = 0
    inventory_initialized: bool = False
    commented_visual_keys: dict[str, datetime] = field(default_factory=dict)
    commented_auditory_keys: dict[str, tuple[datetime, int]] = field(default_factory=dict)
    announced_hostile_counts: dict[str, int] = field(default_factory=dict)
    last_dark_push_comment_at: datetime | None = None
    dark_push_active: bool = False


@dataclass(slots=True)
class DerivedSignals:
    nearest_visual_threat_distance: float = inf
    visual_threat_count_within_7: int = 0
    visual_threat_count_within_10: int = 0
    has_approaching_visual_threat: bool = False
    recent_hostile_audio_ms: int | None = None
    recent_hostile_visual_ms: int | None = None
    recent_damage_ms: int | None = None
    combat_active_hint: bool = False
    combat_end_candidate: bool = False
    danger_darkness_score: float = 0.0
    torch_available: bool = False
    torch_craftable: bool = False
    bed_available: bool = False
    bed_craftable: bool = False
    torch_materials_nearby: bool = False
    bed_materials_nearby: bool = False
    rear_high_risk: bool = False
    newly_burning_visual: VisualThreat | None = None
    occluded_dark_zone: bool = False
    entered_occluded_dark_zone: bool = False
    light_source_crafted: bool = False


@dataclass(slots=True)
class StateMachineResult:
    state: RuntimeState
    combat_active: bool
    actions: list[AudioAction]

    def as_actions(self) -> list[dict[str, str | bool | None]]:
        return [asdict(action) for action in self.actions]


class DogidoStateMachine:
    def __init__(self, settings: Settings, llm: DogidoLLM | None = None) -> None:
        self.settings = settings
        self.state = RuntimeState()
        self.llm = llm
        self.policy_tree = PyTreeActionPolicy() if settings.decision_policy == "py_trees" else None

    def process(self, event: GameEvent) -> StateMachineResult:
        now = event.observed_at
        previous_mode = self.state.mode
        newly_burning_visual = self._find_newly_burning_visual(event)
        entered_occluded_dark_zone = self._entered_occluded_dark_zone(event)
        light_source_crafted = self._light_source_crafted(event)

        self._update_memory(event, now)
        signals = self._derive_signals(event, now)
        signals.newly_burning_visual = newly_burning_visual
        signals.entered_occluded_dark_zone = entered_occluded_dark_zone
        signals.light_source_crafted = light_source_crafted
        next_mode = self._resolve_mode(event, signals, now)
        self._apply_mode_transition(previous_mode, next_mode, now)
        actions = self._build_actions(event, previous_mode, next_mode, signals, now)

        combat_active = next_mode in {"panic", "suppressed_panic"} or signals.combat_active_hint
        return StateMachineResult(state=self.state, combat_active=combat_active, actions=actions)

    def _update_memory(self, event: GameEvent, now: datetime) -> None:
        self._prune_comment_memory(now)
        self.state.prior_recent_visual_ms = self._recent_ms(now, self.state.last_visual_threat_at)
        self.state.prior_recent_audio_ms = self._recent_ms(now, self.state.last_audio_threat_at)

        if event.visual_threats:
            self.state.last_visual_threat_at = now
            self.state.last_confirmed_hostiles = [threat.type for threat in event.visual_threats]
            self.state.last_known_hostile_directions = [
                threat.direction.horizontal.value
                for threat in event.visual_threats
                if threat.direction.horizontal is not None
            ]

        if event.auditory_threats:
            self.state.last_audio_threat_at = now

        if event.combat.recent_damage_ms is not None:
            self.state.last_damage_at = now - timedelta(milliseconds=event.combat.recent_damage_ms)

        user_text = event.meta.user_text or ""
        if user_text and any(keyword in user_text for keyword in HUSH_KEYWORDS):
            self.state.shut_up_count += 1

        if event.event.name in {EventName.COMBAT_ENDED, EventName.PLAYER_DIED}:
            self.state.last_combat_end_at = now

        self.state.burning_visual_keys = {
            self._visual_identity_key(threat)
            for threat in event.visual_threats
            if threat.on_fire
        }
        self.state.last_occluded_dark_zone = self._is_occluded_dark_zone_event(event)
        self.state.last_light_source_count = self._light_source_count(event.inventory)
        self.state.inventory_initialized = True

    def _derive_signals(self, event: GameEvent, now: datetime) -> DerivedSignals:
        visual_distances = [threat.distance for threat in event.visual_threats if threat.distance is not None]
        nearest_visual = min(visual_distances, default=inf)

        visual_within_7 = sum(
            1 for threat in event.visual_threats if threat.distance is not None and threat.distance <= 7.0
        )
        visual_within_10 = sum(
            1 for threat in event.visual_threats if threat.distance is not None and threat.distance <= 10.0
        )
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

        return DerivedSignals(
            nearest_visual_threat_distance=nearest_visual,
            visual_threat_count_within_7=visual_within_7,
            visual_threat_count_within_10=visual_within_10,
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
            torch_materials_nearby=self._torch_materials_nearby(event.nearby_resources),
            bed_materials_nearby=self._bed_materials_nearby(event.nearby_resources),
            rear_high_risk=rear_high_risk,
            occluded_dark_zone=occluded_dark_zone,
        )

    def _resolve_mode(self, event: GameEvent, signals: DerivedSignals, now: datetime) -> str:
        if event.event.name == EventName.PLAYER_DIED:
            return "aftermath"

        if event.event.name == EventName.COMBAT_ENDED:
            return "aftermath"

        if signals.combat_end_candidate and self.state.mode in {"panic", "suppressed_panic"}:
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

        alert_condition = (
            bool(event.visual_threats)
            or bool(event.auditory_threats)
            or signals.danger_darkness_score >= self.settings.darkness_alert_threshold
            or signals.entered_occluded_dark_zone
            or (signals.occluded_dark_zone and not signals.torch_available)
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

        if previous_mode == "aftermath" and next_mode == "normal":
            self.state.shut_up_count = 0
            self.state.suppression_started_at = None
            self.state.suppression_until = None

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
            callout = self._panic_callout(event, signals)
            entry_cue = self._panic_entry_cue(event, signals, now, has_callout=callout is not None)
            if entry_cue is not None:
                actions.append(entry_cue)
                if entry_cue.cue_id == "panic_scream_start":
                    return actions
            if callout:
                actions.append(AudioAction(layer="callout", interrupt=True, text=callout))
            return actions

        if next_mode == "suppressed_panic":
            cue = self._suppressed_entry_cue(previous_mode, now)
            if cue is not None:
                actions.append(cue)
                return actions
            callout = self._suppressed_callout(event, signals, now)
            if callout:
                actions.append(AudioAction(layer="callout", interrupt=True, text=callout))
            return actions

        if next_mode == "alert":
            threat_callout = self._alert_callout(event, signals)
            cue = self._alert_entry_cue(event, signals, now, has_callout=threat_callout is not None)
            if cue is not None:
                actions.append(cue)
                if cue.cue_id == "panic_scream_start":
                    return actions
            if threat_callout:
                actions.append(AudioAction(layer="callout", interrupt=True, text=threat_callout))
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
            line = self._render_ambient_mob_line(event.peaceful_mobs)
            if line:
                actions.append(AudioAction(layer="speech", interrupt=False, text=line))
            return actions

        if next_mode == "normal":
            actions.extend(self._environmental_actions(event, signals, previous_mode, now))

        return actions

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

    def _generate_leaf_text(
        self,
        kind: str,
        fallback_text: str,
        details: dict[str, object],
        temperature: float = 0.2,
    ) -> str:
        if self.llm is None:
            return fallback_text
        return self.llm.generate_leaf_text(
            LeafGenerationRequest(
                kind=kind,
                fallback_text=fallback_text,
                details=details,
                temperature=temperature,
            )
        )

    def _panic_entry_cue(
        self,
        event: GameEvent,
        signals: DerivedSignals,
        now: datetime,
        has_callout: bool,
    ) -> AudioAction | None:
        if self._should_emit_scream_only(event, signals) and self._can_emit_panic_cue(now):
            return self._build_cue_action("panic_scream_start", "きゃー！", now)
        if has_callout and self._can_emit_panic_cue(now):
            return self._build_cue_action("spot_hostile_gasp", "ハッ", now)
        return None

    def _suppressed_entry_cue(self, previous_mode: str, now: datetime) -> AudioAction | None:
        if not self._can_emit_panic_cue(now):
            return None
        cue_id, cue_text = self._suppressed_cue(previous_mode)
        return self._build_cue_action(cue_id, cue_text, now)

    def _alert_entry_cue(
        self,
        event: GameEvent,
        signals: DerivedSignals,
        now: datetime,
        has_callout: bool,
    ) -> AudioAction | None:
        if self._should_emit_scream_only(event, signals) and self._can_emit_panic_cue(now):
            return self._build_cue_action("panic_scream_start", "きゃー！", now)
        if has_callout and self._can_emit_panic_cue(now):
            return self._build_cue_action("spot_hostile_gasp", "ハッ", now)
        return None

    def _build_cue_action(self, cue_id: str, text: str, now: datetime) -> AudioAction:
        self.state.panic_scream_cooldown_until = now + timedelta(
            milliseconds=self.settings.panic_scream_cooldown_ms
        )
        return AudioAction(layer="panic_cue", interrupt=True, text=text, cue_id=cue_id)

    def _should_emit_scream_only(self, event: GameEvent, signals: DerivedSignals) -> bool:
        return (
            self._is_close_audio_ambush(event)
            or self._is_skeleton_damage_ambush(event, signals)
        )

    def _panic_callout(self, event: GameEvent, signals: DerivedSignals) -> str | None:
        surge = self._swarm_callout(event, now=event.observed_at)
        if surge is not None:
            return surge

        if event.visual_threats:
            if signals.newly_burning_visual is not None:
                return "っしゃ！もえろもえろおお！"
            nearest = self._next_visual_comment_target(event.visual_threats, now=event.observed_at)
            if nearest is None:
                return None
            if signals.visual_threat_count_within_10 >= 2:
                return f"{self._direction_label(nearest)}！ まだ{signals.visual_threat_count_within_10}体おる！"
            return self._render_hostile_visual_callout(nearest, mode="panic")

        auditory = self._auditory_comment(event.auditory_threats, now=event.observed_at, style="panic")
        if auditory is not None:
            return auditory

        return None

    def _suppressed_callout(
        self,
        event: GameEvent,
        signals: DerivedSignals,
        now: datetime,
    ) -> str | None:
        surge = self._swarm_callout(event, now=now)
        if surge is not None:
            return surge

        if event.visual_threats:
            if signals.newly_burning_visual is not None:
                return "っしゃ！もえろもえろおお！"
            nearest = self._next_visual_comment_target(event.visual_threats, now=now)
            if nearest is not None:
                if (
                    self.state.suppression_until is not None
                    and now >= self.state.suppression_until
                    and signals.visual_threat_count_within_10 >= 2
                ):
                    return f"まだ{signals.visual_threat_count_within_10}体おる！"
                return f"{self._direction_label(nearest)}……"

        auditory = self._auditory_comment(event.auditory_threats, now=now, style="suppressed")
        if auditory is not None:
            return auditory
        return None

    def _alert_callout(self, event: GameEvent, signals: DerivedSignals) -> str | None:
        surge = self._swarm_callout(event, now=event.observed_at)
        if surge is not None:
            return surge

        if event.visual_threats:
            if signals.newly_burning_visual is not None:
                return "っしゃ！もえろもえろおお！"
            nearest = self._next_visual_comment_target(event.visual_threats, now=event.observed_at)
            if nearest is not None:
                return self._render_hostile_visual_callout(nearest, mode="alert")

        auditory = self._auditory_comment(event.auditory_threats, now=event.observed_at, style="alert")
        if auditory is not None:
            return auditory

        return None

    def _darkness_advice(self, event: GameEvent, signals: DerivedSignals) -> str | None:
        if signals.danger_darkness_score < self.settings.darkness_alert_threshold:
            return None

        if signals.torch_available:
            return "なあ、ここ急に暗なってきたやん。松明つけとこ。"
        if signals.torch_craftable:
            return "石炭あるやん、今のうちに松明作っとこや。"
        if signals.torch_materials_nearby:
            return "このへんで木とか石炭拾って、先に松明作っとこ。"
        if signals.bed_available:
            return "ベッド持ってるやん、今日はもう無理せんと寝よ。"
        if signals.bed_craftable:
            return "これベッド作れるで、先に寝る準備しとこや。"
        if signals.bed_materials_nearby:
            return "羊毛か木を探して、先にベッド作っとこや。"
        if not self._has_weapon(event):
            return self._render_darkness_escape_line(event)
        if event.world.time_phase in {"evening", "night"}:
            return "これはもうあかん、こんなん家に帰ったほうがええって。"
        return "なんかこの先、普通に危ない空気してるで。"

    def _environmental_actions(
        self,
        event: GameEvent,
        signals: DerivedSignals,
        previous_mode: str,
        now: datetime,
    ) -> list[AudioAction]:
        if event.visual_threats or event.auditory_threats:
            self.state.dark_push_active = False
            return []

        stop_dark_push = self._should_stop_dark_push_audio(signals)

        if signals.light_source_crafted:
            self.state.dark_push_active = False
            actions: list[AudioAction] = []
            if stop_dark_push:
                actions.append(AudioAction(layer="control", interrupt=True))
            actions.append(
                AudioAction(
                    layer="speech",
                    interrupt=False,
                    text=self._render_light_crafted_line(event),
                )
            )
            return actions

        if signals.entered_occluded_dark_zone:
            self.state.dark_push_active = False
            line = self._render_occluded_entry_line(event, signals)
            self._log_darkness_decision("occluded_entry", event, signals)
            if line:
                return [AudioAction(layer="speech", interrupt=False, text=line)]
            return []

        if self._should_warn_dark_push_no_light(event, signals, now):
            line = self._render_dark_push_no_light_line(event)
            aftermath = self._render_dark_push_after_breath_line(event)
            if line:
                self.state.last_dark_push_comment_at = now
                self.state.dark_push_active = True
                self._log_darkness_decision("dark_push", event, signals)
                actions = [
                    AudioAction(layer="speech", interrupt=False, text=line),
                    AudioAction(
                        layer="panic_cue",
                        interrupt=False,
                        cue_id="suppressed_breath",
                        text="ハァハァ……",
                    ),
                ]
                if aftermath:
                    actions.append(
                        AudioAction(
                            layer="speech",
                            interrupt=False,
                            text=aftermath,
                            protect_ms=2600,
                        )
                    )
                return actions
            return []

        if stop_dark_push:
            self.state.dark_push_active = False
            self._log_darkness_decision("dark_push_stop", event, signals)
            return [AudioAction(layer="control", interrupt=True)]

        if previous_mode != "alert" or event.event.name in {
            EventName.DANGER_DARKNESS_CHANGED,
            EventName.TIME_PHASE_CHANGED,
        }:
            darkness_advice = self._darkness_advice(event, signals)
            if darkness_advice:
                self._log_darkness_decision("darkness_advice", event, signals)
                return [AudioAction(layer="speech", interrupt=False, text=darkness_advice)]
        return []

    def _log_darkness_decision(
        self,
        reason: str,
        event: GameEvent,
        signals: DerivedSignals,
    ) -> None:
        LOGGER.warning(
            "darkness_decision=%s event=%s sky_visible=%s enclosure=%.2f local_light=%s danger=%.2f biome=%s cover=%s ceiling=%s occluded=%s entered=%s torch=%s",
            reason,
            getattr(event.event.name, "value", event.event.name),
            event.world.sky_visible,
            event.world.enclosure_score or 0.0,
            event.world.local_light,
            signals.danger_darkness_score,
            event.world.biome or "unknown",
            event.world.overhead_cover_type or "unknown",
            event.world.ceiling_height,
            signals.occluded_dark_zone,
            signals.entered_occluded_dark_zone,
            signals.torch_available,
        )

    def _ambient_mob_line(self, mobs: list[PeacefulMob]) -> str | None:
        if not mobs:
            return None
        mob = mobs[0]
        label = self._mob_label(mob.type)
        if mob.type == "rabbit":
            return "おっ！ うさぎおるやん！ かわい〜！"
        return f"おっ！ {label}おるやん！"

    def _render_ambient_mob_line(self, mobs: list[PeacefulMob]) -> str | None:
        fallback = self._ambient_mob_line(mobs)
        if fallback is None or not mobs:
            return fallback
        mob = mobs[0]
        return self._generate_leaf_text(
            kind="ambient",
            fallback_text=fallback,
            details={"mob": self._mob_label(mob.type), "direction": self._direction_label(mob)},
        )

    def _death_message(self, event: GameEvent) -> str:
        cause = (event.meta.death_cause or "").lower()
        if any(name in cause for name in ("zombie", "creeper", "skeleton", "witch", "spider", "enderman")):
            return "うん。そういうこともあるな。次は照明がっつり使お。"
        if any(word in cause for word in ("fall", "fell", "void", "accident")):
            return "あー痛かったな。まあゲームやから。"
        return "どんまい。また立て直そ。"

    def _render_death_message(self, event: GameEvent) -> str:
        fallback = self._death_message(event)
        hostile = next(
            (
                name
                for name in ("zombie", "creeper", "skeleton", "witch", "spider", "enderman")
                if name in (event.meta.death_cause or "").lower()
            ),
            "",
        )
        return self._generate_leaf_text(
            kind="death",
            fallback_text=fallback,
            details={"cause": event.meta.death_cause or "unknown", "hostile": hostile},
        )

    def _render_aftermath_line(self, event: GameEvent) -> str:
        fallback = "あー……こわかったぁ……ほんまにもうおらんのか、まだ不安やわ……。"
        return self._generate_leaf_text(
            kind="aftermath",
            fallback_text=fallback,
            details={
                "hostiles": list(self.state.last_confirmed_hostiles),
                "health": event.player.health,
            },
        )

    def _render_darkness_escape_line(self, event: GameEvent) -> str | None:
        now = event.observed_at
        if (
            self.state.last_darkness_advice_at is not None
            and self._recent_ms(now, self.state.last_darkness_advice_at) is not None
            and self._recent_ms(now, self.state.last_darkness_advice_at)
            < self.settings.darkness_llm_comment_cooldown_ms
        ):
            return None

        self.state.last_darkness_advice_at = now
        hostiles = [self._hostile_label(threat.type) for threat in event.visual_threats]
        if not hostiles and event.auditory_threats:
            hostiles = ["気配あり"]
        fallback = "これはもうあかんって、いったん家に帰って立て直そや。"
        return self._generate_leaf_text(
            kind="darkness_escape",
            fallback_text=fallback,
            details={
                "hostiles": hostiles,
                "biome": event.world.biome or "unknown",
                "time_phase": getattr(event.world.time_phase, "value", event.world.time_phase) or "unknown",
            },
            temperature=0.95,
        )

    def _render_occluded_entry_line(self, event: GameEvent, signals: DerivedSignals) -> str | None:
        if signals.torch_available:
            return self._generate_leaf_text(
                kind="occluded_entry_with_light",
                fallback_text="え、ここ急に暗ない？ ん……あかりは持っとるな……でも怖いわ……。",
                details={
                    "biome": event.world.biome or "unknown",
                    "time_phase": getattr(event.world.time_phase, "value", event.world.time_phase) or "unknown",
                    "local_light": event.world.local_light,
                },
                temperature=0.82,
            )
        return self._generate_leaf_text(
            kind="occluded_entry_no_light",
            fallback_text="え、ここ暗すぎひん？ ちょっ……あかり持っとらんやん、先にクラフトしよや。",
            details={
                "biome": event.world.biome or "unknown",
                "time_phase": getattr(event.world.time_phase, "value", event.world.time_phase) or "unknown",
                "craftable": signals.torch_craftable,
                "local_light": event.world.local_light,
            },
            temperature=0.52,
        )

    def _render_dark_push_no_light_line(self, event: GameEvent) -> str | None:
        hostiles = [self._hostile_label(threat.type) for threat in event.visual_threats]
        if not hostiles and event.auditory_threats:
            hostiles = ["気配あり"]
        return self._generate_leaf_text(
            kind="dark_push_no_light",
            fallback_text="え、ほんまに行くん？ その暗さで？ それ、だいぶ無茶やと思うで……。",
            details={
                "biome": event.world.biome or "unknown",
                "time_phase": getattr(event.world.time_phase, "value", event.world.time_phase) or "unknown",
                "hostiles": hostiles,
                "local_light": event.world.local_light,
            },
            temperature=0.9,
        )

    def _render_dark_push_after_breath_line(self, event: GameEvent) -> str | None:
        hostiles = [self._hostile_label(threat.type) for threat in event.visual_threats]
        if not hostiles and event.auditory_threats:
            hostiles = ["気配あり"]
        return self._generate_leaf_text(
            kind="dark_push_after_breath",
            fallback_text="こわかったわ……ほんまに今の、心臓ずっとバクバクしとるわ……。",
            details={
                "biome": event.world.biome or "unknown",
                "time_phase": getattr(event.world.time_phase, "value", event.world.time_phase) or "unknown",
                "hostiles": hostiles,
                "local_light": event.world.local_light,
            },
            temperature=0.92,
        )

    def _render_light_crafted_line(self, event: GameEvent) -> str:
        return self._generate_leaf_text(
            kind="light_crafted",
            fallback_text="っしゃ！ あかりできたやん、これでさっきよりだいぶ安心できるわ！",
            details={
                "biome": event.world.biome or "unknown",
                "time_phase": getattr(event.world.time_phase, "value", event.world.time_phase) or "unknown",
                "light_count": self._light_source_count(event.inventory),
            },
            temperature=0.86,
        )

    def _render_hostile_visual_callout(self, threat: VisualThreat, mode: str) -> str:
        return self._realtime_hostile_visual_callout(threat, mode)

    def _realtime_hostile_visual_callout(self, threat: VisualThreat, mode: str) -> str:
        direction = self._direction_label(threat)
        hostile = self._hostile_label(threat.type)
        variants: list[str]
        if mode == "panic":
            variants = [
                f"{direction}！ {hostile}や！",
                f"うわっ、{direction}に{hostile}や！",
                f"{direction}や！ {hostile}来とる！",
            ]
        else:
            variants = [
                f"{direction}に{hostile}おるで。",
                f"ひっ、{direction}に{hostile}おる。",
                f"{direction}や、{hostile}見えとるで。",
            ]
        identity = self._visual_identity_key(threat)
        index = sum(ord(ch) for ch in identity) % len(variants)
        return variants[index]

    def _alert_cue_id(self, event: GameEvent, previous_mode: str) -> str | None:
        if previous_mode in {"alert", "panic", "suppressed_panic"}:
            return None
        if event.visual_threats or event.auditory_threats:
            return "spot_hostile_gasp"
        return None

    def _suppressed_cue(self, previous_mode: str) -> tuple[str, str]:
        if previous_mode != "suppressed_panic":
            return ("suppressed_gasp", "ひいっ！")
        return ("suppressed_breath", "ハァハァ……")

    def _nearest_visual(self, threats: list[VisualThreat]) -> VisualThreat | None:
        if not threats:
            return None
        return min(threats, key=lambda threat: threat.distance if threat.distance is not None else inf)

    def _next_visual_comment_target(self, threats: list[VisualThreat], now: datetime) -> VisualThreat | None:
        counts = self._hostile_counts(threats)
        ordered = sorted(threats, key=lambda threat: threat.distance if threat.distance is not None else inf)
        for threat in ordered:
            visual_key = self._visual_identity_key(threat)
            if not self._visual_comment_allowed(visual_key, now):
                continue
            self.state.commented_visual_keys[visual_key] = now
            self.state.announced_hostile_counts[threat.type] = max(
                counts.get(threat.type, 1),
                self.state.announced_hostile_counts.get(threat.type, 0),
            )
            return threat
        return None

    def _find_newly_burning_visual(self, event: GameEvent) -> VisualThreat | None:
        for threat in event.visual_threats:
            if not threat.on_fire:
                continue
            if self._visual_identity_key(threat) not in self.state.burning_visual_keys:
                return threat
        return None

    def _visual_identity_key(self, threat: VisualThreat) -> str:
        if threat.entity_id:
            return threat.entity_id
        direction = threat.direction.horizontal.value if threat.direction.horizontal is not None else "nearby"
        return f"{threat.type}:{direction}"

    def _visual_comment_allowed(self, visual_key: str, now: datetime) -> bool:
        commented_at = self.state.commented_visual_keys.get(visual_key)
        if commented_at is None:
            return True
        return self._recent_ms(now, commented_at) >= self.settings.hostile_comment_cooldown_ms

    def _hostile_counts(self, threats: list[VisualThreat]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for threat in threats:
            counts[threat.type] = counts.get(threat.type, 0) + 1
        return counts

    def _swarm_callout(self, event: GameEvent, now: datetime) -> str | None:
        counts = self._hostile_counts(event.visual_threats)
        if not counts:
            return None

        ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        for hostile_type, count in ordered:
            previous = self.state.announced_hostile_counts.get(hostile_type, 0)
            if (
                count >= self.settings.hostile_count_surge_min_total
                and count - previous >= self.settings.hostile_count_surge_threshold
            ):
                self.state.announced_hostile_counts[hostile_type] = count
                for threat in event.visual_threats:
                    if threat.type == hostile_type:
                        self.state.commented_visual_keys[self._visual_identity_key(threat)] = now
                return "増えてきたで！"
        return None

    def _auditory_comment(
        self,
        threats: list[AuditoryThreat],
        now: datetime,
        style: str,
    ) -> str | None:
        target = self._next_auditory_comment_target(threats, now)
        if target is None:
            return None

        previous = self.state.commented_auditory_keys.get(self._auditory_comment_key(target))
        distance_rank = self._distance_band_rank(target.distance_band)
        self.state.commented_auditory_keys[self._auditory_comment_key(target)] = (now, distance_rank)
        getting_closer = previous is not None and distance_rank < previous[1]
        direction = self._direction_label(target)

        if style == "panic":
            if getting_closer:
                return f"{direction}で近づいとる！"
            return f"{direction}でなんかおる！"
        if style == "suppressed":
            if getting_closer:
                return f"{direction}で近づいとる……"
            return f"{direction}で声する……"
        if getting_closer:
            return f"なんか{direction}で近づいとる。"
        return f"なんか{direction}で声する。"

    def _next_auditory_comment_target(self, threats: list[AuditoryThreat], now: datetime) -> AuditoryThreat | None:
        ordered = sorted(threats, key=lambda threat: self._distance_band_rank(threat.distance_band))
        for threat in ordered:
            key = self._auditory_comment_key(threat)
            previous = self.state.commented_auditory_keys.get(key)
            current_rank = self._distance_band_rank(threat.distance_band)
            if previous is None:
                return threat
            previous_at, previous_rank = previous
            recent_ms = self._recent_ms(now, previous_at)
            if recent_ms is None or recent_ms >= self.settings.hostile_comment_cooldown_ms:
                return threat
            if current_rank < previous_rank:
                return threat
        return None

    def _auditory_comment_key(self, threat: AuditoryThreat) -> str:
        return threat.direction.horizontal.value if threat.direction.horizontal is not None else "nearby"

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

    def _is_open_visibility_environment(self, event: GameEvent) -> bool:
        enclosure_score = event.world.enclosure_score or 0.0
        cover_type = (event.world.overhead_cover_type or "unknown").lower()
        ceiling_height = event.world.ceiling_height or 0.0
        if event.world.sky_visible and ceiling_height >= 12.0:
            return True
        if not event.world.sky_visible:
            if ceiling_height >= 12.0 and enclosure_score < 0.12:
                return True
            return cover_type in {"foliage", "wood"} and enclosure_score < 0.18
        if enclosure_score >= 0.18:
            return False
        return True

    def _is_occluded_environment(self, event: GameEvent) -> bool:
        return not self._is_open_visibility_environment(event)

    def _is_occluded_dark_zone_event(self, event: GameEvent) -> bool:
        local_light = event.world.local_light if event.world.local_light is not None else 15
        return self._is_occluded_environment(event) and (
            (event.world.danger_darkness_score or 0.0) >= self.settings.occluded_entry_darkness_threshold
            or local_light <= 9
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

    def _prior_audio_gap_exceeded(self, threshold_ms: int) -> bool:
        return self.state.prior_recent_audio_ms is None or self.state.prior_recent_audio_ms >= threshold_ms

    def _prior_visual_gap_exceeded(self, threshold_ms: int) -> bool:
        return self.state.prior_recent_visual_ms is None or self.state.prior_recent_visual_ms >= threshold_ms

    def _hostile_label(self, hostile_type: str) -> str:
        return HOSTILE_LABELS.get(hostile_type, hostile_type)

    def _mob_label(self, mob_type: str) -> str:
        return MOB_LABELS.get(mob_type, mob_type)

    def _direction_label(self, threat: VisualThreat | AuditoryThreat) -> str:
        return DIRECTION_LABELS.get(threat.direction.horizontal, "近く")

    def _can_emit_panic_cue(self, now: datetime) -> bool:
        if self.state.panic_scream_cooldown_until is None:
            return True
        return now >= self.state.panic_scream_cooldown_until

    def _has_torch(self, inventory: dict[str, int]) -> bool:
        return self._light_source_count(inventory) > 0

    def _has_weapon(self, event: GameEvent) -> bool:
        weapon_keywords = ("sword", "axe", "bow", "crossbow", "trident", "mace")
        held_item = event.player.held_item or ""
        if any(keyword in held_item for keyword in weapon_keywords):
            return True
        return any(any(keyword in key for keyword in weapon_keywords) and value > 0 for key, value in event.inventory.items())

    def _torch_craftable(self, inventory: dict[str, int]) -> bool:
        fuel = inventory.get("coal", 0) + inventory.get("charcoal", 0)
        return fuel >= 1 and inventory.get("stick", 0) >= 1

    def _light_source_count(self, inventory: dict[str, int]) -> int:
        keys = ("torch", "soul_torch", "lantern", "soul_lantern")
        return sum(inventory.get(key, 0) for key in keys)

    def _light_source_crafted(self, event: GameEvent) -> bool:
        if event.event.name != EventName.STATUS_SNAPSHOT:
            return False
        if not self.state.inventory_initialized:
            return False
        return self._light_source_count(event.inventory) > self.state.last_light_source_count

    def _entered_occluded_dark_zone(self, event: GameEvent) -> bool:
        if event.event.name != EventName.STATUS_SNAPSHOT:
            return False
        current = self._is_occluded_dark_zone_event(event)
        previous = self.state.last_occluded_dark_zone
        return current and not bool(previous)

    def _should_warn_dark_push_no_light(
        self,
        event: GameEvent,
        signals: DerivedSignals,
        now: datetime,
    ) -> bool:
        if not signals.occluded_dark_zone or signals.torch_available:
            return False
        if event.event.name != EventName.STATUS_SNAPSHOT:
            return False
        if signals.entered_occluded_dark_zone:
            return False
        recent_ms = self._recent_ms(now, self.state.last_dark_push_comment_at)
        return recent_ms is None or recent_ms >= self.settings.dark_push_comment_cooldown_ms

    def _should_stop_dark_push_audio(self, signals: DerivedSignals) -> bool:
        if not self.state.dark_push_active:
            return False
        return (not signals.occluded_dark_zone) or signals.torch_available

    def _has_bed(self, inventory: dict[str, int]) -> bool:
        if inventory.get("bed", 0) > 0:
            return True
        return any(key.endswith("_bed") and value > 0 for key, value in inventory.items())

    def _bed_craftable(self, inventory: dict[str, int]) -> bool:
        wool_count = inventory.get("wool", 0) + sum(
            value for key, value in inventory.items() if key.endswith("_wool")
        )
        plank_count = inventory.get("planks", 0) + sum(
            value for key, value in inventory.items() if key.endswith("_planks")
        )
        log_count = inventory.get("oak_log", 0) + sum(
            value for key, value in inventory.items() if key.endswith("_log")
        )
        return wool_count >= 3 and (plank_count >= 3 or log_count >= 3)

    def _torch_materials_nearby(self, resources: list[NearbyResource]) -> bool:
        names = {resource.name for resource in resources}
        has_fuel = bool({"coal_ore", "coal", "charcoal"} & names)
        has_wood = any(name.endswith("_log") or name.endswith("_planks") for name in names)
        return has_fuel or has_wood

    def _bed_materials_nearby(self, resources: list[NearbyResource]) -> bool:
        names = {resource.name for resource in resources}
        has_wool = any(name.endswith("_wool") for name in names)
        has_wood = any(name.endswith("_log") or name.endswith("_planks") for name in names)
        return has_wool or has_wood

    def _recent_ms(self, now: datetime, when: datetime | None) -> int | None:
        if when is None:
            return None
        return int((now - when).total_seconds() * 1000)

    def _older_than(self, value_ms: int | None, threshold_ms: int) -> bool:
        return value_ms is None or value_ms > threshold_ms

    def _prune_comment_memory(self, now: datetime) -> None:
        cooldown = self.settings.hostile_comment_cooldown_ms
        self.state.commented_visual_keys = {
            visual_key: commented_at
            for visual_key, commented_at in self.state.commented_visual_keys.items()
            if self._recent_ms(now, commented_at) is not None and self._recent_ms(now, commented_at) < cooldown
        }
        self.state.commented_auditory_keys = {
            key: value
            for key, value in self.state.commented_auditory_keys.items()
            if self._recent_ms(now, value[0]) is not None and self._recent_ms(now, value[0]) < cooldown
        }
