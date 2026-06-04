# state_machine/mixins/narration.py
from __future__ import annotations

from dogido_server.entry_catalog import mob_entry, mob_poetic_tags
from dogido_server.models import GameEvent, PeacefulMob
from dogido_server.state_machine.ambient_mob_catalog import (
    AmbientMobReactionContext,
    ambient_mob_fallback_candidates,
)
from dogido_server.state_machine.fallback_catalog import dark_push_after_breath_fallback, death_fallback_text, fallback_text
from dogido_server.state_machine.response_catalog import response_text
from dogido_server.state_machine.types import DerivedSignals


class NarrationMixin:
    def _ambient_mob_line(self, event: GameEvent, mobs: list[PeacefulMob]) -> str | None:
        if not mobs:
            return None
        candidates = self._ambient_mob_fallback_candidates(event, mobs)
        if not candidates:
            return None
        return candidates[0]

    def _render_ambient_mob_line(self, event: GameEvent, mobs: list[PeacefulMob]) -> str | None:
        fallback = self._ambient_mob_line(event, mobs)
        if fallback is None or not mobs:
            return fallback
        mob = mobs[0]
        direction = self._direction_label(mob)
        entry = mob_entry(mob.type) or {}
        poetic = entry.get("poetic") if isinstance(entry, dict) else {}
        role = poetic.get("role") if isinstance(poetic, dict) else ""
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
                "fallback_candidates": self._ambient_mob_fallback_candidates(event, mobs),
            },
        )

    def _ambient_mob_fallback_candidates(self, event: GameEvent, mobs: list[PeacefulMob]) -> list[str]:
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
