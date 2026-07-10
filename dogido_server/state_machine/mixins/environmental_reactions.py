# state_machine/mixins/environmental_reactions.py
from __future__ import annotations

from datetime import datetime, timedelta

from dogido_server.models import EventName, GameEvent
from dogido_server.state_machine.constants import *  # noqa: F403
from dogido_server.state_machine.fallback_catalog import fallback_text
from dogido_server.state_machine.response_catalog import response_text
from dogido_server.state_machine.types import AudioAction, DerivedSignals


class EnvironmentalReactionsMixin:
    def _speech_action(self, text: str, *, protect_ms: int = 0) -> AudioAction:
        return AudioAction(
            layer="speech",
            interrupt=False,
            text=text,
            protect_ms=protect_ms,
        )

    def _control_interrupt_action(self) -> AudioAction:
        return AudioAction(layer="control", interrupt=True)

    def _speech_actions(self, text: str | None, *, protect_ms: int = 0) -> list[AudioAction]:
        if not text:
            return []
        return [self._speech_action(text, protect_ms=protect_ms)]

    def _darkness_advice_on_cooldown(self, now: datetime) -> bool:
        if self.state.last_darkness_advice_at is None:
            return False
        recent_ms = self._recent_ms(now, self.state.last_darkness_advice_at)
        return (
            recent_ms is not None
            and recent_ms < self.settings.darkness_advice_cooldown_ms
        )

    def _darkness_advice(self, event: GameEvent, signals: DerivedSignals) -> str | None:
        now = event.observed_at
        if signals.submerged:
            return self._render_submerged_darkness_line(event)
        if self._darkness_advice_on_cooldown(now):
            return None
        if signals.emergency_shelter:
            return None
        if self._is_cramped_dark_burrow_event(event):
            return None
        if self._is_nearby_light_source_buffered_event(event):
            return None
        if self._is_lit_interior_safe_pocket_event(event):
            return None
        if self._is_safe_zone_with_door_event(event):
            return None
        if self._is_foliage_shade_context(event):
            return None
        if signals.danger_darkness_score < self.settings.darkness_alert_threshold:
            return None
        local_light = event.world.local_light
        if local_light is not None and local_light > self.settings.darkness_advice_light_threshold:
            return None

        if signals.torch_available:
            self.state.last_darkness_advice_at = now
            return "なあ、ここ急に暗なってきたやん。松明つけとこ。"
        if signals.torch_craftable:
            self.state.last_darkness_advice_at = now
            return "石炭あるやん、今のうちに松明作っとこや。"
        if signals.torch_materials_nearby:
            self.state.last_darkness_advice_at = now
            return "このへんで木とか石炭拾って、先に松明作っとこ。"
        if not self._has_weapon(event):
            return self._render_darkness_escape_line(event)
        if self._effective_time_phase(event) in {"evening", "night"}:
            self.state.last_darkness_advice_at = now
            return "これはもうあかん、こんなんいえに帰ったほうがええって。"
        self.state.last_darkness_advice_at = now
        return "なんかこの先、普通に危ない空気してるで。"

    def _should_emit_emergency_shelter_advice(self, event: GameEvent, signals: DerivedSignals) -> bool:
        if event.event.name != EventName.STATUS_SNAPSHOT:
            return False
        if not self._is_overworld_dimension(event):
            return False
        if self._is_cave_biome(event.world.biome):
            return False
        if self.state.emergency_shelter_advised_this_cycle:
            return False
        if signals.submerged or signals.safe_zone_with_door or signals.emergency_shelter:
            return False
        if self._is_nearby_light_source_buffered_event(event):
            return False
        if self._is_lit_interior_safe_pocket_event(event):
            return False
        if self._is_tree_canopy_cover_event(event) or self._is_foliage_shade_context(event):
            return False
        local_light = event.world.local_light
        if local_light is not None and local_light > self.settings.darkness_advice_light_threshold:
            return False
        if not self._has_surface_hostile_spawn_started(event):
            return False
        if self._normalized_biome(event.world.biome) in SURFACE_HOSTILE_SAFE_BIOMES:
            return False
        return signals.home_or_respawn_return_is_unrealistic

    def _emit_emergency_shelter_advice(self, event: GameEvent, signals: DerivedSignals) -> str | None:
        if not self._should_emit_emergency_shelter_advice(event, signals):
            return None
        self.state.emergency_shelter_advised_this_cycle = True
        self.state.emergency_shelter_morning_announced = False
        return EMERGENCY_SHELTER_CALL

    def _should_emit_emergency_shelter_morning_call(self, event: GameEvent, signals: DerivedSignals) -> bool:
        if event.event.name != EventName.STATUS_SNAPSHOT:
            return False
        if not (
            self.state.emergency_shelter_advised_this_cycle
            or self.state.emergency_shelter_seen_this_cycle
        ):
            return False
        if self.state.emergency_shelter_morning_announced:
            return False
        if not signals.emergency_shelter:
            return False
        return self._is_emergency_shelter_morning(event)

    def _emit_emergency_shelter_morning_call(self, event: GameEvent, signals: DerivedSignals) -> str | None:
        if not self._should_emit_emergency_shelter_morning_call(event, signals):
            return None
        self.state.emergency_shelter_morning_announced = True
        return EMERGENCY_SHELTER_MORNING_CALL

    def _firefly_actions(
        self,
        event: GameEvent,
        signals: DerivedSignals,
        now: datetime,
    ) -> list[AudioAction]:
        time_phase = self._effective_time_phase(event)
        if time_phase != "night":
            return []
        if self.state.firefly_reacted_this_night:
            return []
        if (event.world.nearby_firefly_bush_count or 0) <= 0:
            return []
        if signals.submerged or signals.safe_zone_with_door:
            return []
        if event.visual_threats or event.auditory_threats:
            return []
        self.state.firefly_reacted_this_night = True
        cue = self._build_cue_action("suppressed_gasp", "ヒイ！", now, interrupt=False)
        return [
            cue,
            AudioAction(
                layer="speech",
                interrupt=False,
                text="なんや。ほたるかいな……驚いて損したわ……。",
            ),
        ]

    def _foliage_shade_callout(
        self,
        event: GameEvent,
        signals: DerivedSignals,
        now: datetime,
    ) -> str | None:
        if self.state.current_structure is not None:
            return None
        if not self._is_foliage_shade_context(event):
            return None
        if not self._entered_foliage_shade_context(event):
            return None
        if signals.safe_zone_with_door or signals.submerged:
            return None
        if (
            self.state.last_foliage_darkness_advice_at is not None
            and self._recent_ms(now, self.state.last_foliage_darkness_advice_at) is not None
            and self._recent_ms(now, self.state.last_foliage_darkness_advice_at)
            < self.settings.foliage_darkness_comment_cooldown_ms
        ):
            return None
        self.state.last_foliage_darkness_advice_at = now
        return "木がしげっているとこは暗いわー。こういうとこはおひさんでとっても敵が残っとるんやで……。"

    def _environmental_actions(
        self,
        event: GameEvent,
        signals: DerivedSignals,
        previous_mode: str,
        now: datetime,
    ) -> list[AudioAction]:
        # ノーマルモード中でもウォーデンのビームには即座に悲鳴を上げる
        sonic_boom_cue = self._warden_sonic_boom_scream_cue(event, now)
        if sonic_boom_cue is not None:
            return [sonic_boom_cue]

        # 話しかけは暗所ループや環境反応より先に返す（取りこぼし防止）
        if self.player_input.asks_hostile_count:
            return self._speech_actions(
                self._render_hostile_query_line(event, signals.ground_hostile_count_within_query_range)
            )
        if self.player_input.asks_dragon_direction:
            return self._speech_actions(self._render_dragon_direction_answer(event))
        if self._has_pending_player_chat(event):
            return self._speech_actions(self._render_player_chat_reply(event))

        stop_dark_push = self._should_stop_dark_push_audio(event, signals)
        blocked = self._blocked_environmental_actions(event, signals, now, stop_dark_push)
        if blocked is not None:
            return blocked

        high_priority = self._high_priority_environmental_actions(event, signals, now, stop_dark_push)
        if high_priority:
            return high_priority

        return self._ambient_environmental_actions(event, signals, previous_mode, now, stop_dark_push)

    def _blocked_environmental_actions(
        self,
        event: GameEvent,
        signals: DerivedSignals,
        now: datetime,
        stop_dark_push: bool,
    ) -> list[AudioAction] | None:
        if not self._should_block_environmental_actions_for_threats(event, signals):
            return None
        if not stop_dark_push:
            return []
        return self._handle_dark_push_stop(event, signals, now, defer_speech=True)

    def _high_priority_environmental_actions(
        self,
        event: GameEvent,
        signals: DerivedSignals,
        now: datetime,
        stop_dark_push: bool,
    ) -> list[AudioAction]:
        actions = self._emergency_shelter_morning_actions(event, signals, stop_dark_push)
        if actions:
            return actions

        actions = self._emergency_shelter_entry_actions(event, signals, stop_dark_push)
        if actions:
            return actions

        actions = self._light_source_crafted_actions(event, signals, stop_dark_push)
        if actions:
            return actions

        actions = self._mining_fatigue_warning_actions(event, signals)
        if actions:
            return actions

        actions = self._boss_omen_actions(event)
        if actions:
            return actions

        actions = self._ominous_sound_priority_actions(event, now)
        if actions:
            return actions

        actions = self._submerged_dark_entry_actions(event, signals, stop_dark_push)
        if actions:
            return actions

        actions = self._occluded_dark_entry_actions(event, signals, now)
        if actions:
            return actions

        actions = self._dark_push_warning_actions(event, signals, now)
        if actions:
            return actions

        if stop_dark_push or self._should_stop_dark_push_stage_one(event, signals):
            actions = self._handle_dark_push_stop(event, signals, now, defer_speech=False)
            actions.extend(self._explicit_ambient_mob_followup_actions(event, now))
            return actions

        pending_after_breath = self._emit_pending_dark_push_after_breath(event, signals, now)
        if pending_after_breath:
            return pending_after_breath

        if self._should_continue_dark_push_breath(event, signals, now):
            self.state.last_dark_push_breath_at = now
            return [
                AudioAction(
                    layer="panic_cue",
                    interrupt=False,
                    cue_id="suppressed_breath",
                    text="ハァハァ……",
                )
            ]
        return []

    def _explicit_ambient_mob_followup_actions(
        self,
        event: GameEvent,
        now: datetime,
    ) -> list[AudioAction]:
        if self._player_input_priority_active(now, purpose="ambient"):
            return []
        if event.event.name != EventName.AMBIENT_MOB_DETECTED:
            return []
        if not event.passive_mobs or event.visual_threats or event.auditory_threats:
            return []
        line = self._emit_ambient_mob_comment_line(event, now)
        if not line:
            return []
        return [self._speech_action(line)]

    def _night_warning_actions(self, event: GameEvent, now: datetime) -> list[AudioAction]:
        if self.state.pending_night_warning_detail:
            if (
                self._boss_presence_active(now)
                or self._ominous_sound_presence_active(now)
                or not self._is_surface_evening_warning_context(event)
            ):
                self.state.pending_night_warning_detail = False
                return []
            self.state.pending_night_warning_detail = False
            self.state.night_warning_pending = False
            self.state.night_warning_emitted_this_cycle = True
            return self._speech_actions(
                response_text("darkness", "night_warning", "surface_evening")
            )
        if not self._should_consider_night_warning(event):
            return []
        if (
            self._player_input_priority_active(now)
            and self._is_surface_evening_warning_context(event)
        ):
            # 夕方警告は時限性が高いので、プレイヤーの話を遮って
            # 注意喚起 -> 次イベントで本文 の2段階で出す
            self.state.pending_night_warning_detail = True
            return [
                AudioAction(
                    layer="speech",
                    interrupt=True,
                    text=response_text("darkness", "night_warning", "surface_evening_attention"),
                )
            ]
        line = self._render_night_warning_line(event)
        if line is None:
            return []
        self.state.night_warning_pending = False
        self.state.night_warning_emitted_this_cycle = True
        return self._speech_actions(line)

    def _ambient_environmental_actions(
        self,
        event: GameEvent,
        signals: DerivedSignals,
        previous_mode: str,
        now: datetime,
        stop_dark_push: bool,
    ) -> list[AudioAction]:
        if self.player_input.asks_hostile_count:
            return self._speech_actions(
                self._render_hostile_query_line(event, signals.ground_hostile_count_within_query_range)
            )
        if self.player_input.asks_dragon_direction:
            return self._speech_actions(self._render_dragon_direction_answer(event))
        # 夜警告は入力優先クールダウンをバイパスする（時限性のため）
        night_warning_actions = self._night_warning_actions(event, now)
        if night_warning_actions:
            return night_warning_actions

        # ドラゴン戦の特殊コールアウト（突進・着地・クリスタル残数）も時限性が高いので
        # 入力優先ミュートをバイパスする。視覚脅威の30マス圏外でも出せるよう normal フローに置く
        dragon_special = self._next_dragon_special_callout(event, now)
        if dragon_special:
            return self._speech_actions(dragon_special)

        # キーワードに一致しない話しかけには会話として返事する
        # （入力優先ミュートはこの後の自発発話を黙らせるためのものなので、返事自体は通す）
        if self._has_pending_player_chat(event):
            return self._speech_actions(self._render_player_chat_reply(event))

        if self._player_input_priority_active(now):
            return []

        # 発句済みの川柳は最優先で本句を完了させる（次のスナップショットで出す）
        if self.state.pending_haiku_after_preface:
            haiku_completion = self._emit_haiku_line(event, now)
            if haiku_completion:
                return self._speech_actions(haiku_completion)

        overworld_return_line = self._emit_pending_overworld_return_line(now)
        if overworld_return_line:
            return self._speech_actions(overworld_return_line)

        lightning_actions = self._emit_nearby_lightning_strike_actions(event, now)
        if lightning_actions:
            return lightning_actions

        weather_transition = self._weather_transition_callout(event, signals)
        if weather_transition:
            return self._speech_actions(weather_transition)

        # エンダーアイ投擲はプレイヤー自身の行動への相槌なので早めに返す
        ender_eye_line = self._emit_ender_eye_throw_line(event, now)
        if ender_eye_line:
            return self._speech_actions(ender_eye_line)

        portal_line = self._emit_portal_appearance_line(event, now)
        if portal_line:
            return self._speech_actions(portal_line)

        if self._boss_presence_active(now):
            self.state.night_warning_pending = False
            self.state.pending_night_warning_detail = False
            self.state.pending_special_biome_line = None
            self.state.pending_structure_entry_key = None
            return []

        if self._ominous_sound_presence_active(now):
            self.state.night_warning_pending = False
            self.state.pending_night_warning_detail = False
            if self.state.current_biome != "deep_dark":
                self.state.pending_special_biome_line = None
                self.state.pending_structure_entry_key = None

        firefly_actions = self._firefly_actions(event, signals, now)
        if firefly_actions:
            return firefly_actions

        ominous_sound_line = self._emit_ominous_sound_line(event, now)
        if ominous_sound_line:
            return self._speech_actions(ominous_sound_line)

        magma_block_line = self._emit_magma_block_comment(event, now)
        if magma_block_line:
            return self._speech_actions(magma_block_line)

        damaging_light_line = self._emit_damaging_light_warning(event, now)
        if damaging_light_line:
            return self._speech_actions(damaging_light_line)

        shelter_actions = self._emergency_shelter_presence_actions(event, signals, stop_dark_push)
        if shelter_actions:
            return shelter_actions

        emergency_shelter_advice = self._emit_emergency_shelter_advice(event, signals)
        if emergency_shelter_advice is not None:
            self._log_darkness_decision("emergency_shelter_advice", event, signals)
            return self._speech_actions(emergency_shelter_advice)

        foliage_darkness = self._foliage_shade_callout(event, signals, now)
        if foliage_darkness:
            self.state.pending_special_biome_line = None
            self._log_darkness_decision("foliage_shade", event, signals)
            return self._speech_actions(foliage_darkness)

        if self._ominous_sound_presence_active(now) and not (
            self.state.current_biome == "deep_dark"
            and (
                self.state.pending_special_biome_line is not None
                or self.state.pending_structure_entry_key is not None
            )
        ):
            return []

        # 構造物入場コメントはバイオーム入場コメントより優先
        structure_line = self._emit_pending_structure_line(event, now)
        if structure_line:
            return self._speech_actions(structure_line)

        portal_frame_line = self._emit_end_portal_frame_line(event, now)
        if portal_frame_line:
            return self._speech_actions(portal_frame_line)

        special_biome_line = self._emit_pending_special_biome_line(now)
        if special_biome_line:
            return self._speech_actions(special_biome_line)

        if previous_mode != "alert" or event.event.name in {
            EventName.DANGER_DARKNESS_CHANGED,
            EventName.TIME_PHASE_CHANGED,
        }:
            darkness_advice = self._darkness_advice(event, signals)
            if darkness_advice:
                self._log_darkness_decision("darkness_advice", event, signals)
                return self._speech_actions(darkness_advice)

        # ポータルが近い場合も専用の近道は使わない。通常の川柳フロー
        # （「ここで一句。」発句 → 情景・持ち物込みの本句）に一本化し、
        # ポータルは題材候補（_haiku_feature_candidates）として混ざる
        haiku_line = self._emit_haiku_line(event, now)
        return self._speech_actions(haiku_line)

    def _emit_nearby_lightning_strike_actions(
        self,
        event: GameEvent,
        now: datetime,
    ) -> list[AudioAction]:
        if not self._has_recent_nearby_lightning(event):
            return []
        recent_ms = self._recent_ms(now, self.state.last_nearby_lightning_comment_at)
        if recent_ms is not None and recent_ms < self.settings.nearby_lightning_comment_cooldown_ms:
            return []
        self.state.last_nearby_lightning_comment_at = now
        return [
            self._build_cue_action("spot_hostile_gasp", "ひいっ！", now, interrupt=False),
            self._speech_action(fallback_text("general", "weather_transition", "nearby_lightning_strike")),
        ]

    def _emit_damaging_light_warning(self, event: GameEvent, now: datetime) -> str | None:
        if not self._should_consider_damaging_light_warning(event, now):
            return None
        self.state.last_damaging_light_warning_at = now
        return "触るとあちちやで！"

    def _emit_magma_block_comment(self, event: GameEvent, now: datetime) -> str | None:
        if not self._should_consider_magma_block_comment(event, now):
            return None
        self.state.last_magma_block_comment_at = now
        return "………しゃがめば大丈夫なんが不思議やな……"

    def _emit_ominous_sound_line(self, event: GameEvent, now: datetime) -> str | None:
        if self._boss_presence_active(now):
            return None
        if event.event.name == EventName.HOSTILE_AUDIO_DETECTED:
            return None
        kind = self._ominous_sound_kind(event)
        if kind is None:
            return None
        recent_ms = self._recent_ms(now, self.state.last_ominous_sound_comment_at)
        cooldown_ms = self._ominous_sound_comment_cooldown_ms(kind)
        if recent_ms is not None and recent_ms < cooldown_ms:
            return None
        severity = self._ominous_sound_severity(kind)
        stage = 1
        if self.state.ominous_sound_stage >= 1 and severity >= max(2, self.state.last_ominous_sound_severity):
            stage = 2
        self.state.last_ominous_sound_comment_at = now
        self.state.ominous_sound_stage = max(self.state.ominous_sound_stage, stage)
        return self._render_deep_dark_ominous_sound_line(event, kind, stage)

    def _ominous_sound_comment_cooldown_ms(self, kind: str) -> int:
        if kind in {"sculk_sensor", "sculk_shrieker"}:
            return self.settings.sculk_ominous_sound_comment_cooldown_ms
        return self.settings.ominous_sound_comment_cooldown_ms

    def _ominous_sound_priority_actions(self, event: GameEvent, now: datetime) -> list[AudioAction]:
        line = self._emit_ominous_sound_line(event, now)
        if not line:
            return []
        return self._speech_actions(line)

    def _should_consider_damaging_light_warning(self, event: GameEvent, now: datetime) -> bool:
        count = event.world.nearby_damaging_light_source_count or 0
        nearest = event.world.nearest_damaging_light_source_distance
        if count <= 0 or nearest is None:
            return False
        if nearest > self.settings.damaging_light_warning_max_distance:
            return False
        if event.world.standing_on_magma_block:
            return False
        recent_ms = self._recent_ms(now, self.state.last_damaging_light_warning_at)
        if recent_ms is not None and recent_ms < self.settings.damaging_light_warning_cooldown_ms:
            return False
        return True

    def _should_consider_magma_block_comment(self, event: GameEvent, now: datetime) -> bool:
        if not event.world.standing_on_magma_block:
            return False
        recent_ms = self._recent_ms(now, self.state.last_magma_block_comment_at)
        if recent_ms is not None and recent_ms < self.settings.magma_block_comment_cooldown_ms:
            return False
        return True

    def _mining_fatigue_warning_actions(
        self,
        event: GameEvent,
        signals: DerivedSignals,
    ) -> list[AudioAction]:
        if not signals.entered_mining_fatigue:
            return []
        recent_ms = self._recent_ms(event.observed_at, self.state.last_mining_fatigue_comment_at)
        if recent_ms is not None and recent_ms < self.settings.mining_fatigue_comment_cooldown_ms:
            return []
        self.state.last_mining_fatigue_comment_at = event.observed_at
        return self._speech_actions(response_text("boss", "elder_guardian", "mining_fatigue"))

    def _boss_omen_actions(self, event: GameEvent) -> list[AudioAction]:
        kind = self._boss_omen_kind(event)
        if kind is None:
            return []
        recent_ms = self._recent_ms(event.observed_at, self.state.last_boss_omen_comment_at)
        if (
            kind == self.state.last_boss_omen_kind
            and recent_ms is not None
            and recent_ms < self.settings.boss_omen_comment_cooldown_ms
        ):
            return []
        self.state.last_boss_omen_kind = kind
        self.state.last_boss_omen_comment_at = event.observed_at
        if kind == "ender_dragon_arena":
            return self._speech_actions(response_text("boss", "ender_dragon", "arena_hint"))
        if kind == "ender_dragon_summon":
            return self._speech_actions(response_text("boss", "ender_dragon", "summon_commit"))
        if kind == "wither_assembly":
            return self._speech_actions(response_text("boss", "wither", "assembly_hint"))
        return []

    def _emergency_shelter_morning_actions(
        self,
        event: GameEvent,
        signals: DerivedSignals,
        stop_dark_push: bool,
    ) -> list[AudioAction]:
        emergency_shelter_morning = self._emit_emergency_shelter_morning_call(event, signals)
        if emergency_shelter_morning is None:
            return []
        actions: list[AudioAction] = []
        if stop_dark_push or self._should_stop_dark_push_stage_one(event, signals):
            self._reset_dark_push_state()
            actions.append(self._control_interrupt_action())
        actions.extend(self._speech_actions(emergency_shelter_morning))
        return actions

    def _light_source_crafted_actions(
        self,
        event: GameEvent,
        signals: DerivedSignals,
        stop_dark_push: bool,
    ) -> list[AudioAction]:
        if not signals.light_source_crafted:
            return []
        self._reset_dark_push_state()
        actions: list[AudioAction] = []
        if stop_dark_push:
            actions.append(self._control_interrupt_action())
        actions.extend(self._speech_actions(self._render_light_crafted_line(event)))
        return actions

    def _emergency_shelter_entry_actions(
        self,
        event: GameEvent,
        signals: DerivedSignals,
        stop_dark_push: bool,
    ) -> list[AudioAction]:
        if not signals.entered_emergency_shelter:
            return []
        should_interrupt = stop_dark_push or self._should_stop_dark_push_stage_one(event, signals)
        self._reset_dark_push_state()
        self._log_darkness_decision("emergency_shelter_entry", event, signals)
        actions: list[AudioAction] = []
        if should_interrupt:
            actions.append(self._control_interrupt_action())
        actions.extend(self._speech_actions(self._render_emergency_shelter_relief_line(event)))
        return actions

    def _submerged_dark_entry_actions(
        self,
        event: GameEvent,
        signals: DerivedSignals,
        stop_dark_push: bool,
    ) -> list[AudioAction]:
        if not signals.entered_submerged_dark_zone:
            return []
        self._reset_dark_push_state()
        line = self._render_submerged_darkness_line(event)
        if line is None:
            return []
        self._log_darkness_decision("submerged_dark_entry", event, signals)
        actions: list[AudioAction] = []
        if stop_dark_push:
            actions.append(self._control_interrupt_action())
        actions.extend(self._speech_actions(line))
        return actions

    def _occluded_dark_entry_actions(
        self,
        event: GameEvent,
        signals: DerivedSignals,
        now: datetime,
    ) -> list[AudioAction]:
        if not signals.entered_occluded_dark_zone:
            return []
        self._reset_dark_push_state()
        self._set_dark_push_entry_reference(event)
        if not signals.torch_available and self._is_immediately_severe_dark_push_entry(event):
            line = self._render_dark_push_no_light_line(event)
            if line:
                self.state.last_dark_push_comment_at = now
                self.state.last_dark_push_breath_at = None
                self.state.dark_push_breath_ready_at = now + timedelta(
                    milliseconds=self.settings.dark_push_breath_delay_ms
                )
                self.state.dark_push_active = True
                self.state.dark_push_stage = 2
                self._log_darkness_decision("dark_push_immediate_entry", event, signals)
                return self._speech_actions(line)
        self.state.dark_push_stage = 1
        line = self._render_occluded_entry_line(event, signals)
        self._log_darkness_decision("occluded_entry", event, signals)
        return self._speech_actions(line)

    def _dark_push_warning_actions(
        self,
        event: GameEvent,
        signals: DerivedSignals,
        now: datetime,
    ) -> list[AudioAction]:
        if not self._should_warn_dark_push_no_light(event, signals, now):
            return []
        self.state.pending_dark_push_after_breath_until = None
        line = self._render_dark_push_no_light_line(event)
        if not line:
            return []
        self.state.last_dark_push_comment_at = now
        self.state.last_dark_push_breath_at = None
        self.state.dark_push_breath_ready_at = now + timedelta(
            milliseconds=self.settings.dark_push_breath_delay_ms
        )
        self.state.dark_push_active = True
        self.state.dark_push_stage = 2
        self._log_darkness_decision("dark_push", event, signals)
        return self._speech_actions(line)

    def _emit_end_portal_frame_line(self, event: GameEvent, now: datetime) -> str | None:
        # 設置済みのエンドポータルフレームなら要塞外（手置き）でも反応する
        if not self._has_nearby_end_portal_frame(event):
            return None
        recent_ms = self._recent_ms(now, self.state.last_portal_frame_comment_at)
        if recent_ms is not None and recent_ms < self.settings.portal_frame_comment_cooldown_ms:
            return None
        self.state.last_portal_frame_comment_at = now
        return response_text("exploration", "portal", "frame_nearby")

    def _emit_portal_appearance_line(self, event: GameEvent, now: datetime) -> str | None:
        portal_type = self.state.pending_portal_type
        if portal_type is None:
            return None
        self.state.pending_portal_type = None
        self.state.reacted_portal_types.add(portal_type)
        return self._render_portal_appearance_line(event, portal_type)

    def _emergency_shelter_presence_actions(
        self,
        event: GameEvent,
        signals: DerivedSignals,
        stop_dark_push: bool,
    ) -> list[AudioAction]:
        if not signals.emergency_shelter:
            return []
        if not (stop_dark_push or self._should_stop_dark_push_stage_one(event, signals)):
            return []
        self._reset_dark_push_state()
        self._log_darkness_decision("dark_push_stop_emergency_shelter", event, signals)
        return [self._control_interrupt_action()]
