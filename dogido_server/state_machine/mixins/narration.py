# state_machine/mixins/narration.py
from __future__ import annotations

from datetime import datetime

from dogido_server.entry_catalog import mob_entry, mob_poetic_tags
from dogido_server.models import GameEvent, PassiveMob
from dogido_server.state_machine.ambient_mob_catalog import (
    AmbientMobReactionContext,
    ambient_mob_fallback_candidates,
)
from dogido_server.state_machine.fallback_catalog import dark_push_after_breath_fallback, death_fallback_text, fallback_text
from dogido_server.state_machine.response_catalog import (
    response_lines,
    response_text,
    structure_entry_fallback_text,
)
from dogido_server.state_machine.types import DerivedSignals


class NarrationMixin:
    def _ambient_mob_line(self, event: GameEvent, mobs: list[PassiveMob]) -> str | None:
        if not mobs:
            return None
        candidates = self._ambient_mob_fallback_candidates(event, mobs)
        if not candidates:
            return None
        return candidates[0]

    def _render_ambient_mob_line(self, event: GameEvent, mobs: list[PassiveMob]) -> str | None:
        fallback = self._ambient_mob_line(event, mobs)
        if fallback is None or not mobs:
            return fallback
        mob = mobs[0]
        direction = self._direction_label(mob)
        entry = mob_entry(mob.type) or {}
        poetic = entry.get("poetic") if isinstance(entry, dict) else {}
        role = poetic.get("role") if isinstance(poetic, dict) else ""
        variation_slot = event.sequence % 4 if event.sequence is not None else 0
        return self._generate_leaf_text(
            kind="ambient",
            fallback_text=fallback,
            details={
                "mob": self._mob_label(mob.type),
                "direction": direction,
                "mob_count": len(mobs),
                "distance": mob.distance,
                "biome": self._biome_label(event.world.biome),
                "time_phase": getattr(event.world.time_phase, "value", event.world.time_phase) or "unknown",
                "mob_tags": list(mob_poetic_tags(mob.type))[:8],
                "mob_role": str(role) if role else "",
                "mob_temperament": getattr(mob, "temperament", None) or "friendly",
                "mob_caution_reason": getattr(mob, "caution_reason", None) or "",
                "fallback_candidates": self._ambient_mob_fallback_candidates(event, mobs),
                "variation_slot": variation_slot,
            },
            temperature=0.48,
        )

    def _ambient_mob_type_key(self, mob: PassiveMob) -> str:
        return (mob.type or "").strip().lower()

    def _next_ambient_mob_target(self, mobs: list[PassiveMob], now: datetime) -> PassiveMob | None:
        for mob in mobs:
            key = self._ambient_mob_type_key(mob)
            if not key:
                continue
            recent_ms = self._recent_ms(
                now, self.state.last_ambient_mob_comment_at_by_type.get(key)
            )
            if recent_ms is None or recent_ms >= self.settings.ambient_mob_comment_cooldown_ms:
                return mob
        return None

    def _emit_ambient_mob_comment_line(self, event: GameEvent, now: datetime) -> str | None:
        # クールダウンは種ごと。別の種ならすぐ反応してよい
        # （⭕️「うしさんや」→「にわとりさんや」 ❌「うしさんや」→「うしさんや」）
        target = self._next_ambient_mob_target(event.passive_mobs, now)
        if target is None:
            return None
        ordered_mobs = [target] + [mob for mob in event.passive_mobs if mob is not target]
        line = self._render_ambient_mob_line(event, ordered_mobs)
        if not line:
            return None
        self.state.last_ambient_mob_comment_at = now
        self.state.last_ambient_mob_comment_at_by_type[self._ambient_mob_type_key(target)] = now
        # モブ反応が優先。発句中の川柳はキャンセルし、静けさが戻ってから再発句する
        self.state.pending_haiku_after_preface = False
        return line

    def _ambient_mob_fallback_candidates(self, event: GameEvent, mobs: list[PassiveMob]) -> list[str]:
        if not mobs:
            return []
        mob = mobs[0]
        inventory_item_ids = frozenset(
            item_id.split(":")[-1].strip().lower()
            for item_id, count in event.inventory.items()
            if count > 0
        )
        context = AmbientMobReactionContext(
            mob_type=mob.type,
            mob_label=self._mob_label(mob.type),
            inventory_item_ids=inventory_item_ids,
            temperament=getattr(mob, "temperament", None),
            caution_reason=getattr(mob, "caution_reason", None),
        )
        return ambient_mob_fallback_candidates(context)

    def _death_message(self, event: GameEvent) -> str:
        return death_fallback_text(event.meta.death_cause)

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
            details={
                "cause": event.meta.death_cause or "unknown",
                "hostile": hostile,
                "player_name": self._player_call_name(event),
            },
        )

    def _render_aftermath_line(self, event: GameEvent) -> str:
        if any(hostile == "warden" for hostile in self.state.last_confirmed_hostiles):
            return response_text("boss", "warden", "defeated")
        fallback = fallback_text("aftermath", "line")
        health = event.player.health
        recent_combat_end_ms = self._recent_ms(event.observed_at, self.state.last_combat_end_at)
        if health is None:
            health_state = "不明"
        elif health <= 8:
            health_state = "かなり減ってる"
        elif health <= 14:
            health_state = "少し減ってる"
        else:
            health_state = "まだ余力はある"
        hostiles = (
            list(self.state.last_confirmed_hostiles)
            if recent_combat_end_ms is not None
            and recent_combat_end_ms <= self.settings.pending_safe_aftermath_window_ms
            else []
        )
        return self._generate_leaf_text(
            kind="aftermath",
            fallback_text=fallback,
            details={
                "player_name": self._player_call_name(event),
                "hostiles": hostiles,
                "health_state": health_state,
            },
        )

    def _render_darkness_escape_line(self, event: GameEvent) -> str | None:
        if self._is_safe_zone_with_door_event(event):
            return None
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
        fallback = fallback_text("general", "darkness", "darkness_escape", prefix=self._player_call_prefix(event))
        return self._generate_leaf_text(
            kind="darkness_escape",
            fallback_text=fallback,
            details={
                "player_name": self._player_call_name(event),
                "hostiles": hostiles,
                "biome": self._biome_label(event.world.biome),
                "time_phase": getattr(event.world.time_phase, "value", event.world.time_phase) or "unknown",
            },
            temperature=0.62,
        )

    def _render_submerged_darkness_line(self, event: GameEvent) -> str | None:
        depth = event.world.submerged_depth_blocks or 0
        if depth < self.settings.submerged_darkness_depth_threshold:
            return None
        now = event.observed_at
        if (
            self.state.last_submerged_darkness_advice_at is not None
            and self._recent_ms(now, self.state.last_submerged_darkness_advice_at) is not None
            and self._recent_ms(now, self.state.last_submerged_darkness_advice_at)
            < self.settings.submerged_darkness_comment_cooldown_ms
        ):
            return None
        self.state.last_submerged_darkness_advice_at = now
        return response_text("darkness", "darkness", "submerged_entry")

    def _render_emergency_shelter_relief_line(self, event: GameEvent) -> str:
        return self._generate_leaf_text(
            kind="emergency_shelter_relief",
            fallback_text=fallback_text(
                "general",
                "darkness",
                "emergency_shelter_relief",
                prefix=self._player_call_prefix(event),
            ),
            details={
                "player_name": self._player_call_name(event),
                "biome": self._biome_label(event.world.biome),
                "time_phase": getattr(event.world.time_phase, "value", event.world.time_phase) or "unknown",
                "ceiling_height": event.world.ceiling_height,
                "enclosure_score": event.world.enclosure_score,
            },
            temperature=0.5,
        )

    def _render_occluded_entry_line(self, event: GameEvent, signals: DerivedSignals) -> str | None:
        if signals.torch_available:
            return self._generate_leaf_text(
                kind="occluded_entry_with_light",
                fallback_text=fallback_text(
                    "general",
                    "darkness",
                    "occluded_entry_with_light",
                    prefix=self._player_call_prefix(event),
                ),
                details={
                    "player_name": self._player_call_name(event),
                    "biome": self._biome_label(event.world.biome),
                    "time_phase": getattr(event.world.time_phase, "value", event.world.time_phase) or "unknown",
                    "local_light": event.world.local_light,
                },
                temperature=0.5,
            )
        return self._generate_leaf_text(
            kind="occluded_entry_no_light",
            fallback_text=fallback_text(
                "general",
                "darkness",
                "occluded_entry_no_light",
                prefix=self._player_call_prefix(event),
            ),
            details={
                "player_name": self._player_call_name(event),
                "biome": self._biome_label(event.world.biome),
                "time_phase": getattr(event.world.time_phase, "value", event.world.time_phase) or "unknown",
                "craftable": signals.torch_craftable,
                "local_light": event.world.local_light,
            },
            temperature=0.42,
        )

    def _render_dark_push_no_light_line(self, event: GameEvent) -> str | None:
        hostiles = [self._hostile_label(threat.type) for threat in event.visual_threats]
        if not hostiles and event.auditory_threats:
            hostiles = ["気配あり"]
        return self._generate_leaf_text(
            kind="dark_push_no_light",
            fallback_text=fallback_text(
                "general",
                "darkness",
                "dark_push_no_light",
                prefix=self._player_call_prefix(event),
            ),
            details={
                "player_name": self._player_call_name(event),
                "biome": self._biome_label(event.world.biome),
                "time_phase": getattr(event.world.time_phase, "value", event.world.time_phase) or "unknown",
                "hostiles": hostiles,
                "local_light": event.world.local_light,
            },
            temperature=0.58,
        )

    def _render_dark_push_after_breath_line(self, event: GameEvent) -> str | None:
        if self._boss_recently_seen(event.observed_at):
            return None
        hostiles = [self._hostile_label(threat.type) for threat in event.visual_threats]
        if not hostiles and event.auditory_threats:
            hostiles = ["気配あり"]
        time_phase = self._effective_time_phase(event) or "unknown"
        fallback = dark_push_after_breath_fallback(
            time_phase,
            prefix=self._player_call_prefix(event),
        )
        line = self._generate_leaf_text(
            kind="dark_push_after_breath",
            fallback_text=fallback,
            details={
                "player_name": self._player_call_name(event),
                "biome": self._biome_label(event.world.biome),
                "time_phase": time_phase,
                "hostiles": hostiles,
                "local_light": event.world.local_light,
            },
            temperature=0.5,
        )
        if time_phase not in {"evening", "night"} and any(token in line for token in {"夜", "夕方", "朝"}):
            return fallback
        if time_phase not in {"evening", "night"} and "一難" in line:
            return fallback_text(
                "general",
                "darkness",
                "dark_push_after_breath_default",
                prefix=self._player_call_prefix(event),
            )
        if time_phase == "evening" and "まだ夜" in line:
            return line.replace("まだ夜", "もう夜")
        return line

    def _render_deep_dark_ominous_sound_line(self, event: GameEvent, kind: str, stage: int) -> str:
        return self._generate_leaf_text(
            kind="deep_dark_ominous_sound",
            fallback_text=self._deep_dark_ominous_fallback(event, kind, stage),
            details={
                "player_name": self._player_call_name(event),
                "biome": self._biome_label(event.world.biome),
                "time_phase": getattr(event.world.time_phase, "value", event.world.time_phase) or "unknown",
                "ominous_kind": kind,
                "ominous_stage": stage,
                "variation_hint": self._select_deterministic_line(
                    f"{kind}:{stage}:{event.sequence or 0}",
                    (
                        "反響",
                        "悲鳴っぽさ",
                        "静けさ",
                        "嫌な予感",
                    ),
                ),
            },
            temperature=0.6,
        )

    def _deep_dark_ominous_fallback(self, event: GameEvent, kind: str, stage: int) -> str:
        if kind == "warden_heartbeat":
            if stage <= 1:
                return response_text("boss", "warden", "heartbeat_first")
            return response_text("boss", "warden", "heartbeat_close")
        if kind == "warden_presence":
            return response_text("boss", "warden", "heartbeat_close")
        key = "sculk_shrieker_fallbacks" if kind == "sculk_shrieker" else "sculk_sensor_fallbacks"
        lines = response_lines("boss", "deep_dark", key)
        return self._select_deterministic_line(
            f"{kind}:{event.sequence or 0}:{stage}",
            lines,
        )

    PORTAL_LABELS: dict[str, str] = {
        "nether_portal": "ネザーポータル",
        "end_portal": "エンドポータル",
        "end_gateway": "エンドゲートウェイ",
    }

    def _portal_label(self, portal_type: str) -> str:
        return self.PORTAL_LABELS.get(portal_type, portal_type)

    def _render_portal_appearance_line(self, event: GameEvent, portal_type: str) -> str:
        fallback = response_text("exploration", "portal", "appearance_fallbacks", portal_type)
        return self._generate_leaf_text(
            kind="portal_appearance",
            fallback_text=fallback,
            details={
                "player_name": self._player_call_name(event),
                "portal_type": portal_type,
                "portal_label": self._portal_label(portal_type),
                "portal_distance": event.world.nearby_portal_distance,
                "biome": self._biome_label(event.world.biome),
                "time_phase": getattr(event.world.time_phase, "value", event.world.time_phase) or "unknown",
                "dimension": self._normalized_dimension(event),
            },
            temperature=0.55,
        )

    def _render_structure_entry_line(self, event: GameEvent, structure_key: str) -> str | None:
        fallback = structure_entry_fallback_text(structure_key)
        if fallback is None:
            return None
        entry = self._structure_entry(structure_key) or {}
        group_id = str(entry.get("group_id") or "")
        biome = "地下" if group_id == "overworld_underground" else self._biome_label(event.world.biome)
        return self._generate_leaf_text(
            kind="structure_entry",
            fallback_text=fallback,
            details={
                "player_name": self._player_call_name(event),
                "structure": structure_key,
                "structure_label": str(entry.get("label") or structure_key),
                "structure_note": str(entry.get("note") or ""),
                "group_label": str(entry.get("group_label") or ""),
                "biome": biome,
                "time_phase": getattr(event.world.time_phase, "value", event.world.time_phase) or "unknown",
            },
            temperature=0.55,
        )

    def _render_ender_eye_throw_line(self, event: GameEvent) -> str:
        # 何度も投げる行動なので、印象は控えめ・短め（TTS 向け）の固定候補を軸にする
        lines = response_lines("exploration", "ender_eye", "throw", "lines")
        fallback = self._select_deterministic_line(
            f"ender_eye:{event.sequence or 0}",
            lines,
        )
        return self._generate_leaf_text(
            kind="ender_eye_throw",
            fallback_text=fallback,
            details={
                "player_name": self._player_call_name(event),
                "biome": self._biome_label(event.world.biome),
                "time_phase": getattr(event.world.time_phase, "value", event.world.time_phase) or "unknown",
                "reference_lines": list(lines),
            },
            temperature=0.4,
        )

    def _render_light_crafted_line(self, event: GameEvent) -> str:
        return self._generate_leaf_text(
            kind="light_crafted",
            fallback_text=fallback_text(
                "general",
                "darkness",
                "light_crafted",
                prefix=self._player_call_prefix(event),
            ),
            details={
                "player_name": self._player_call_name(event),
                "biome": self._biome_label(event.world.biome),
                "time_phase": getattr(event.world.time_phase, "value", event.world.time_phase) or "unknown",
                "light_count": self._light_source_count(event.inventory),
            },
            temperature=0.62,
        )
