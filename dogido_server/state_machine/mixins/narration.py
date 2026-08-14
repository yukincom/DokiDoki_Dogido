# state_machine/mixins/narration.py
from __future__ import annotations

import logging
from datetime import datetime

from dogido_server.entry_catalog import mob_entry, mob_poetic_tags, resolve_mob_catalog_entry
from dogido_server.models import GameEvent, PassiveMob
from dogido_server.player_activity import player_vehicle_fact
from dogido_server.state_machine.ambient_mob_catalog import (
    AmbientMobReactionContext,
    ambient_mob_fallback_candidates,
)
from dogido_server.state_machine.villager_schedule import (
    project_villager_speech_facts,
    resolve_villager_schedule,
    should_suppress_ambient_for_sleep,
)
from dogido_server.state_machine.fallback_catalog import dark_push_after_breath_fallback, death_fallback_text, fallback_text
from dogido_server.state_machine.response_catalog import (
    response_lines,
    response_text,
    structure_entry_fallback_text,
)
from dogido_server.state_machine.types import DerivedSignals, RecentHearingMemo, RecentVisualMemo

LOGGER = logging.getLogger("uvicorn.error")


class NarrationMixin:
    def _ambient_mob_line(self, event: GameEvent, mobs: list[PassiveMob]) -> str | None:
        if not mobs:
            return None
        candidates = self._ambient_mob_fallback_candidates(event, mobs)
        if not candidates:
            return None
        return candidates[0]

    # 村人団子: 職別連発を止める共有 CD key
    _VILLAGER_CROWD_COOLDOWN_KEY = "villager:crowd"

    def _render_ambient_mob_line(
        self,
        event: GameEvent,
        mobs: list[PassiveMob],
        *,
        villager_crowd: bool = False,
    ) -> str | None:
        fallback = self._ambient_mob_line(event, mobs)
        if fallback is None or not mobs:
            return fallback
        mob = mobs[0]
        direction = self._direction_label(mob)
        is_baby = bool(getattr(mob, "is_baby", None))
        raw_profession = getattr(mob, "profession", None)
        is_villager = self._is_passive_villager(mob)

        # 村人: SM が「明確/不明」を判定してから details を組み立てる（プロンプト判定にしない）
        # 団子（crowd）時は職を渡さず汎用「村人」
        speech_prof: str | None = None
        speech_baby = is_baby
        if is_villager:
            facts = project_villager_speech_facts(
                day_time=self._effective_time_of_day(event),
                is_baby=is_baby,
                profession=None if villager_crowd else raw_profession,
            )
            if villager_crowd:
                # 汎用村人（人数は言わない。職・人数を LLM に渡さない）
                label = "村人"
                speech_prof = None
                speech_baby = False
                entry = resolve_mob_catalog_entry("villager", profession=None, is_baby=False) or {}
                LOGGER.warning(
                    "ambient_villager mode=crowd raw_profession=%s label=%s day_time=%s",
                    raw_profession,
                    label,
                    self._effective_time_of_day(event),
                )
            else:
                label = facts.label
                speech_prof = facts.profession  # 明確なときだけ
                speech_baby = facts.is_baby
                entry = (
                    resolve_mob_catalog_entry(
                        "villager",
                        profession=speech_prof,
                        is_baby=speech_baby,
                    )
                    or {}
                )
                LOGGER.warning(
                    "ambient_villager raw_profession=%s known=%s profession=%s is_baby=%s "
                    "schedule=%s label=%s day_time=%s",
                    raw_profession,
                    facts.profession_known,
                    speech_prof,
                    speech_baby,
                    facts.schedule,
                    label,
                    self._effective_time_of_day(event),
                )
        else:
            entry = resolve_mob_catalog_entry(mob.type) or {}
            label = str(entry.get("label") or self._mob_label(mob.type))

        poetic = entry.get("poetic") if isinstance(entry, dict) else {}
        role = poetic.get("role") if isinstance(poetic, dict) else ""
        variation_slot = event.sequence % 4 if event.sequence is not None else 0
        # crowd 時は mob_count=1（人数を言わせない）
        detail_mobs = [mob] if villager_crowd else mobs
        details: dict[str, object] = {
            "mob": label,
            "direction": direction,
            "mob_count": 1 if villager_crowd else len(mobs),
            "distance": mob.distance,
            "biome": self._biome_label(event.world.biome),
            "time_phase": getattr(event.world.time_phase, "value", event.world.time_phase) or "unknown",
            "mob_tags": list(
                mob_poetic_tags(mob.type, profession=speech_prof, is_baby=speech_baby)
            )[:8],
            "mob_role": str(role) if role else "",
            "mob_temperament": getattr(mob, "temperament", None) or "friendly",
            "mob_caution_reason": getattr(mob, "caution_reason", None) or "",
            "fallback_candidates": self._ambient_mob_fallback_candidates(event, detail_mobs),
            "variation_slot": variation_slot,
        }
        if is_villager and not villager_crowd:
            # 日課は常にコード解決。職は明確なときだけキーを載せる
            details["mob_is_baby"] = speech_baby
            details["villager_schedule"] = facts.schedule
            details["villager_schedule_ja"] = facts.schedule_ja
            if facts.profession_known and facts.profession is not None:
                details["mob_profession"] = facts.profession
            if facts.job_site:
                details["mob_job_site"] = facts.job_site
        elif is_villager and villager_crowd:
            details["mob_is_baby"] = False
            details["villager_schedule"] = facts.schedule
            details["villager_schedule_ja"] = facts.schedule_ja
        return self._generate_leaf_text(
            kind="ambient",
            fallback_text=fallback,
            details=details,
            temperature=0.48,
        )

    def _is_passive_villager(self, mob: PassiveMob) -> bool:
        return (mob.type or "").strip().lower().removeprefix("minecraft:") == "villager"

    def _ambient_mob_type_key(self, mob: PassiveMob) -> str:
        base = (mob.type or "").strip().lower().removeprefix("minecraft:")
        if base != "villager":
            return base
        if getattr(mob, "is_baby", None):
            return "villager:baby"
        prof = (getattr(mob, "profession", None) or "none").strip().lower() or "none"
        return f"villager:{prof}"

    def _villager_activity_for_mob(self, event: GameEvent, mob: PassiveMob) -> str | None:
        if not self._is_passive_villager(mob):
            return None
        return resolve_villager_schedule(
            self._effective_time_of_day(event),
            is_baby=bool(getattr(mob, "is_baby", None)),
            profession=getattr(mob, "profession", None),
        )

    def _awake_villagers(
        self, mobs: list[PassiveMob], event: GameEvent | None
    ) -> list[PassiveMob]:
        """睡眠中を除く村人。crowd 判定用。"""
        out: list[PassiveMob] = []
        for mob in mobs:
            if not self._is_passive_villager(mob):
                continue
            if event is not None:
                activity = self._villager_activity_for_mob(event, mob)
                if activity is not None and should_suppress_ambient_for_sleep(activity):
                    continue
            out.append(mob)
        return out

    def _villager_crowd_mode(self, mobs: list[PassiveMob], event: GameEvent | None) -> bool:
        threshold = max(1, int(getattr(self.settings, "ambient_villager_crowd_threshold", 3)))
        return len(self._awake_villagers(mobs, event)) >= threshold

    def _ambient_type_cooldown_ready(self, key: str, now: datetime) -> bool:
        recent_ms = self._recent_ms(
            now, self.state.last_ambient_mob_comment_at_by_type.get(key)
        )
        return recent_ms is None or recent_ms >= self.settings.ambient_mob_comment_cooldown_ms

    def _next_ambient_mob_target(
        self, mobs: list[PassiveMob], now: datetime, event: GameEvent | None = None
    ) -> PassiveMob | None:
        awake_villagers = self._awake_villagers(mobs, event)
        crowd = len(awake_villagers) >= max(
            1, int(getattr(self.settings, "ambient_villager_crowd_threshold", 3))
        )
        crowd_cd_ready = self._ambient_type_cooldown_ready(self._VILLAGER_CROWD_COOLDOWN_KEY, now)

        # 団子 or crowd CD 中: 村人は crowd key だけで制御（職別連発しない）
        if crowd or not crowd_cd_ready:
            if crowd and crowd_cd_ready and awake_villagers:
                # いちばん近い村人を代表に（距離不明は後ろ）
                return min(
                    awake_villagers,
                    key=lambda m: (
                        m.distance is None,
                        m.distance if m.distance is not None else 1e9,
                    ),
                )
            # crowd CD 中、または crowd だが代表なし → 村人はスキップし他モブへ
            for mob in mobs:
                if self._is_passive_villager(mob):
                    continue
                key = self._ambient_mob_type_key(mob)
                if not key:
                    continue
                if self._ambient_type_cooldown_ready(key, now):
                    return mob
            return None

        for mob in mobs:
            key = self._ambient_mob_type_key(mob)
            if not key:
                continue
            # 睡眠中の村人は ambient を出さない（起こさない）
            if event is not None and self._is_passive_villager(mob):
                activity = self._villager_activity_for_mob(event, mob)
                if activity is not None and should_suppress_ambient_for_sleep(activity):
                    continue
            if self._ambient_type_cooldown_ready(key, now):
                return mob
        return None

    def _emit_ambient_mob_comment_line(self, event: GameEvent, now: datetime) -> str | None:
        # クールダウンは種ごと（村人は villager:職 で別 key → 別職は即出し可）。
        # 村人 N>=threshold は汎用1発 + villager:crowd 共有 CD（職連発渋滞防止）。
        # 既定 120s。同種／同職の連発だけ抑える。全体ギャップは設けない。
        # （⭕️ 牛→鶏 / 農民→聖職者  ❌ 牛→牛 / 農民→農民 / 団子村の職リレー）
        target = self._next_ambient_mob_target(event.passive_mobs, now, event)
        if target is None:
            return None
        villager_crowd = self._is_passive_villager(target) and self._villager_crowd_mode(
            event.passive_mobs, event
        )
        if villager_crowd:
            ordered_mobs = [target]
        else:
            ordered_mobs = [target] + [mob for mob in event.passive_mobs if mob is not target]
        line = self._render_ambient_mob_line(
            event, ordered_mobs, villager_crowd=villager_crowd
        )
        if not line:
            return None
        self.state.last_ambient_mob_comment_at = now
        if villager_crowd:
            self.state.last_ambient_mob_comment_at_by_type[self._VILLAGER_CROWD_COOLDOWN_KEY] = now
        else:
            self.state.last_ambient_mob_comment_at_by_type[self._ambient_mob_type_key(target)] = now
        # モブ反応が優先。発句中の川柳はキャンセルし、静けさが戻ってから再発句する
        self._clear_pending_haiku_prep()
        if villager_crowd:
            label = "村人"
        else:
            entry = resolve_mob_catalog_entry(
                target.type,
                profession=getattr(target, "profession", None),
                is_baby=bool(getattr(target, "is_baby", None)),
            ) or {}
            label = str(entry.get("label") or self._mob_label(target.type))
        self.state.pending_dialogue_notes.append(f"{label}を見た")
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
        if any(hostile == "ender_dragon" for hostile in self.state.last_confirmed_hostiles):
            return response_text("boss", "ender_dragon", "defeated")
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

    def _render_player_chat_reply(self, event: GameEvent) -> str:
        from dogido_server.llm.prompts import resolve_character_mode_from_state

        fallback = fallback_text("general", "chat", "reply")
        combat_active = bool(getattr(event.combat, "combat_active_hint", False)) or self.state.mode in {
            "panic",
            "suppressed_panic",
        }
        has_visual_threats = bool(event.visual_threats)
        recent_visual_types = self._player_chat_recent_visual_types(event)
        effective_visual_types = self._merge_unique_types(
            [str(threat.type) for threat in event.visual_threats if threat.type],
            recent_visual_types,
        )
        # バッファに種が残っていれば「見た」材料あり（character_mode は今フレーム優先）
        has_visual_for_chat = has_visual_threats or bool(recent_visual_types)
        danger_score = event.world.danger_darkness_score
        danger_darkness_high = danger_score is not None and float(danger_score) >= float(
            getattr(self.settings, "darkness_alert_threshold", 0.72)
        )
        character_mode = resolve_character_mode_from_state(
            self.state.mode,
            combat_active=combat_active,
            has_visual_threats=has_visual_threats,
            danger_darkness_high=danger_darkness_high,
        )
        # inventory は重いので、所持品を聞かれたときだけ要約を渡す
        inventory_summary = ""
        held_item_label = ""
        if self.player_input.asks_inventory:
            inventory_summary = self._player_chat_inventory_summary(event)
            held_item_label = self._item_label(event.player.held_item) if event.player.held_item else ""
        # 文脈 STT 補正は雑談理解だけに使う。明示操作・永続化の判定は
        # PlayerInputContext.raw/normalized_text を参照する別経路のまま。
        user_text = (self.player_input.semantic_text or "").strip()
        from dogido_server.player_chat_policy import (
            build_allowed_speech_labels,
            build_identify_skeleton,
            filter_usable_topic_hits,
            has_threat_presence_query,
            reply_policy_line,
            resolve_reply_stance,
            should_enforce_speech_whitelist,
        )

        # 音メモ: 音の明示問い または 在否・気配の問い（#33 戦況）
        # 視覚話題の常時上書きは避けるが、在否では音レンジも材料にする
        wants_sound = bool(self.player_input.asks_about_sound)
        wants_presence = has_threat_presence_query(user_text)
        hearing_summary = ""
        hearing_named_mobs: list[str] = []
        hearing_source_labels: list[str] = []
        hearing_types: list[str] = []
        if wants_sound or wants_presence:
            hearing_summary = self._player_chat_hearing_summary(event)
            hearing_named_mobs = self._player_chat_hearing_named_mobs(event)
            hearing_source_labels = self._player_chat_hearing_source_labels(event)
            hearing_types = self._player_chat_hearing_mob_types(event)
        threat_summary = self._player_chat_threat_summary(
            event,
            include_hearing=wants_sound or wants_presence,
            hearing_summary=hearing_summary,
        )
        place_ctx = self._player_chat_place_context(event)
        precipitation_context = self._precipitation_context(event)
        LOGGER.warning(
            "player_chat_precipitation y=%s temp=%s snow_start_y=%s snowfall_zone=%s "
            "local_precipitation=%s snow_evidence=%s surface_snow=%s",
            precipitation_context.current_y,
            precipitation_context.biome_temperature,
            precipitation_context.snow_start_y,
            precipitation_context.snowfall_zone,
            precipitation_context.precipitation_kind,
            precipitation_context.snow_evidence,
            precipitation_context.surface_snow_observed,
        )
        tactics = self._player_chat_mob_tactics(event, extra_types=recent_visual_types)
        nearby_types = list(tactics.get("nearby_hostile_types") or [])
        if tactics.get("safe_fallback"):
            fallback = str(tactics["safe_fallback"])
        raw_topic_hits = self._player_chat_topic_hits(user_text, effective_visual_types)

        passive_types = self._player_chat_observed_passive_types(event)
        # 存在判定: 視認（recent 含む）∪ 音バッファの種
        observed_ids = self._merge_unique_types(
            effective_visual_types,
            passive_types,
            hearing_types,
        )
        usable_topic_hits = filter_usable_topic_hits(raw_topic_hits)
        reply_stance = resolve_reply_stance(
            has_visual_threats=has_visual_for_chat,
            topic_hits=raw_topic_hits,
            threat_summary=threat_summary,
            user_text=user_text,
            observed_ids=observed_ids,
        )
        reply_policy = reply_policy_line(reply_stance)
        # hypothesis のときだけ強い topic を hints / allowed / plausibility に使う
        topic_for_identify = usable_topic_hits if reply_stance == "hypothesis" else []
        catalog_topic_hints = (
            self._format_player_chat_topic_hints(topic_for_identify) if topic_for_identify else ""
        )
        allowed_speech_labels = build_allowed_speech_labels(
            topic_hits=topic_for_identify,
            visual_types=effective_visual_types,
            passive_types=passive_types,
            hearing_named_mobs=[*hearing_named_mobs, *hearing_source_labels],
        )
        speech_whitelist_enforce = should_enforce_speech_whitelist(
            reply_stance, allowed_speech_labels
        )
        identify_skeleton = build_identify_skeleton(
            stance=reply_stance,
            topic_hits=topic_for_identify,
        )
        from dogido_server.entry_catalog import (
            build_plausibility_hint_lines,
            normalize_biome_id,
            structure_ids_for_plausibility,
        )

        if reply_stance == "hypothesis" and topic_for_identify:
            structure_ids = structure_ids_for_plausibility(topic_for_identify)
            plausibility_lines = build_plausibility_hint_lines(
                topic_hits=topic_for_identify,
                current_biome_id=normalize_biome_id(event.world.biome),
                current_biome_label=self._biome_label(event.world.biome),
            )
        else:
            structure_ids = []
            plausibility_lines = []
        plausibility_hints = "\n".join(f"- {line}" for line in plausibility_lines)
        look_target_label = self._look_target_label(event)
        # ＋は指差しのときだけ観測メモに載せる（戦況・開いた雑談では控えめ）
        look_for_observation = (
            look_target_label if self._player_chat_wants_look_answer(user_text) else ""
        )
        observation_summary = self._player_chat_observation_summary(
            event,
            threat_summary=threat_summary,
            hearing_summary=hearing_summary if (wants_sound or wants_presence) else "",
            passive_types=passive_types,
            look_target_label=look_for_observation,
        )
        LOGGER.warning(
            "player_chat_visual count=%s types=%s recent=%s threat_summary=%s look=%s hearing_n=%s",
            len(event.visual_threats),
            ",".join(str(t.type) for t in event.visual_threats if t.type) or "-",
            ",".join(recent_visual_types) or "-",
            (threat_summary or "")[:120] or "-",
            (look_for_observation or look_target_label or "-"),
            len(hearing_named_mobs) + len(hearing_source_labels),
        )
        if structure_ids or plausibility_lines:
            LOGGER.warning(
                "player_chat_plausibility structures=%s lines=%s",
                ",".join(structure_ids) or "-",
                len(plausibility_lines),
            )
        LOGGER.warning(
            "player_chat_topics raw=%s usable=%s stance=%s allowed=%s enforce_wl=%s",
            ",".join(
                f"{hit.get('entry_id')}:{','.join(hit.get('matched_terms') or ())}"
                for hit in raw_topic_hits
            )
            or "-",
            ",".join(
                f"{hit.get('entry_id')}:{','.join(hit.get('matched_terms') or ())}"
                for hit in usable_topic_hits
            )
            or "-",
            reply_stance,
            ",".join(allowed_speech_labels) or "-",
            speech_whitelist_enforce,
        )
        LOGGER.warning(
            "player_chat_hearing empty=%s mobs=%s sources=%s summary=%s auditory=%d ambient=%d buffer=%d",
            not bool(hearing_summary),
            ",".join(hearing_named_mobs) or "-",
            ",".join(hearing_source_labels) or "-",
            (hearing_summary or "")[:120],
            len(event.auditory_threats),
            len(event.ambient_sounds),
            len(self.state.recent_hearing_memos),
        )
        details = {
            "player_name": self._player_call_name(event),
            "user_text": user_text[:160],
            "biome": self._biome_label(event.world.biome),
            "structure_label": (
                self._structure_label(self.state.current_structure)
                if self.state.current_structure
                else ""
            ),
            "place_context": place_ctx["place_line"],
            "space_kind": place_ctx["space_kind"],
            "sky_visible": place_ctx["sky_visible"],
            "time_phase": getattr(event.world.time_phase, "value", event.world.time_phase) or "unknown",
            # raw weather は world 状態、weather_label は現在Y・気温で雨/雪を解決済み。
            # hearing / 雨音 packet とは混ぜない。
            "weather": self._weather_value(event.world.weather) or "unknown",
            "weather_label": self._player_chat_weather_label(event),
            "weather_fact": self._player_chat_weather_fact(event),
            **precipitation_context.to_prompt_details(),
            "mode": self.state.mode,
            "character_mode": character_mode,
            "combat_active": combat_active,
            "has_visual_threats": has_visual_threats,
            "danger_darkness_high": danger_darkness_high,
            "threat_summary": threat_summary,
            "hearing_summary": hearing_summary,
            "hearing_named_mobs": hearing_named_mobs,
            "hearing_source_labels": hearing_source_labels,
            "asks_about_sound": self.player_input.asks_about_sound,
            "observation_summary": observation_summary,
            "catalog_topic_hints": catalog_topic_hints,
            "catalog_topic_ids": [str(hit.get("entry_id") or "") for hit in topic_for_identify],
            "reply_stance": reply_stance,
            "reply_policy": reply_policy,
            "allowed_speech_labels": allowed_speech_labels,
            "speech_whitelist_enforce": speech_whitelist_enforce,
            "identify_skeleton": identify_skeleton or "",
            "plausibility_hints": plausibility_hints,
            "asks_inventory": self.player_input.asks_inventory,
            "inventory_summary": inventory_summary,
            "held_item_label": held_item_label,
            # 指差し時だけラベルを強く渡す（常時は空に近い）
            "look_target_label": look_for_observation,
            "look_target_kind": (
                str(event.look_target.kind)
                if event.look_target is not None and look_for_observation
                else ""
            ),
            "look_target_name": (
                str(event.look_target.name)
                if event.look_target is not None and look_for_observation
                else ""
            ),
            "nearby_hostile_types": nearby_types,
            "mob_tactics_notes": list(tactics.get("notes") or []),
            "forbidden_advice": list(tactics.get("forbidden_advice") or []),
            "safe_hints": list(tactics.get("safe_hints") or []),
            **self._player_chat_history_details(),
            **self._player_chat_haiku_workshop_details(),
        }
        # Stage3: workshop open 中の player_chat は脅威以外の look/topic で句を食わない
        # （soft 既定で本来は workshop 経路。hard off-topic 時の安全網）
        if self._haiku_workshop_is_open():
            details["look_target_label"] = ""
            details["look_target_kind"] = ""
            details["look_target_name"] = ""
            details["catalog_topic_hints"] = ""
            details["catalog_topic_ids"] = []
            details["plausibility_hints"] = ""
            details["identify_skeleton"] = ""
            details["reply_stance"] = "none"
            details["reply_policy"] = reply_policy_line("none")
            details["speech_whitelist_enforce"] = False
            details["allowed_speech_labels"] = []
            if not self.player_input.asks_about_sound:
                details["hearing_summary"] = ""
                details["hearing_named_mobs"] = []
                details["hearing_source_labels"] = []
            if not self.player_input.asks_inventory:
                details["inventory_summary"] = ""
                details["held_item_label"] = ""
            # observation も look/topic 抜きで再構成（脅威は残す）
            details["observation_summary"] = self._player_chat_observation_summary(
                event,
                threat_summary=threat_summary,
                hearing_summary=str(details.get("hearing_summary") or ""),
                passive_types=[],
                look_target_label="",
            )
            identify_skeleton = ""
            LOGGER.warning(
                "player_chat_workshop_strip open=1 look/topic/hearing stripped "
                "(keep threat only)"
            )
        # S3: 高信頼 identify は LLM より骨子を優先できる（オフ時・失敗時の最低限）
        preferred_fallback = identify_skeleton or fallback
        text = self._generate_leaf_text(
            kind="player_chat",
            fallback_text=preferred_fallback,
            details=details,
            temperature=0.65,
        )
        from dogido_server.llm.sanitize import contains_forbidden_mob_advice, is_style_acceptable

        if contains_forbidden_mob_advice(text, details):
            return preferred_fallback
        # S2: 白リスト外種名なども style 不合格 → 骨子 or 中立 fallback
        if not is_style_acceptable("player_chat", text, details):
            LOGGER.warning(
                "player_chat_style_reject stance=%s allowed=%s text=%s",
                reply_stance,
                ",".join(allowed_speech_labels) or "-",
                (text or "")[:80],
            )
            return preferred_fallback
        return text

    def _player_chat_weather_label(self, event: GameEvent) -> str:
        """雑談用。globalな雨を、現在地の気温・標高で雪へ解決する。"""
        from dogido_server.state_machine.constants import WEATHER_LABELS

        if self._precipitation_context(event).precipitation_kind == "snow":
            return "雪"
        weather = self._weather_value(event.world.weather)
        if not weather:
            return "不明"
        return WEATHER_LABELS.get(str(weather), str(weather))

    def _player_chat_weather_fact(self, event: GameEvent) -> str:
        """昼の雨・雷だけ、地上の敵に関する淡々とした事実。

        日光でアンデッドが燃えない時間帯＝昼間に限る。安全断定はしない。
        """
        weather = self._weather_value(event.world.weather)
        if weather not in {"rain", "thunder"}:
            return ""
        phase = getattr(event.world.time_phase, "value", event.world.time_phase) or ""
        if str(phase) not in {"day", "morning"}:
            return ""
        return "薄暗い。敵が地上にいる。"

    def _player_chat_place_context(self, event: GameEvent) -> dict[str, object]:
        """地表バイオームと「空間」（地下っぽさ）を分けて chat に渡す。

        biome id が白樺の森のままでも、sky_visible / 天井 / 囲まれ度で洞窟っぽさを伝える。
        """
        biome_label = self._biome_label(event.world.biome)
        sky_raw = event.world.sky_visible
        sky_visible = bool(sky_raw) if sky_raw is not None else None
        y = event.player.position.y
        ceiling = event.world.ceiling_height
        enclosure = float(event.world.enclosure_score or 0.0)
        cover = (event.world.overhead_cover_type or "unknown").lower()
        light = event.world.local_light
        structure = (
            self._structure_label(self.state.current_structure)
            if self.state.current_structure
            else ""
        )

        cave_biome = self._is_cave_biome(event.world.biome)
        submerged = bool(event.world.is_submerged)
        occluded = self._is_occluded_environment(event)
        foliage = self._is_foliage_shade_context(event)
        low_ceiling = ceiling is not None and ceiling <= 8.0
        deep_y = y is not None and y <= 48.0
        enclosed = enclosure >= 0.35

        if submerged:
            space_kind = "underwater"
            space_ja = "水中"
        elif cave_biome:
            space_kind = "cave_biome"
            space_ja = "洞窟バイオームの中"
        elif sky_visible is False and (low_ceiling or enclosed or deep_y or occluded):
            space_kind = "underground_or_roofed"
            space_ja = "地下っぽい／屋根のある空間（空は見えない）"
        elif foliage or (cover == "foliage" and sky_visible is not True):
            space_kind = "canopy"
            space_ja = "木陰っぽい空間"
        elif sky_visible is True:
            space_kind = "open_surface"
            space_ja = "開けた地上（空が見える）"
        elif sky_visible is False:
            space_kind = "roofed_unclear"
            space_ja = "空は見えないが、深さははっきりしない空間"
        else:
            space_kind = "unknown"
            space_ja = "空間の詳細は不明"

        sky_ja = {
            True: "空が見える",
            False: "空は見えない",
            None: "空の見え方は不明",
        }[sky_visible]
        bits = [
            f"地表バイオーム: {biome_label}",
            f"空間: {space_ja}",
            sky_ja,
        ]
        if y is not None:
            bits.append(f"高さY{int(round(y))}")
        if ceiling is not None:
            bits.append(f"天井おおよそ{ceiling:.0f}m")
        if light is not None:
            bits.append(f"明るさ{light}")
        if structure:
            bits.append(f"構造物: {structure}")
        place_line = " / ".join(bits)
        return {
            "space_kind": space_kind,
            "sky_visible": sky_visible,
            "place_line": place_line,
            "biome_label": biome_label,
        }

    def _player_chat_topic_hits(
        self,
        user_text: str,
        observed_types: list[str] | tuple[str, ...],
    ) -> list[dict[str, object]]:
        """プレイヤー文 → カタログ話題候補（種族ハードコードなし）。"""
        from dogido_server.entry_catalog import find_catalog_topics

        return find_catalog_topics(user_text, observed_ids=observed_types)

    def _format_player_chat_topic_hints(self, hits: list[dict[str, object]]) -> str:
        from dogido_server.entry_catalog import format_catalog_topic_hints

        return format_catalog_topic_hints(hits)

    def _merge_unique_types(self, *groups: list[str] | tuple[str, ...]) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for group in groups:
            for raw in group:
                text = str(raw or "").removeprefix("minecraft:").strip().lower()
                if not text or text in seen:
                    continue
                seen.add(text)
                merged.append(text)
        return merged

    def _player_chat_observed_passive_types(self, event: GameEvent) -> list[str]:
        """今フレームの passive_mobs + 直近見た平和/中立（ambient 根拠）。"""
        now = event.observed_at
        retention_ms = int(getattr(self.settings, "player_chat_visual_retention_ms", 12000))
        # ambient はもう少し長く「話題に残ってよい」
        ambient_retention_ms = max(retention_ms, 60000)
        current = [str(mob.type) for mob in (event.passive_mobs or []) if getattr(mob, "type", None)]
        recent: list[str] = []
        for mob_type, seen_at in (self.state.recent_passive_mob_seen_at_by_type or {}).items():
            age = self._recent_ms(now, seen_at)
            if age is not None and age <= ambient_retention_ms:
                recent.append(str(mob_type))
        return self._merge_unique_types(current, recent)

    def _look_target_label(self, event: GameEvent) -> str:
        """クロスヘア対象の日本語ラベル。無ければ空。"""
        target = getattr(event, "look_target", None)
        if target is None or not getattr(target, "name", None):
            return ""
        name = str(target.name)
        kind = str(getattr(target, "kind", None) or "block").lower()
        if kind == "entity":
            from dogido_server.entry_catalog import mob_entry

            entry = mob_entry(name)
            if entry and entry.get("label"):
                return str(entry["label"])
            return self._hostile_label(name) if hasattr(self, "_hostile_label") else name
        # block（感圧板・花など）
        return self._block_label(name) or name

    def _player_chat_wants_look_answer(self, user_text: str) -> bool:
        """『これ何』など指差し・視線先を聞いているか（＋を控えめに使う）。"""
        text = (user_text or "").strip()
        if not text:
            return False
        markers = (
            "これ何",
            "これなに",
            "これは何",
            "これはなに",
            "何かな",
            "なにかな",
            "何これ",
            "なにこれ",
            "それ何",
            "それなに",
            "あれ何",
            "このブロック",
            "この花",
            "この石",
            "見てる",
            "指して",
            "指差",
        )
        if any(m in text for m in markers):
            return True
        # 「これは？」単体・短い指差し
        compact = text.replace(" ", "").replace("　", "")
        if compact in {"これ？", "これ?", "これ", "それ？", "それ?", "あれ？", "あれ?"}:
            return True
        if ("これ" in text or "それ" in text or "あれ" in text) and (
            "何" in text or "なに" in text or "？" in text or "?" in text
        ):
            return True
        return False

    def _player_chat_observation_summary(
        self,
        event: GameEvent,
        *,
        threat_summary: str,
        hearing_summary: str,
        passive_types: list[str] | tuple[str, ...],
        look_target_label: str | None = None,
    ) -> str:
        """観測だけの短い事実メモ（topic 仮説は混ぜない）。最大数行。"""
        lines: list[str] = []
        look = (look_target_label or "").strip()
        if look:
            lines.append(f"視線先: {look}")
        vehicle_fact = player_vehicle_fact(event.player.vehicle)
        if vehicle_fact:
            # 「プレイヤー」を保った完成文だけ渡し、ドギド自身の行動にしない。
            lines.append(vehicle_fact)
        threat = (threat_summary or "").strip()
        if threat and threat != "とくになし":
            lines.append(f"脅威: {threat}")
        labels: list[str] = []
        seen: set[str] = set()
        from dogido_server.entry_catalog import mob_entry

        for mob_type in passive_types:
            entry = mob_entry(mob_type)
            label = str((entry or {}).get("label") or mob_type)
            if label and label not in seen:
                seen.add(label)
                labels.append(label)
            if len(labels) >= 4:
                break
        if labels:
            lines.append(f"近くの生き物: {'、'.join(labels)}")
        hearing = (hearing_summary or "").strip()
        if hearing:
            lines.append(f"音: {hearing}")
        return "\n".join(f"- {line}" for line in lines[:4])

    def _player_chat_mob_tactics(
        self,
        event: GameEvent,
        *,
        extra_types: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, object]:
        """観測（今フレーム + visual バッファ）の敵対だけ tactics を集約。

        トピック仮説だけの種は混ぜない（空観測で断定的 tactics を出さない）。
        """
        from dogido_server.entry_catalog import collect_dogido_tactics_for_mobs

        nearby_types = self._merge_unique_types(
            [threat.type for threat in event.visual_threats if threat.type],
            list(extra_types or ()),
        )
        if not nearby_types:
            return {
                "nearby_hostile_types": [],
                "notes": [],
                "forbidden_advice": [],
                "safe_hints": [],
                "safe_fallback": None,
            }
        tactics = collect_dogido_tactics_for_mobs(nearby_types)
        safe_fallback = None
        # 今フレームに視認があるときだけ短い安全 fallback（バッファのみは threat 文に任せる）
        if event.visual_threats and (tactics.get("forbidden_advice") or tactics.get("safe_hints")):
            nearest = min(
                event.visual_threats,
                key=lambda threat: threat.distance if threat.distance is not None else 999.0,
            )
            direction = self._direction_label(nearest)
            label = self._hostile_label(nearest.type)
            hints = tactics.get("safe_hints") or []
            hint = str(hints[0]) if hints else "気いつけ"
            safe_fallback = f"{direction}に{label}や！{hint}や！"
        return {
            "nearby_hostile_types": nearby_types,
            "notes": tactics.get("notes") or [],
            "forbidden_advice": tactics.get("forbidden_advice") or [],
            "safe_hints": tactics.get("safe_hints") or [],
            "safe_fallback": safe_fallback,
        }

    def _player_chat_history_details(self) -> dict[str, str]:
        """Session 側の DialogueContext があれば会話履歴・出来事を返す。"""
        provider = getattr(self, "dialogue_context_provider", None)
        if provider is None:
            return {"conversation_history": "", "event_digest": ""}
        try:
            context = provider()
        except Exception:
            return {"conversation_history": "", "event_digest": ""}
        if context is None:
            return {"conversation_history": "", "event_digest": ""}
        blocks = context.prompt_blocks()
        return {
            "conversation_history": str(blocks.get("conversation_history") or ""),
            "event_digest": str(blocks.get("event_digest") or ""),
        }

    def _player_chat_haiku_workshop_details(self) -> dict[str, str]:
        """open 中の句 pin（履歴と別）。"""
        provider = getattr(self, "haiku_workshop_provider", None)
        if provider is None:
            return {}
        try:
            workshop = provider()
        except Exception:
            return {}
        from dogido_server.haiku.workshop import workshop_prompt_details

        return workshop_prompt_details(workshop)

    def _player_chat_threat_summary(
        self,
        event: GameEvent,
        *,
        include_hearing: bool = False,
        hearing_summary: str = "",
    ) -> str:
        """戦況メモ。在否・音問いでは音レンジ（hearing）も載せる。"""
        parts: list[str] = []
        if event.visual_threats:
            nearest = min(
                event.visual_threats,
                key=lambda threat: threat.distance if threat.distance is not None else 999.0,
            )
            direction = self._direction_label(nearest)
            distance = f"{nearest.distance:.0f}マス" if nearest.distance is not None else "近く"
            label = self._hostile_label(nearest.type)
            parts.append(f"視認 {label} が{direction} {distance}")
            # 今フレームで渡された本数を素直に（創作しない）
            n = len(event.visual_threats)
            if n > 1:
                parts.append(f"視認リスト{n}体")
            else:
                parts.append("視認リスト1体")
        else:
            # 今フレーム 0 でも直近バッファがあれば「ついさっき」
            recent_line = self._player_chat_recent_visual_summary_line(event)
            if recent_line:
                parts.append(recent_line)
            elif event.auditory_threats and not include_hearing:
                # 在否・音問い以外のフォールバック（1件だけ）
                audio = event.auditory_threats[0]
                direction = self._direction_label(audio)
                band = getattr(audio.distance_band, "value", audio.distance_band) or ""
                name = self._resolve_hearing_mob_label(audio.label, getattr(audio, "sound_event", None))
                if name:
                    parts.append(f"音 {name} {direction} {band}".strip())
                else:
                    parts.append(f"音（種別未確定） {direction} {band}".strip())
        # 在否・音問い: 音レンジの要約を明示（視認と併記可）
        if include_hearing and (hearing_summary or "").strip():
            parts.append(f"音メモ: {hearing_summary.strip()}")
        if event.combat.combat_active_hint and parts:
            parts.append("交戦中っぽい")
        return "、".join(parts)

    def _player_chat_hearing_mob_types(self, event: GameEvent) -> list[str]:
        """hearing バッファ＋今フレームから種 id を集める（存在判定用）。"""
        types: list[str] = []
        seen: set[str] = set()
        now = event.observed_at
        retention_ms = int(getattr(self.settings, "player_chat_hearing_retention_ms", 20000))

        def _add(mob_type: str | None) -> None:
            mid = str(mob_type or "").removeprefix("minecraft:").strip().lower()
            if not mid or mid in seen:
                return
            seen.add(mid)
            types.append(mid)

        for audio in event.auditory_threats:
            _add(self._resolve_hearing_mob_type(audio.label, getattr(audio, "sound_event", None)))
        for sound in event.ambient_sounds:
            _add(self._resolve_hearing_mob_type(sound.type, getattr(sound, "sound_event", None)))
        for memo in self.state.recent_hearing_memos:
            age = self._recent_ms(now, memo.heard_at)
            if age is not None and age <= retention_ms:
                _add(memo.mob_type)
        return types

    def _remember_visual_for_chat(self, event: GameEvent, now: datetime) -> None:
        """今フレームの visual_threats を短期バッファへ。"""
        retention_ms = int(getattr(self.settings, "player_chat_visual_retention_ms", 12000))
        kept: list[RecentVisualMemo] = []
        for memo in self.state.recent_visual_memos:
            age = self._recent_ms(now, memo.seen_at)
            if age is not None and age <= retention_ms:
                kept.append(memo)

        by_key = {memo.dedupe_key: memo for memo in kept}
        for threat in event.visual_threats:
            mob_type = str(threat.type or "").removeprefix("minecraft:").strip().lower()
            if not mob_type:
                continue
            direction = self._direction_label(threat)
            label_ja = self._hostile_label(mob_type)
            key = f"visual:{mob_type}:{direction}"
            by_key[key] = RecentVisualMemo(
                mob_type=mob_type,
                label_ja=label_ja,
                direction=direction,
                distance=threat.distance,
                seen_at=now,
                dedupe_key=key,
            )

        memos = sorted(by_key.values(), key=lambda memo: memo.seen_at, reverse=True)[:12]
        self.state.recent_visual_memos = memos

    def _player_chat_recent_visual_types(self, event: GameEvent) -> list[str]:
        now = event.observed_at
        retention_ms = int(getattr(self.settings, "player_chat_visual_retention_ms", 12000))
        types: list[str] = []
        seen: set[str] = set()
        for memo in self.state.recent_visual_memos:
            age = self._recent_ms(now, memo.seen_at)
            if age is None or age > retention_ms:
                continue
            if memo.mob_type in seen:
                continue
            seen.add(memo.mob_type)
            types.append(memo.mob_type)
        return types

    def _player_chat_recent_visual_summary_line(self, event: GameEvent) -> str | None:
        """バッファ先頭の視認を1行に（ついさっき ピリジャー 前）。"""
        now = event.observed_at
        retention_ms = int(getattr(self.settings, "player_chat_visual_retention_ms", 12000))
        for memo in self.state.recent_visual_memos:
            age = self._recent_ms(now, memo.seen_at)
            if age is None or age > retention_ms:
                continue
            label = memo.label_ja or memo.mob_type
            direction = memo.direction or "近く"
            return f"ついさっき 視認 {label} が{direction}"
        return None

    def _resolve_hearing_mob_type(self, raw_type: str | None, sound_event: str | None = None) -> str | None:
        """sound / label から mob カタログ id を解決。解決できなければ None。"""
        from dogido_server.entry_catalog import mob_entry

        candidates: list[str] = []
        if raw_type:
            candidates.append(str(raw_type).removeprefix("minecraft:").strip().lower())
        if sound_event:
            # entity.zombie.ambient / entity.minecraft.zombie.hurt など
            se = str(sound_event).removeprefix("minecraft:").strip().lower().replace("/", ".")
            parts = [p for p in se.split(".") if p and p not in {"entity", "minecraft", "hostile", "neutral", "passive"}]
            candidates.extend(parts)
        seen: set[str] = set()
        for cand in candidates:
            if not cand or cand in seen:
                continue
            seen.add(cand)
            if mob_entry(cand) is not None:
                return cand
        return None

    def _resolve_hearing_mob_label(self, raw_type: str | None, sound_event: str | None = None) -> str | None:
        from dogido_server.entry_catalog import mob_entry
        from dogido_server.state_machine.constants import MOB_LABELS

        mob_type = self._resolve_hearing_mob_type(raw_type, sound_event)
        if not mob_type:
            return None
        entry = mob_entry(mob_type)
        if entry is not None:
            label = str(entry.get("label") or "").strip()
            if label:
                return label
        mapped = MOB_LABELS.get(mob_type)
        return str(mapped) if mapped else None

    def _resolve_hearing_environment_label(
        self,
        raw_type: str | None,
        sound_event: str | None = None,
    ) -> str | None:
        """adapter が実再生音から確定した環境音源だけを表示名へ変換する。

        周辺ブロック名や説明文から「鳴るはず」と推測しない。`block:` / `weather:` /
        `environment:` は SoundManager で実際に観測した sound_event にのみ付く。
        """
        raw = str(raw_type or "").strip().lower()
        if raw.startswith("weather:"):
            return {
                "weather:rain": "雨",
                "weather:thunder": "雷鳴",
            }.get(raw)
        if raw.startswith("environment:"):
            return {
                "environment:cave": "洞窟の環境音",
                "environment:underwater": "水中の環境音",
                "environment:basalt_deltas": "玄武岩デルタの環境音",
                "environment:crimson_forest": "真紅の森の環境音",
                "environment:nether_wastes": "ネザーの荒地の環境音",
                "environment:soul_sand_valley": "ソウルサンドの谷の環境音",
                "environment:warped_forest": "歪んだ森の環境音",
            }.get(raw)
        if not raw.startswith("block:"):
            return None
        block_id = raw.removeprefix("block:").strip()
        if not block_id:
            return None
        from dogido_server.entry_catalog import block_entry

        entry = block_entry(block_id)
        if entry is not None:
            label = str(entry.get("label") or entry.get("japanese") or "").strip()
            if label:
                return label
        # sound_event が音源を直接確定しているが、カタログに単独項目がないものだけ。
        return {
            "fire": "火",
            "lava": "溶岩",
            "water": "水",
            "nether_portal": "ネザーポータル",
            "bubble_column": "気泡柱",
            "trial_spawner": "トライアルスポナー",
            "pointed_dripstone": "鍾乳石",
            "wooden_door": "木のドア",
            "wooden_trapdoor": "木のトラップドア",
            "wooden_button": "木のボタン",
            "wooden_pressure_plate": "木の感圧板",
        }.get(block_id)

    def _remember_hearing_for_chat(self, event: GameEvent, now: datetime) -> None:
        """今フレームの音を短期バッファへ。player_chat が数秒遅れても種名を使えるようにする。"""
        retention_ms = int(getattr(self.settings, "player_chat_hearing_retention_ms", 12000))
        # prune
        kept: list[RecentHearingMemo] = []
        for memo in self.state.recent_hearing_memos:
            age = self._recent_ms(now, memo.heard_at)
            if age is not None and age <= retention_ms:
                kept.append(memo)

        by_key = {memo.dedupe_key: memo for memo in kept}

        def _dir_band(obj: object) -> tuple[str, str]:
            direction = self._direction_label(obj)  # type: ignore[arg-type]
            band = str(getattr(getattr(obj, "distance_band", None), "value", getattr(obj, "distance_band", None)) or "")
            return direction, band

        for audio in event.auditory_threats:
            direction, band = _dir_band(audio)
            mob_type = self._resolve_hearing_mob_type(audio.label, getattr(audio, "sound_event", None))
            label_ja = self._resolve_hearing_mob_label(audio.label, getattr(audio, "sound_event", None))
            key = f"hostile:{mob_type or audio.label}:{direction}:{band}"
            by_key[key] = RecentHearingMemo(
                kind="hostile",
                mob_type=mob_type,
                label_ja=label_ja,
                direction=direction,
                distance_band=band,
                heard_at=now,
                dedupe_key=key,
            )

        for sound in event.ambient_sounds:
            direction, band = _dir_band(sound)
            raw = str(sound.type or "")
            mob_type = self._resolve_hearing_mob_type(raw, getattr(sound, "sound_event", None))
            environment_label = self._resolve_hearing_environment_label(
                raw, getattr(sound, "sound_event", None)
            )
            label_ja = environment_label or self._resolve_hearing_mob_label(
                raw, getattr(sound, "sound_event", None)
            )
            kind = "environment" if environment_label else "ambient"
            key = f"{kind}:{mob_type or raw}:{direction}:{band}"
            by_key[key] = RecentHearingMemo(
                kind=kind,
                mob_type=mob_type,
                label_ja=label_ja,
                direction=direction,
                distance_band=band,
                heard_at=now,
                dedupe_key=key,
            )

        # 雷鳴は落雷座標が通常の音距離より遠くても、クライアントで実際に
        # 再生されれば world の recent_ms に載る。ambient_sounds 側に距離で
        # 載らなかった場合だけ補い、「今の音なに？」へ短期記憶から答えられるようにする。
        observed_environment_types = {
            str(sound.type or "").strip().lower() for sound in event.ambient_sounds
        }
        recent_weather_sounds = (
            ("thunder", "雷鳴", self._has_recent_thunder_sound(event)),
            ("rain", "雨", self._has_recent_rain_sound(event)),
        )
        for weather_kind, label_ja, heard in recent_weather_sounds:
            raw = f"weather:{weather_kind}"
            if not heard or raw in observed_environment_types:
                continue
            key = f"environment:{raw}:周囲:"
            by_key[key] = RecentHearingMemo(
                kind="environment",
                mob_type=None,
                label_ja=label_ja,
                direction="周囲",
                distance_band="",
                heard_at=now,
                dedupe_key=key,
            )

        # 新しい順に上限
        memos = sorted(by_key.values(), key=lambda m: m.heard_at, reverse=True)[:12]
        self.state.recent_hearing_memos = memos

    def _player_chat_hearing_summary(self, event: GameEvent) -> str:
        """今フレーム + 直近バッファの音要約。名前は実音またはカタログ解決だけ。"""
        now = event.observed_at
        retention_ms = int(getattr(self.settings, "player_chat_hearing_retention_ms", 12000))
        parts: list[str] = []
        seen_keys: set[str] = set()

        def _add_line(key: str, line: str) -> None:
            if key in seen_keys or not line:
                return
            seen_keys.add(key)
            parts.append(line)

        # 1) 今フレーム優先
        for audio in event.auditory_threats[:4]:
            direction = self._direction_label(audio)
            band = getattr(audio.distance_band, "value", audio.distance_band) or ""
            label_ja = self._resolve_hearing_mob_label(audio.label, getattr(audio, "sound_event", None))
            key = f"hostile:{label_ja or audio.label}:{direction}:{band}"
            if label_ja:
                _add_line(key, f"{label_ja}の音 {direction} {band}".strip())
            else:
                _add_line(key, f"音（種別未確定） {direction} {band}".strip())

        for sound in event.ambient_sounds[:4]:
            direction = self._direction_label(sound)  # type: ignore[arg-type]
            band = getattr(sound.distance_band, "value", sound.distance_band) or ""
            raw = str(sound.type or "")
            environment_label = self._resolve_hearing_environment_label(
                raw, getattr(sound, "sound_event", None)
            )
            mob_label = self._resolve_hearing_mob_label(
                raw, getattr(sound, "sound_event", None)
            )
            kind = "environment" if environment_label else "ambient"
            key = f"{kind}:{environment_label or mob_label or raw}:{direction}:{band}"
            if environment_label:
                _add_line(key, f"{environment_label}の音 {direction} {band}".strip())
            elif mob_label:
                _add_line(key, f"{mob_label}っぽい声 {direction} {band}".strip())
            else:
                _add_line(key, f"音（種別未確定） {direction} {band}".strip())

        # 2) 直近バッファ（今フレームで埋まらなかった分）
        for memo in self.state.recent_hearing_memos:
            if len(parts) >= 6:
                break
            age = self._recent_ms(now, memo.heard_at)
            if age is None or age > retention_ms:
                continue
            memo_key = (
                f"{memo.kind}:{memo.label_ja or memo.mob_type or 'unknown'}:"
                f"{memo.direction}:{memo.distance_band}"
            )
            if memo_key in seen_keys:
                continue
            if memo.label_ja:
                if memo.kind == "hostile":
                    line = f"{memo.label_ja}の音 {memo.direction} {memo.distance_band}（ついさっき）".strip()
                elif memo.kind == "environment":
                    line = f"{memo.label_ja}の音 {memo.direction} {memo.distance_band}（ついさっき）".strip()
                else:
                    line = f"{memo.label_ja}っぽい声 {memo.direction} {memo.distance_band}（ついさっき）".strip()
            else:
                line = f"音（種別未確定） {memo.direction} {memo.distance_band}（ついさっき）".strip()
            _add_line(memo_key, line)

        return "、".join(parts)

    def _player_chat_hearing_named_mobs(self, event: GameEvent) -> list[str]:
        """hearing 要約から、種名として使ってよいカタログ名だけ。"""
        names: list[str] = []
        seen: set[str] = set()
        now = event.observed_at
        retention_ms = int(getattr(self.settings, "player_chat_hearing_retention_ms", 12000))

        def _add(name: str | None) -> None:
            text = str(name or "").strip()
            if not text or text in seen:
                return
            seen.add(text)
            names.append(text)

        for audio in event.auditory_threats:
            _add(self._resolve_hearing_mob_label(audio.label, getattr(audio, "sound_event", None)))
        for sound in event.ambient_sounds:
            _add(self._resolve_hearing_mob_label(sound.type, getattr(sound, "sound_event", None)))
        for memo in self.state.recent_hearing_memos:
            age = self._recent_ms(now, memo.heard_at)
            if age is not None and age <= retention_ms and memo.kind != "environment":
                _add(memo.label_ja)
        return names

    def _player_chat_hearing_source_labels(self, event: GameEvent) -> list[str]:
        """実再生されたブロック・天候・環境音から、発話してよい音源名を返す。"""
        labels: list[str] = []
        seen: set[str] = set()
        now = event.observed_at
        retention_ms = int(getattr(self.settings, "player_chat_hearing_retention_ms", 12000))

        def _add(label: str | None) -> None:
            text = str(label or "").strip()
            if not text or text in seen:
                return
            seen.add(text)
            labels.append(text)

        for sound in event.ambient_sounds:
            _add(
                self._resolve_hearing_environment_label(
                    sound.type, getattr(sound, "sound_event", None)
                )
            )
        for memo in self.state.recent_hearing_memos:
            age = self._recent_ms(now, memo.heard_at)
            if age is not None and age <= retention_ms and memo.kind == "environment":
                _add(memo.label_ja)
        return labels

    def _player_chat_inventory_summary(self, event: GameEvent, *, max_items: int = 18) -> str:
        """所持品の短い要約。player_chat 専用。常時注入しない。"""
        counted: list[tuple[int, str, str]] = []
        for item_id, count in event.inventory.items():
            try:
                amount = int(count)
            except (TypeError, ValueError):
                continue
            if amount <= 0:
                continue
            key = str(item_id).removeprefix("minecraft:")
            label = self._item_label(key)
            counted.append((amount, key, label))
        if not counted:
            return "（所持品データなし、または空）"
        # 多い順、同数なら id 順。上位だけ渡してプロンプトを軽く保つ
        counted.sort(key=lambda row: (-row[0], row[1]))
        parts = [f"{label}×{amount}" for amount, _key, label in counted[:max_items]]
        if len(counted) > max_items:
            parts.append(f"ほか{len(counted) - max_items}種")
        return "、".join(parts)

    def _item_label(self, item_id: str | None) -> str:
        if not item_id:
            return ""
        from dogido_server.state_machine.constants import BLOCK_LABELS, ITEM_LABELS

        key = str(item_id).removeprefix("minecraft:")
        # 松明などは block カタログ側に日本語がある
        return str(ITEM_LABELS.get(key) or BLOCK_LABELS.get(key) or key)

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
