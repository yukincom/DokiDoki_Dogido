# state_machine/mixins/common.py
from __future__ import annotations

from datetime import datetime
from functools import lru_cache

from dogido_server.entry_catalog import neutral_mob_entries
from dogido_server.models import EventName, GameEvent, HorizontalDirection, VisualThreat
from dogido_server.state_machine.constants import *  # noqa: F403
from dogido_server.state_machine.response_catalog import (
    response_lines,
    response_text,
    selected_ushiro_call_text,
    special_biome_entry_lines,
)


@lru_cache(maxsize=1)
def _neutral_mob_type_set() -> frozenset[str]:
    return frozenset(neutral_mob_entries().keys())


class CommonMixin:
    def _active_status_effects(self, event: GameEvent) -> set[str]:
        return {
            effect.split(":")[-1].strip().lower()
            for effect in (event.player.active_status_effects or [])
            if isinstance(effect, str) and effect.strip()
        }

    def _entered_status_effect(self, event: GameEvent, effect_id: str) -> bool:
        if event.event.name != EventName.STATUS_SNAPSHOT:
            return False
        normalized = effect_id.strip().lower()
        current = self._active_status_effects(event)
        return normalized in current and normalized not in self.state.last_active_status_effects

    def _is_boss_type(self, hostile_type: str | None) -> bool:
        return (hostile_type or "").strip().lower() in BOSS_HOSTILES

    def _boss_panic_policy(self, hostile_type: str | None) -> str | None:
        normalized = (hostile_type or "").strip().lower()
        if normalized in TACTICAL_BOSS_HOSTILES:
            return "tactical"
        if normalized in REVEAL_ONLY_BOSS_HOSTILES:
            return "reveal_only"
        return None

    def _highest_priority_boss_visual(self, threats: list[VisualThreat]) -> VisualThreat | None:
        bosses = [threat for threat in threats if self._is_boss_type(threat.type)]
        if not bosses:
            return None
        return min(bosses, key=self._visual_threat_priority_key)

    def _is_new_visual_reveal(self, threat: VisualThreat) -> bool:
        visual_key = self._visual_identity_key(threat)
        if self._is_boss_type(threat.type):
            return visual_key not in self.state.seen_boss_visual_keys
        return visual_key not in self.state.seen_visual_keys

    def _boss_recently_seen(self, now: datetime) -> bool:
        recent_ms = self._recent_ms(now, self.state.last_visual_threat_at)
        if recent_ms is None or recent_ms >= self.settings.boss_recent_visual_window_ms:
            return False
        return any(self._is_boss_type(hostile) for hostile in self.state.last_confirmed_hostiles)

    def _boss_presence_active(self, now: datetime) -> bool:
        return self._boss_recently_seen(now)

    def _is_warden_visual_present(self, event: GameEvent) -> bool:
        return any((threat.type or "").strip().lower() == "warden" for threat in event.visual_threats)

    def _is_warden_combat_context_active(self, event: GameEvent, now: datetime) -> bool:
        if self._is_warden_visual_present(event):
            return True
        recent_ms = self._recent_ms(now, self.state.last_visual_threat_at)
        if recent_ms is None or recent_ms >= self.settings.boss_recent_visual_window_ms:
            return False
        return "warden" in self.state.last_confirmed_hostiles

    def _has_new_warden_visual_reveal(self, event: GameEvent) -> bool:
        return any(
            (threat.type or "").strip().lower() == "warden" and self._is_new_visual_reveal(threat)
            for threat in event.visual_threats
        )

    def _boss_defeat_confirmed(self, event: GameEvent) -> bool:
        bosses = [hostile for hostile in self.state.last_confirmed_hostiles if self._is_boss_type(hostile)]
        if not bosses:
            return True
        if "warden" in bosses and not bool(event.combat.warden_defeat_confirmed):
            return False
        if "ender_dragon" in bosses and not bool(event.combat.dragon_defeat_confirmed):
            return False
        return True

    def _reset_warden_combat_comment_state(self) -> None:
        self.state.last_warden_chasing_comment_at = None
        self.state.last_warden_sonic_boom_scream_at = None
        self.state.warden_attack_start_announced = False
        self.state.warden_golem_army_announced = False
        self.state.warden_extreme_tactic_announced = False

    def _next_warden_special_callout(self, event: GameEvent, now: datetime) -> str | None:
        if (
            not self._is_warden_combat_context_active(event, now)
            or self.player_input.asks_hostile_count
            or self._has_new_warden_visual_reveal(event)
        ):
            return None
        crystal_active = bool(event.combat.warden_end_crystal_bombardment_active)
        tnt_active = bool(event.combat.warden_tnt_minecart_setup_active)
        golem_active = (event.combat.warden_nearby_iron_golem_count or 0) >= 2
        ranged_trap_active = bool(event.combat.warden_ranged_trap_active)
        # ゴーレムリンチだけ専用ライン。クリスタル爆破・TNT装置・上空ちくちくは
        # 共通の「そこまでしてウォーデンを！」で受ける
        extreme_active = crystal_active or tnt_active or ranged_trap_active
        tactic_active = extreme_active or golem_active

        if golem_active and not self.state.warden_golem_army_announced:
            self.state.warden_golem_army_announced = True
            return response_text("boss", "warden", "golem_army")

        if extreme_active and not self.state.warden_extreme_tactic_announced:
            self.state.warden_extreme_tactic_announced = True
            return response_text("boss", "warden", "extreme_tactics")

        if bool(event.combat.warden_recently_hurt) and not tactic_active and not self.state.warden_attack_start_announced:
            self.state.warden_attack_start_announced = True
            return response_text("boss", "warden", "attack_start")

        return None

    def _reset_dragon_combat_comment_state(self) -> None:
        self.state.dragon_perch_announced = False
        self.state.last_dragon_approach_callout_at = None
        self.state.pending_crystal_count_announce = None
        self.state.dragon_crystal_hint_announced = False

    def _normalized_dragon_phase(self, event: GameEvent) -> str | None:
        return (event.combat.dragon_phase or "").strip().lower() or None

    def _is_dragon_combat_context_active(self, event: GameEvent, now: datetime) -> bool:
        if self._normalized_dragon_phase(event) is not None:
            return True
        if any((threat.type or "").strip().lower() == "ender_dragon" for threat in event.visual_threats):
            return True
        recent_ms = self._recent_ms(now, self.state.last_dragon_seen_at)
        return recent_ms is not None and recent_ms <= 20000

    def _dragon_special_pending(self, event: GameEvent, now: datetime) -> bool:
        """副作用なしの『ドラゴン特殊コールアウトが出せるか』チェック（py_tree ゲート用）。"""
        if not self._is_dragon_combat_context_active(event, now):
            return False
        phase = self._normalized_dragon_phase(event)
        if phase in DRAGON_PERCH_PHASES and not self.state.dragon_perch_announced:
            return True
        if phase == "charging_player":
            recent_ms = self._recent_ms(now, self.state.last_dragon_approach_callout_at)
            if recent_ms is None or recent_ms >= self.settings.dragon_approach_callout_cooldown_ms:
                return True
        if (
            not self.state.dragon_crystal_hint_announced
            and (event.combat.end_crystal_count or 0) > 0
        ):
            return True
        if self.state.pending_crystal_count_announce is not None:
            recent_ms = self._recent_ms(now, self.state.last_crystal_callout_at)
            if recent_ms is None or recent_ms >= self.settings.dragon_crystal_callout_cooldown_ms:
                return True
        return False

    def _has_new_dragon_visual_reveal(self, event: GameEvent) -> bool:
        return any(
            (threat.type or "").strip().lower() == "ender_dragon" and self._is_new_visual_reveal(threat)
            for threat in event.visual_threats
        )

    def _next_dragon_special_callout(self, event: GameEvent, now: datetime) -> str | None:
        if not self._is_dragon_combat_context_active(event, now):
            return None
        if self.player_input.asks_hostile_count or self.player_input.asks_dragon_direction:
            return None
        if self._has_new_dragon_visual_reveal(event):
            # 初視認の「くるで！」を先に言わせる
            return None
        phase = self._normalized_dragon_phase(event)

        # チャンスタイム: 着地系フェーズに入った瞬間だけ一回言う
        if phase in DRAGON_PERCH_PHASES and not self.state.dragon_perch_announced:
            self.state.dragon_perch_announced = True
            return response_text("boss", "ender_dragon", "chance_time")

        # 突進: ドラゴンがプレイヤーに向かってくるフェーズ
        if phase == "charging_player":
            recent_ms = self._recent_ms(now, self.state.last_dragon_approach_callout_at)
            if recent_ms is None or recent_ms >= self.settings.dragon_approach_callout_cooldown_ms:
                self.state.last_dragon_approach_callout_at = now
                return response_text("boss", "ender_dragon", "approach")

        # クリスタル助言: 初回は本数つきの促し、以降は割れるたびに残数だけ言う
        crystal_count = event.combat.end_crystal_count
        if (
            not self.state.dragon_crystal_hint_announced
            and crystal_count is not None
            and crystal_count > 0
        ):
            self.state.dragon_crystal_hint_announced = True
            self.state.pending_crystal_count_announce = None
            return response_text(
                "boss", "ender_dragon", "crystal_first_hint", count=str(crystal_count)
            )
        if self.state.pending_crystal_count_announce is not None:
            recent_ms = self._recent_ms(now, self.state.last_crystal_callout_at)
            if recent_ms is None or recent_ms >= self.settings.dragon_crystal_callout_cooldown_ms:
                remaining = self.state.pending_crystal_count_announce
                self.state.pending_crystal_count_announce = None
                self.state.last_crystal_callout_at = now
                self.state.dragon_crystal_hint_announced = True
                if remaining <= 0:
                    return response_text("boss", "ender_dragon", "crystal_clear")
                return response_text(
                    "boss", "ender_dragon", "crystal_remaining", count=str(remaining)
                )
        return None

    def _render_dragon_direction_answer(self, event: GameEvent) -> str:
        horizontal = (event.combat.dragon_horizontal or "").strip().lower() or None
        vertical = (event.combat.dragon_vertical or "").strip().lower() or None
        if horizontal is None:
            threat = next(
                (
                    candidate
                    for candidate in event.visual_threats
                    if (candidate.type or "").strip().lower() == "ender_dragon"
                ),
                None,
            )
            if threat is not None:
                if threat.direction.horizontal is not None:
                    horizontal = threat.direction.horizontal.value
                if threat.direction.vertical is not None:
                    vertical = threat.direction.vertical.value
        if horizontal is None:
            return response_text("boss", "ender_dragon", "direction_unknown")
        try:
            direction_label = DIRECTION_LABELS[HorizontalDirection(horizontal)]
        except (KeyError, ValueError):
            direction_label = "近く"
        if vertical == "above":
            return response_text("boss", "ender_dragon", "direction_above", direction=direction_label)
        return response_text("boss", "ender_dragon", "direction_answer", direction=direction_label)

    def _neutral_turned_hostile_callout(self, event: GameEvent, now: datetime) -> str | None:
        """さっきまで平和な姿で見えていた中立モブが脅威化したら警告する。

        キュー音声（固定文・LLMなし）。バリエーションは combat.json の
        neutral_turned_hostile_variants から決定的に選ぶ。
        """
        for threat in event.visual_threats:
            mob_type = (threat.type or "").strip().lower()
            if not mob_type or mob_type not in _neutral_mob_type_set():
                continue
            seen_ms = self._recent_ms(
                now, self.state.recent_passive_mob_seen_at_by_type.get(mob_type)
            )
            if seen_ms is None or seen_ms > self.settings.neutral_hostility_memory_ms:
                continue
            commented_ms = self._recent_ms(
                now,
                self.state.last_neutral_turned_hostile_comment_at_by_type.get(mob_type),
            )
            if (
                commented_ms is not None
                and commented_ms < self.settings.neutral_turned_hostile_comment_cooldown_ms
            ):
                continue
            self.state.last_neutral_turned_hostile_comment_at_by_type[mob_type] = now
            self.state.commented_visual_keys[self._visual_identity_key(threat)] = now
            self._mark_visual_priority_callout(now, single_type=None)
            lines = response_lines("combat", "calls", "neutral_turned_hostile_variants")
            line = self._select_deterministic_line(f"{event.sequence or ''}|{mob_type}", lines)
            return (
                line.replace("{player_name}", self._player_call_name(event))
                .replace("{label}", self._hostile_label(mob_type))
            )
        return None

    def _ominous_sound_kind(self, event: GameEvent) -> str | None:
        kind = (event.world.ominous_sound_kind or "").strip().lower()
        if not kind:
            return None
        recent_ms = event.world.ominous_sound_recent_ms
        if recent_ms is None or recent_ms > self.settings.ominous_sound_reset_ms:
            return None
        if not self._ominous_sound_context_allows(event, kind):
            return None
        return kind

    def _ominous_sound_context_allows(self, event: GameEvent, kind: str | None) -> bool:
        normalized = (kind or "").strip().lower()
        if not normalized:
            return False
        biome = (event.world.biome or "").strip().lower()
        if normalized in {"sculk_sensor", "sculk_shrieker"}:
            # スカルク音はディープダーク、またはウォーデンの気配がある場面で反応する
            if biome == "deep_dark":
                return True
            if self._is_warden_visual_present(event):
                return True
            return any((threat.label or "").strip().lower() == "warden" for threat in event.auditory_threats)
        # ウォーデン固有音（心音・presence・ビーム）はバイオームを問わず反応する。
        # 地上にスポーンしたウォーデンでもディープダークと同じ挙動にする。
        return True

    def _ominous_sound_severity(self, kind: str | None) -> int:
        normalized = (kind or "").strip().lower()
        if normalized == "sculk_sensor":
            return 1
        if normalized == "sculk_shrieker":
            return 2
        if normalized == "warden_heartbeat":
            return 3
        if normalized == "warden_presence":
            return 4
        if normalized == "warden_sonic_boom":
            return 5
        return 0

    def _detect_warden_sonic_boom(self, event: GameEvent) -> bool:
        kind = (event.world.ominous_sound_kind or "").strip().lower()
        if kind == "warden_sonic_boom":
            recent_ms = event.world.ominous_sound_recent_ms
            if recent_ms is not None and recent_ms <= self.settings.warden_sonic_boom_fresh_ms:
                return True
        return any(
            "sonic_boom" in (threat.sound_event or "").strip().lower()
            for threat in event.auditory_threats
        )

    def _ominous_sound_presence_active(self, now: datetime) -> bool:
        if not self.state.last_ominous_sound_kind:
            return False
        recent_ms = self._recent_ms(now, self.state.last_ominous_sound_seen_at)
        return recent_ms is not None and recent_ms < self.settings.ominous_sound_reset_ms

    def _boss_omen_kind(self, event: GameEvent) -> str | None:
        kind = (event.world.boss_omen_kind or "").strip().lower()
        if kind in {"ender_dragon_arena", "ender_dragon_summon", "wither_assembly"}:
            return kind
        return None

    def _effective_time_phase(self, event: GameEvent) -> str | None:
        if not self._is_overworld_dimension(event):
            return None
        return getattr(event.world.time_phase, "value", event.world.time_phase)

    def _effective_time_of_day(self, event: GameEvent) -> int | None:
        if not self._is_overworld_dimension(event):
            return None
        return event.world.time_of_day

    def _player_call_name(self, event: GameEvent) -> str:
        call_name = (event.meta.call_name or "").strip()
        if call_name:
            return call_name
        default_call_name = (self.settings.default_call_name or "").strip()
        if default_call_name:
            return default_call_name
        player_name = (event.player.name or "").strip()
        if player_name:
            return player_name
        return "プレイヤー"

    def _player_call_prefix(self, event: GameEvent) -> str:
        name = self._player_call_name(event)
        if not name or name == "プレイヤー":
            return ""
        return f"{name}、"

    def _enemy_presence_for_low_health_warning(self, event: GameEvent, signals: object | None = None) -> bool:
        if event.visual_threats or event.auditory_threats:
            return True
        if signals is None:
            return False
        ground_count = getattr(signals, "ground_hostile_count_within_query_range", 0) or 0
        flying_count = getattr(signals, "flying_hostile_count_within_query_range", 0) or 0
        return ground_count > 0 or flying_count > 0

    def _should_emit_low_health_warning(self, event: GameEvent, signals: object | None = None) -> bool:
        health = event.player.health
        if health is None or health > self.settings.low_health_warning_threshold:
            return False
        if not self.state.low_health_warning_armed:
            return False
        return self._enemy_presence_for_low_health_warning(event, signals)

    def _consume_low_health_warning(self, event: GameEvent, signals: object | None = None) -> str | None:
        if not self._should_emit_low_health_warning(event, signals):
            return None
        self.state.low_health_warning_armed = False
        name = (event.player.name or "").strip() or self._player_call_name(event)
        return f"{name}！体力やばいで！"

    def _ushiro_call_text(self, event: GameEvent) -> str:
        seed = "|".join(
            [
                getattr(event.observed_at, "isoformat", lambda: str(event.observed_at))(),
                str(event.sequence or ""),
                self._player_call_name(event),
                ",".join(
                    threat.entity_id or threat.type
                    for threat in event.visual_threats
                ),
            ]
        )
        return selected_ushiro_call_text(self._player_call_name(event), seed)

    def _weather_value(self, weather: object) -> str | None:
        return getattr(weather, "value", weather) if weather is not None else None

    def _has_recent_rain_sound(self, event: GameEvent) -> bool:
        recent_ms = event.world.rain_sound_recent_ms
        return recent_ms is not None and recent_ms <= self.settings.weather_sound_recent_ms

    def _has_recent_thunder_sound(self, event: GameEvent) -> bool:
        recent_ms = event.world.thunder_sound_recent_ms
        return recent_ms is not None and recent_ms <= self.settings.weather_sound_recent_ms

    def _has_recent_nearby_lightning(self, event: GameEvent) -> bool:
        recent_ms = event.world.nearby_lightning_strike_recent_ms
        distance = event.world.nearby_lightning_strike_distance
        return (
            recent_ms is not None
            and recent_ms <= self.settings.nearby_lightning_recent_ms
            and distance is not None
            and distance <= self.settings.hostile_query_distance
        )

    def _player_input_priority_active(self, now: datetime) -> bool:
        if self.player_input.breaks_silence:
            return True
        recent_ms = self._recent_ms(now, self.state.last_player_input_at)
        return recent_ms is not None and recent_ms < self.settings.player_input_priority_cooldown_ms

    def _weather_transition(self, event: GameEvent) -> tuple[str, str] | None:
        current = self._weather_value(event.world.weather)
        previous = self.state.last_weather
        if not current or not previous or current == previous:
            return None
        return previous, current

    def _has_pending_weather_transition(self) -> bool:
        return (
            self.state.pending_weather_transition_from is not None
            and self.state.pending_weather_transition_to is not None
            and self.state.pending_weather_transition_from != self.state.pending_weather_transition_to
        )

    def _is_cold_weather_biome(self, biome: str | None) -> bool:
        return self._normalized_biome(biome) in COLD_WEATHER_BIOMES

    def _normalized_biome(self, biome: str | None) -> str:
        return (biome or "").strip().lower()

    def _update_special_biome_context(self, event: GameEvent) -> None:
        if event.event.name != EventName.STATUS_SNAPSHOT:
            return
        self._update_structure_context(event)
        normalized_biome = self._normalized_biome(event.world.biome) or None
        if normalized_biome == self.state.current_biome:
            return
        self.state.current_biome = normalized_biome
        if self.state.current_structure is not None:
            # 構造物の中ではバイオーム側の入場コメントは出さない（構造物コメントと横並び分岐）
            self.state.pending_special_biome_line = None
            return
        if normalized_biome is not None:
            recent_ms = self._recent_ms(
                event.observed_at,
                self.state.last_special_biome_comment_at.get(normalized_biome),
            )
            if (
                recent_ms is not None
                and recent_ms < self.settings.special_biome_comment_cooldown_ms
            ):
                self.state.pending_special_biome_line = None
                return
        self.state.pending_special_biome_line = self._resolve_special_biome_entry_line(
            normalized_biome,
            self._effective_time_phase(event),
        )

    def _normalized_structure(self, structure: str | None) -> str:
        return (structure or "").split(":")[-1].strip().lower()

    def _update_structure_context(self, event: GameEvent) -> None:
        normalized = self._normalized_structure(event.world.structure) or None
        if normalized == self.state.current_structure:
            return
        self.state.current_structure = normalized
        if normalized is None:
            self.state.pending_structure_entry_key = None
            return
        # 構造物入場が確定したらバイオーム入場コメントは破棄する
        self.state.pending_special_biome_line = None
        if self._structure_entry(normalized) is None:
            # カタログ未登録の構造物は黙って通す
            self.state.pending_structure_entry_key = None
            return
        recent_ms = self._recent_ms(
            event.observed_at,
            self.state.last_structure_comment_at.get(normalized),
        )
        if recent_ms is not None and recent_ms < self.settings.structure_comment_cooldown_ms:
            self.state.pending_structure_entry_key = None
            return
        self.state.pending_structure_entry_key = normalized

    def _emit_pending_structure_line(self, event: GameEvent, now: datetime) -> str | None:
        structure_key = self.state.pending_structure_entry_key
        if structure_key is None:
            return None
        self.state.pending_structure_entry_key = None
        line = self._render_structure_entry_line(event, structure_key)
        if line:
            self.state.last_structure_comment_at[structure_key] = now
        return line

    def _has_recent_ender_eye_launch(self, event: GameEvent) -> bool:
        recent_ms = event.world.ender_eye_launch_recent_ms
        return recent_ms is not None and recent_ms <= self.settings.ender_eye_recent_ms

    def _has_nearby_end_portal_frame(self, event: GameEvent) -> bool:
        distance = event.world.nearby_end_portal_frame_distance
        return (
            distance is not None
            and distance <= self.settings.end_portal_frame_comment_distance
        )

    def _emit_ender_eye_throw_line(self, event: GameEvent, now: datetime) -> str | None:
        if not self._has_recent_ender_eye_launch(event):
            return None
        recent_ms = self._recent_ms(now, self.state.last_ender_eye_comment_at)
        if recent_ms is not None and recent_ms < self.settings.ender_eye_comment_cooldown_ms:
            return None
        self.state.last_ender_eye_comment_at = now
        return self._render_ender_eye_throw_line(event)

    def _resolve_special_biome_entry_line(self, biome: str | None, time_phase: object) -> str | None:
        if biome is None:
            return None
        phase_key = "night" if time_phase == "night" else "day"
        lines = special_biome_entry_lines(biome, phase_key)
        if not lines:
            return None
        return self._select_deterministic_line(f"{biome}:{phase_key}", lines)

    def _select_deterministic_line(self, seed: str, lines: tuple[str, ...]) -> str:
        if len(lines) == 1:
            return lines[0]
        return lines[sum(ord(ch) for ch in seed) % len(lines)]

    def _emit_pending_special_biome_line(self, now: datetime | None = None) -> str | None:
        line = self.state.pending_special_biome_line
        if line is None:
            return None
        if (
            now is not None
            and self.state.current_biome == "deep_dark"
            and self._ominous_sound_presence_active(now)
        ):
            line = response_lines("biome", "reactions", "deep_dark", "ominous", "lines")[0]
        self.state.pending_special_biome_line = None
        if self.state.current_biome is not None and now is not None:
            self.state.last_special_biome_comment_at[self.state.current_biome] = now
        return line

    def _is_overworld_dimension(self, event: GameEvent) -> bool:
        dimension = self._normalized_dimension(event)
        if not dimension:
            return True
        return dimension in {"overworld", "minecraft:overworld"}

    def _is_other_realm_swarm_scene(
        self,
        event: GameEvent,
        *,
        visual_count: int | None = None,
        auditory_count: int | None = None,
    ) -> bool:
        if self._is_overworld_dimension(event):
            return False
        resolved_visual_count = len(event.visual_threats) if visual_count is None else visual_count
        resolved_auditory_count = len(event.auditory_threats) if auditory_count is None else auditory_count
        if resolved_visual_count >= self.settings.other_realm_swarm_visual_threshold:
            return True
        return (
            resolved_visual_count > 0
            and resolved_auditory_count >= self.settings.other_realm_audio_generic_threshold
        )

    def _should_genericize_other_realm_auditory_presence(
        self,
        event: GameEvent,
        auditory_count: int,
    ) -> bool:
        if self._is_overworld_dimension(event):
            return False
        if len(event.visual_threats) >= self.settings.other_realm_swarm_visual_threshold:
            return True
        return auditory_count >= self.settings.other_realm_audio_generic_threshold

    def _normalized_dimension(self, event: GameEvent) -> str:
        return (event.player.dimension or "").strip().lower()

    def _should_emit_ambient_mob_comment(self, event: GameEvent, now: datetime) -> bool:
        if not event.passive_mobs:
            return False
        if self._player_input_priority_active(now):
            return False
        if event.visual_threats or event.auditory_threats:
            return False
        if event.event.name not in {EventName.AMBIENT_MOB_DETECTED, EventName.STATUS_SNAPSHOT}:
            return False
        # クールダウンは種ごとに管理する。別の種が見えたならすぐ反応してよい
        if self._next_ambient_mob_target(event.passive_mobs, now) is None:
            return False
        if event.event.name == EventName.AMBIENT_MOB_DETECTED:
            return True
        if self.state.mode != "normal":
            return False
        if event.combat.combat_active_hint:
            return False
        recent_visual_ms = self._recent_ms(now, self.state.last_visual_threat_at)
        if recent_visual_ms is not None and recent_visual_ms < self.settings.hostile_comment_cooldown_ms:
            return False
        recent_audio_ms = self._recent_ms(now, self.state.last_audio_threat_at)
        if recent_audio_ms is not None and recent_audio_ms < self.settings.hostile_comment_cooldown_ms:
            return False
        recent_damage_ms = self._recent_ms(now, self.state.last_damage_at)
        if recent_damage_ms is not None and recent_damage_ms < self.settings.hostile_comment_cooldown_ms:
            return False
        return True

    def _recent_dimension_warp(self, now: datetime) -> bool:
        recent_ms = self._recent_ms(now, self.state.last_dimension_change_at)
        return (
            recent_ms is not None
            and recent_ms < self.settings.mass_callout_warp_window_ms
        )

    def _did_change_dimension(self, event: GameEvent) -> bool:
        current_dimension = self._normalized_dimension(event) or None
        previous_dimension = self.state.current_dimension
        return (
            previous_dimension is not None
            and current_dimension is not None
            and current_dimension != previous_dimension
        )

    def _is_cave_biome(self, biome: str | None) -> bool:
        normalized = self._normalized_biome(biome)
        return normalized == "deep_dark" or normalized.endswith("_caves")

    def _is_night_warning_suppressed_biome(self, biome: str | None) -> bool:
        normalized = self._normalized_biome(biome)
        return normalized in NIGHT_WARNING_SUPPRESSED_BIOMES

    def _is_flying_hostile_type(self, hostile_type: str) -> bool:
        return hostile_type in FLYING_HOSTILES

    def _visible_ground_hostiles_within_query_range(self, event: GameEvent) -> list[VisualThreat]:
        max_distance = self.settings.hostile_query_distance
        return [
            threat
            for threat in event.visual_threats
            if not self._is_flying_hostile_type(threat.type)
            and threat.distance is not None
            and threat.distance <= max_distance
        ]

    def _visible_flying_hostiles_within_query_range(self, event: GameEvent) -> list[VisualThreat]:
        max_distance = self.settings.hostile_query_distance
        return [
            threat
            for threat in event.visual_threats
            if self._is_flying_hostile_type(threat.type)
            and threat.distance is not None
            and threat.distance <= max_distance
        ]

    def _ground_hostile_count_within_query_range(self, event: GameEvent) -> int:
        counted = event.combat.hostiles_within_30_ground
        if counted is not None:
            return counted
        return len(self._visible_ground_hostiles_within_query_range(event))

    def _flying_hostile_count_within_query_range(self, event: GameEvent) -> int:
        return len(self._visible_flying_hostiles_within_query_range(event))

    def _current_close_flying_visual_keys(self, event: GameEvent) -> set[str]:
        return {
            self._visual_identity_key(threat)
            for threat in self._visible_flying_hostiles_within_query_range(event)
        }

    def _entered_close_flying_visual(self, event: GameEvent) -> VisualThreat | None:
        candidates = [
            threat
            for threat in self._visible_flying_hostiles_within_query_range(event)
            if self._visual_identity_key(threat) not in self.state.active_close_flying_visual_keys
            # ドラゴンは専用コールアウト（reveal・突進・着地）に任せる
            and (threat.type or "").strip().lower() != "ender_dragon"
        ]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda threat: (
                0 if getattr(threat.direction.vertical, "value", threat.direction.vertical) == "above" else 1,
                threat.distance if threat.distance is not None else float("inf"),
            ),
        )

    def _render_hostile_query_line(self, event: GameEvent, count: int) -> str:
        if count <= 0:
            return "30マス以内には今はおらんかな。"
        if count == 1:
            return "30マス以内には今は1体おるで。"
        return f"30マス以内には今は{count}体おるで。"

    def _emit_pending_overworld_return_line(self, now: datetime) -> str | None:
        if not self.state.pending_overworld_return_line:
            return None
        ready_at = self.state.pending_overworld_return_ready_at
        if ready_at is not None and now < ready_at:
            return None
        self.state.pending_overworld_return_line = False
        self.state.pending_overworld_return_ready_at = None
        return "オーバーワールドは落ち着くな・・・"

    def _hostile_massive_callout(self, event: GameEvent, *, suppressed: bool) -> str:
        if suppressed:
            return response_text("combat", "pressure", "hostile_massive_suppressed")
        lines = response_lines("combat", "pressure", "hostile_massive_variants")
        seed = "|".join(
            [
                str(event.sequence or ""),
                getattr(event.observed_at, "isoformat", lambda: str(event.observed_at))(),
                self._normalized_dimension(event),
                self._normalized_biome(event.world.biome),
            ]
        )
        return self._select_deterministic_line(seed, lines)

    def _is_rest_time(self, event: GameEvent) -> bool:
        time_phase = self._effective_time_phase(event)
        return time_phase in {"evening", "night"}

    def _is_near_respawn_bed(self, event: GameEvent) -> bool:
        if not self._respawn_point_set(event):
            return False
        respawn_distance = event.world.respawn_distance
        return respawn_distance is not None and respawn_distance <= self.settings.home_bed_prompt_distance

    def _has_nearby_sleepable_bed(self, event: GameEvent) -> bool:
        return (event.world.nearby_bed_count or 0) > 0

    def _should_emit_sleep_prompt(self, event: GameEvent, now: datetime) -> bool:
        return False

    def _emit_sleep_prompt(self, event: GameEvent, now: datetime) -> str | None:
        return None

    def _should_emit_sleeping_neighbor_comment(self, event: GameEvent, now: datetime) -> bool:
        return False

    def _render_sleeping_neighbor_line(self, event: GameEvent, now: datetime) -> str | None:
        return None

    def _is_surface_evening_warning_context(self, event: GameEvent) -> bool:
        time_phase = self._effective_time_phase(event)
        if time_phase != "evening":
            return False
        if self._is_night_warning_suppressed_biome(event.world.biome):
            return False
        if self._weather_value(event.world.weather) == "thunder":
            return False
        if not bool(event.world.sky_visible):
            return False
        if bool(event.world.is_submerged):
            return False
        if self._is_cave_biome(event.world.biome):
            return False
        if self._is_safe_zone_with_door_event(event):
            return False
        return True

    def _is_cave_or_submerged_night_warning_context(self, event: GameEvent) -> bool:
        if not self._is_overworld_dimension(event):
            return False
        if self._is_night_warning_suppressed_biome(event.world.biome):
            return False
        if bool(event.world.is_submerged):
            return True
        return self._is_cave_biome(event.world.biome)

    def _should_schedule_night_warning(self, event: GameEvent) -> bool:
        if self._boss_presence_active(event.observed_at):
            return False
        if self._ominous_sound_presence_active(event.observed_at):
            return False
        time_phase = self._effective_time_phase(event)
        if time_phase == "evening":
            return (
                self._is_surface_evening_warning_context(event)
                or self._is_cave_or_submerged_night_warning_context(event)
            )
        if time_phase == "night":
            return self._is_cave_or_submerged_night_warning_context(event)
        return False

    def _should_consider_night_warning(self, event: GameEvent) -> bool:
        if self.state.night_warning_emitted_this_cycle:
            return False
        if self._boss_presence_active(event.observed_at):
            return False
        if self._ominous_sound_presence_active(event.observed_at):
            return False
        return self.state.night_warning_pending or self._should_schedule_night_warning(event)

    def _render_night_warning_line(self, event: GameEvent) -> str | None:
        if self.player_input.should_block_ambient:
            return None
        if self._is_surface_evening_warning_context(event):
            return EVENING_SURFACE_WARNING_CALL
        if not self._is_cave_or_submerged_night_warning_context(event):
            return None
        time_phase = self._effective_time_phase(event)
        if time_phase == "evening":
            phase_label = "夕方"
        elif time_phase == "night":
            phase_label = "夜"
        else:
            return None
        return response_text("darkness", "night_warning", "cave_or_submerged", phase_label=phase_label)

