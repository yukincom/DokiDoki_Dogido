# state_machine/mixins/visual_targets.py
from __future__ import annotations

from datetime import datetime
from math import inf

from dogido_server.models import GameEvent, HorizontalDirection, VerticalRelation, VisualThreat
from dogido_server.state_machine.constants import *  # noqa: F403


class VisualTargetsMixin:
    def _render_hostile_visual_callout(self, threat: VisualThreat, mode: str) -> str:
        if threat.type == "charged_creeper":
            return CHARGED_CREEPER_CALL
        if threat.type == "warden":
            return "ウォーデンや！音立てんと離れよ！！"
        if threat.type == "ender_dragon":
            return "上や！ドラゴン来るで！" if threat.direction.vertical == VerticalRelation.ABOVE else "ドラゴンや！回り込んどるで！"
        if threat.type == "wither":
            return "ウィザーや！射線切れるとこ探そ！"
        if threat.type == "elder_guardian":
            return "エルダーガーディアンや！射線切らなあかん！"
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

    def _render_flying_visual_callout(self, threat: VisualThreat) -> str:
        return f"上から{self._hostile_label(threat.type)}きたで！"

    def _alert_cue_id(self, event: GameEvent, previous_mode: str) -> str | None:
        if previous_mode in {"alert", "panic", "suppressed_panic"}:
            return None
        if event.visual_threats or event.auditory_threats:
            return "spot_hostile_gasp"
        return None

    def _should_emit_spotted_hostile_gasp(self, event: GameEvent) -> bool:
        if self._is_other_realm_swarm_scene(event):
            return False
        threat = self._highest_priority_visual(event.visual_threats)
        if threat is None:
            return False
        if threat.distance is None:
            return False
        if threat.direction.horizontal in {
            HorizontalDirection.BACK,
            HorizontalDirection.BACK_LEFT,
            HorizontalDirection.BACK_RIGHT,
        } and threat.distance <= self.settings.rear_warning_distance:
            return True
        if threat.type in RANGED_HOSTILES:
            return threat.distance <= HOSTILE_EFFECTIVE_RANGE.get(threat.type, 6.0) + 1.5
        return threat.distance <= 6.0 or (threat.approaching and threat.distance <= 7.0)

    def _suppressed_cue(self, previous_mode: str) -> tuple[str, str]:
        if previous_mode != "suppressed_panic":
            return ("suppressed_gasp", "ひいっ！")
        return ("suppressed_breath", "ハァハァ……")

    def _nearest_visual(self, threats: list[VisualThreat]) -> VisualThreat | None:
        if not threats:
            return None
        return min(threats, key=lambda threat: threat.distance if threat.distance is not None else inf)

    def _highest_priority_visual(self, threats: list[VisualThreat]) -> VisualThreat | None:
        if not threats:
            return None
        return min(threats, key=self._visual_threat_priority_key)

    def _next_visual_comment_target(self, threats: list[VisualThreat], now: datetime) -> VisualThreat | None:
        counts = self._hostile_counts(threats)
        ordered = sorted(threats, key=self._visual_threat_priority_key)
        for threat in ordered:
            visual_key = self._visual_identity_key(threat)
            if not self._visual_comment_allowed(visual_key, now):
                continue
            self.state.commented_visual_keys[visual_key] = now
            self.state.announced_hostile_counts[threat.type] = max(
                counts.get(threat.type, 1),
                self.state.announced_hostile_counts.get(threat.type, 0),
            )
            self._mark_visual_priority_callout(
                now,
                single_type=threat.type if counts.get(threat.type, 0) == 1 else None,
            )
            return threat
        return None

    def _new_priority_visual_target(self, threats: list[VisualThreat], now: datetime) -> VisualThreat | None:
        uncommented: list[VisualThreat] = []
        scene_acknowledged = self._visual_scene_acknowledged(threats, now)
        for threat in sorted(threats, key=self._visual_threat_priority_key):
            visual_key = self._visual_identity_key(threat)
            if self._visual_comment_allowed(visual_key, now):
                uncommented.append(threat)
        if not scene_acknowledged or not uncommented:
            return None
        target = uncommented[0]
        if target.distance > 3.0:
            self.state.commented_visual_keys[self._visual_identity_key(target)] = now
            counts = self._hostile_counts(threats)
            self._mark_visual_priority_callout(
                now,
                single_type=target.type if counts.get(target.type, 0) == 1 else None,
            )
        return target

    def _peek_new_close_visual_ambush_target(
        self,
        event: GameEvent,
        now: datetime,
    ) -> VisualThreat | None:
        candidates: list[VisualThreat] = []
        commented_seen = False
        for threat in event.visual_threats:
            if self._is_daylight_water_survivor(event, threat):
                continue
            visual_key = self._visual_identity_key(threat)
            seen_at = self.state.seen_visual_keys.get(visual_key)
            if seen_at is not None and self._recent_ms(now, seen_at) is not None:
                commented_seen = True
                continue
            screamed_at = self.state.screamed_visual_keys.get(visual_key)
            if screamed_at is not None and self._recent_ms(now, screamed_at) < self.settings.hostile_comment_cooldown_ms:
                commented_seen = True
                continue
            if visual_key in self.state.commented_visual_keys:
                commented_seen = True
                continue
            if threat.distance is not None and threat.distance <= 3.0:
                candidates.append(threat)
        if not candidates:
            return None
        if not (commented_seen or len(event.visual_threats) == 1):
            return None
        return min(candidates, key=lambda threat: threat.distance if threat.distance is not None else inf)

    def _peek_ushiro_ambush_target(
        self,
        event: GameEvent,
        now: datetime,
    ) -> VisualThreat | None:
        recent_ms = self._recent_ms(now, self.state.last_ushiro_call_at)
        if recent_ms is not None and recent_ms < self.settings.ushiro_comment_cooldown_ms:
            return None
        candidates: list[VisualThreat] = []
        for threat in event.visual_threats:
            if threat.type in RANGED_HOSTILES:
                continue
            if threat.direction.horizontal != HorizontalDirection.BACK:
                continue
            if threat.distance is None or threat.distance > self.settings.rear_warning_distance:
                continue
            visual_key = self._visual_identity_key(threat)
            screamed_at = self.state.screamed_visual_keys.get(visual_key)
            if screamed_at is not None and self._recent_ms(now, screamed_at) < self.settings.hostile_comment_cooldown_ms:
                continue
            candidates.append(threat)
        if not candidates:
            return None
        return min(candidates, key=lambda threat: threat.distance if threat.distance is not None else inf)

    def _consume_new_close_visual_ambush_target(
        self,
        event: GameEvent,
        now: datetime,
    ) -> VisualThreat | None:
        target = self._peek_new_close_visual_ambush_target(event, now)
        if target is not None:
            self.state.screamed_visual_keys[self._visual_identity_key(target)] = now
        return target

    def _peek_dark_push_forward_ambush_target(
        self,
        event: GameEvent,
        now: datetime,
    ) -> VisualThreat | None:
        if not (self.state.dark_push_active or self.state.dark_push_stage >= 1):
            return None
        if not self._is_occluded_environment(event):
            return None
        candidates: list[VisualThreat] = []
        commented_seen = False
        for threat in event.visual_threats:
            if self._is_daylight_water_survivor(event, threat):
                continue
            if threat.direction.horizontal not in {
                HorizontalDirection.FRONT,
                HorizontalDirection.FRONT_LEFT,
                HorizontalDirection.FRONT_RIGHT,
            }:
                continue
            if threat.distance is None or threat.distance > 4.0:
                continue
            visual_key = self._visual_identity_key(threat)
            seen_at = self.state.seen_visual_keys.get(visual_key)
            if seen_at is not None and self._recent_ms(now, seen_at) is not None:
                commented_seen = True
                continue
            screamed_at = self.state.screamed_visual_keys.get(visual_key)
            if screamed_at is not None and self._recent_ms(now, screamed_at) < self.settings.hostile_comment_cooldown_ms:
                commented_seen = True
                continue
            if visual_key in self.state.commented_visual_keys:
                commented_seen = True
                continue
            candidates.append(threat)
        if not candidates:
            return None
        if not (commented_seen or len(event.visual_threats) == 1):
            return None
        return min(candidates, key=lambda threat: threat.distance if threat.distance is not None else inf)

    def _consume_dark_push_forward_ambush_target(
        self,
        event: GameEvent,
        now: datetime,
    ) -> VisualThreat | None:
        target = self._peek_dark_push_forward_ambush_target(event, now)
        if target is not None:
            visual_key = self._visual_identity_key(target)
            self.state.screamed_visual_keys[visual_key] = now
            self.state.commented_visual_keys[visual_key] = now
            self._mark_visual_priority_callout(now, single_type=target.type)
        return target

    def _consume_ushiro_ambush_target(
        self,
        event: GameEvent,
        now: datetime,
    ) -> VisualThreat | None:
        target = self._peek_ushiro_ambush_target(event, now)
        if target is not None:
            self.state.screamed_visual_keys[self._visual_identity_key(target)] = now
            self.state.last_ushiro_call_at = now
            self._mark_visual_priority_callout(now, single_type=None)
        return target
