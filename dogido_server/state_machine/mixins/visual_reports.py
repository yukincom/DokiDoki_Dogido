# state_machine/mixins/visual_reports.py
from __future__ import annotations

from datetime import datetime
from math import inf

from dogido_server.models import GameEvent, HorizontalDirection, VisualThreat
from dogido_server.state_machine.constants import *  # noqa: F403
from dogido_server.state_machine.types import DerivedSignals


class VisualReportsMixin:
    def _is_daylight_water_survivor(self, event: GameEvent, threat: VisualThreat) -> bool:
        return (
            getattr(event.world.time_phase, "value", event.world.time_phase) in {"morning", "day"}
            and bool(event.world.sky_visible)
            and threat.type in DAYLIGHT_BURN_HOSTILES
            and threat.in_water
            and not threat.on_fire
        )

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
        if commented_at is not None and self._recent_ms(now, commented_at) < self.settings.hostile_comment_cooldown_ms:
            return False
        auditory = self.state.commented_auditory_keys.get(visual_key)
        if auditory is None:
            return True
        return self._recent_ms(now, auditory[0]) >= self.settings.hostile_comment_cooldown_ms

    def _visual_priority_cooldown_active(self, now: datetime) -> bool:
        recent_ms = self._recent_ms(now, self.state.last_visual_priority_callout_at)
        return recent_ms is not None and recent_ms < self.settings.multi_hostile_comment_cooldown_ms

    def _mark_visual_priority_callout(self, now: datetime, single_type: str | None = None) -> None:
        self.state.last_visual_priority_callout_at = now
        self.state.last_single_visual_type = single_type
        self.state.last_single_visual_at = now if single_type is not None else None

    def _single_to_multi_increase_callout(self, threats: list[VisualThreat], now: datetime) -> str | None:
        last_type = self.state.last_single_visual_type
        last_at = self.state.last_single_visual_at
        if last_type is None or last_at is None:
            return None
        recent_ms = self._recent_ms(now, last_at)
        if recent_ms is None or recent_ms >= self.settings.multi_hostile_comment_cooldown_ms:
            return None
        counts = self._hostile_counts(threats)
        if counts.get(last_type, 0) < 2:
            return None
        self._mark_visual_priority_callout(now, single_type=None)
        return f"{self._hostile_label(last_type)}が増えたで！"

    def _visual_threat_priority_key(self, threat: VisualThreat) -> tuple[float, float, float, float, float, float, str]:
        distance = threat.distance if threat.distance is not None else inf
        effective_range = HOSTILE_EFFECTIVE_RANGE.get(threat.type, 3.0)
        immediate_any = 0.0 if distance <= 4.0 else 1.0
        close_melee = 0.0 if threat.type not in RANGED_HOSTILES and distance <= 4.5 else 1.0
        range_ratio = distance / max(effective_range, 0.1)
        in_range = 0.0 if distance <= effective_range else 1.0
        rear_risk = 0.0 if threat.direction.horizontal in {
            HorizontalDirection.BACK,
            HorizontalDirection.BACK_LEFT,
            HorizontalDirection.BACK_RIGHT,
        } else 1.0
        approaching = 0.0 if threat.approaching else 1.0
        return (immediate_any, close_melee, in_range, range_ratio, rear_risk, approaching, threat.type)

    def _visual_scene_acknowledged(self, threats: list[VisualThreat], now: datetime) -> bool:
        for threat in threats:
            visual_key = self._visual_identity_key(threat)
            commented_at = self.state.commented_visual_keys.get(visual_key)
            if commented_at is not None and self._recent_ms(now, commented_at) < self.settings.hostile_comment_cooldown_ms:
                return True

        counts = self._hostile_counts(threats)
        if len(counts) >= 2:
            signature = self._multi_species_signature(counts)
            recent_ms = self._recent_ms(now, self.state.last_multi_species_report_at)
            if (
                signature == self.state.last_multi_species_signature
                and recent_ms is not None
                and recent_ms < self.settings.multi_hostile_comment_cooldown_ms
            ):
                return True

        recent_multi_ms = self._recent_ms(now, self.state.last_multi_hostile_report_at)
        if (
            len(threats) >= 2
            and self.state.last_multi_hostile_count == len(threats)
            and recent_multi_ms is not None
            and recent_multi_ms < self.settings.multi_hostile_comment_cooldown_ms
        ):
            return True

        return False

    def _hostile_counts(self, threats: list[VisualThreat]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for threat in threats:
            counts[threat.type] = counts.get(threat.type, 0) + 1
        return counts

    def _visual_group_signature(self, threats: list[VisualThreat]) -> str:
        # ドラゴンは長期戦が前提なので「まだ敵おるやん」系の停滞プレッシャー対象から外す
        keys = sorted(
            self._visual_identity_key(threat)
            for threat in threats
            if (threat.type or "").strip().lower() != "ender_dragon"
        )
        return "|".join(keys)

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

    def _multi_hostile_callout(
        self,
        event: GameEvent,
        signals: DerivedSignals,
        now: datetime,
        suppressed: bool = False,
    ) -> str | None:
        count = signals.visual_threat_count_within_10
        if count < 2 or not event.visual_threats:
            return None
        recent_ms = self._recent_ms(now, self.state.last_multi_hostile_report_at)
        if (
            count == self.state.last_multi_hostile_count
            and recent_ms is not None
            and recent_ms < self.settings.multi_hostile_comment_cooldown_ms
        ):
            return None
        self.state.last_multi_hostile_report_at = now
        self.state.last_multi_hostile_count = count
        self._mark_visual_priority_callout(now, single_type=None)
        counts = self._hostile_counts(event.visual_threats)
        return self._hostile_count_summary(event, counts, suppressed=suppressed, threats=event.visual_threats)

    def _multi_species_callout(
        self,
        event: GameEvent,
        threats: list[VisualThreat],
        now: datetime,
        suppressed: bool,
    ) -> str | None:
        counts = self._hostile_counts(threats)
        if len(counts) < 2:
            return None
        ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        parts = [f"{self._hostile_label(hostile)}{count}体" for hostile, count in ordered[:3]]
        signature = self._multi_species_signature(counts)
        recent_ms = self._recent_ms(now, self.state.last_multi_species_report_at)
        if (
            signature == self.state.last_multi_species_signature
            and recent_ms is not None
            and recent_ms < self.settings.multi_hostile_comment_cooldown_ms
        ):
            return None
        self.state.last_multi_species_signature = signature
        self.state.last_multi_species_report_at = now
        self._mark_visual_priority_callout(now, single_type=None)
        return self._hostile_count_summary(event, dict(ordered), suppressed=suppressed, threats=threats)

    def _multi_species_signature(self, counts: dict[str, int]) -> str:
        ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        return "|".join(f"{hostile}:{count}" for hostile, count in ordered)

    def _overwhelmed_callout(
        self,
        threats: list[VisualThreat],
        event: GameEvent,
        now: datetime,
        suppressed: bool,
    ) -> str | None:
        if len(threats) < 4:
            return None
        if self._is_other_realm_swarm_scene(event, visual_count=len(threats)):
            recent_ms = self._recent_ms(now, self.state.last_overwhelmed_report_at)
            if recent_ms is not None and recent_ms < self.settings.multi_hostile_comment_cooldown_ms:
                return None
            self.state.last_overwhelmed_report_at = now
            self._mark_visual_priority_callout(now, single_type=None)
            return self._hostile_massive_callout(event, suppressed=suppressed)
        if len(threats) >= 9 and not self._contains_boss_hostile(threats):
            recent_ms = self._recent_ms(now, self.state.last_overwhelmed_report_at)
            if recent_ms is not None and recent_ms < self.settings.multi_hostile_comment_cooldown_ms:
                return None
            self.state.last_overwhelmed_report_at = now
            self._mark_visual_priority_callout(now, single_type=None)
            return self._hostile_massive_callout(event, suppressed=suppressed)
        support_targets = self._overwhelmed_support_targets(threats)
        signature_parts = sorted(self._visual_identity_key(threat) for threat in threats)
        signature = "|".join(signature_parts)
        recent_ms = self._recent_ms(now, self.state.last_overwhelmed_report_at)
        if recent_ms is not None and recent_ms < self.settings.multi_hostile_comment_cooldown_ms:
            return None
        self.state.last_overwhelmed_signature = signature
        self.state.last_overwhelmed_report_at = now
        self._mark_visual_priority_callout(now, single_type=None)
        if not support_targets:
            return "あかんあかんあかん……もうあかん……。" if suppressed else "あかんあかんあかん！もうあかん！四方八方敵やんけ！俺もう終わりや〜！"

        parts = [
            f"{self._direction_label(threat)}に{self._hostile_label(threat.type)}"
            for threat in support_targets[:2]
        ]
        joined = "、".join(parts)
        if suppressed:
            return f"あかんあかん……。{joined}……。"
        return f"あかんあかんあかん！もうあかん！四方八方敵やんけ！ {joined}おる！"

    def _overwhelmed_support_targets(self, threats: list[VisualThreat]) -> list[VisualThreat]:
        filtered = [
            threat for threat in threats
            if threat.type in RANGED_HOSTILES or threat.type in HIGH_THREAT_SUPPORT_HOSTILES
        ]
        if not filtered:
            return []
        picked: list[VisualThreat] = []
        seen_types: set[str] = set()
        for threat in sorted(filtered, key=self._visual_threat_priority_key):
            if threat.type in seen_types:
                continue
            picked.append(threat)
            seen_types.add(threat.type)
        return picked
