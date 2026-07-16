# state_machine/mixins/narration.py
from __future__ import annotations

import logging
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
        label = self._mob_label(target.type)
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
        threat_summary = self._player_chat_threat_summary(event)
        hearing_summary = self._player_chat_hearing_summary(event)
        hearing_named_mobs = self._player_chat_hearing_named_mobs(event)
        place_ctx = self._player_chat_place_context(event)
        tactics = self._player_chat_mob_tactics(event, extra_types=recent_visual_types)
        nearby_types = list(tactics.get("nearby_hostile_types") or [])
        if tactics.get("safe_fallback"):
            fallback = str(tactics["safe_fallback"])
        user_text = (self.player_input.raw_text or "").strip()
        topic_hits = self._player_chat_topic_hits(user_text, effective_visual_types)
        catalog_topic_hints = self._format_player_chat_topic_hints(topic_hits)
        from dogido_server.player_chat_policy import (
            build_allowed_speech_labels,
            build_identify_skeleton,
            reply_policy_line,
            resolve_reply_stance,
            should_enforce_speech_whitelist,
        )

        reply_stance = resolve_reply_stance(
            has_visual_threats=has_visual_for_chat,
            topic_hits=topic_hits,
            threat_summary=threat_summary,
            user_text=user_text,
        )
        reply_policy = reply_policy_line(reply_stance)
        # ambient / 平和・中立 mob も「現実にいる」根拠として許可名に入れる
        passive_types = self._player_chat_observed_passive_types(event)
        allowed_speech_labels = build_allowed_speech_labels(
            topic_hits=topic_hits,
            visual_types=effective_visual_types,
            passive_types=passive_types,
            hearing_named_mobs=hearing_named_mobs,
        )
        speech_whitelist_enforce = should_enforce_speech_whitelist(
            reply_stance, allowed_speech_labels
        )
        identify_skeleton = build_identify_skeleton(
            stance=reply_stance,
            topic_hits=topic_hits,
        )
        from dogido_server.entry_catalog import (
            build_plausibility_hint_lines,
            normalize_biome_id,
            structure_ids_for_plausibility,
        )

        structure_ids = structure_ids_for_plausibility(topic_hits)
        plausibility_lines = build_plausibility_hint_lines(
            topic_hits=topic_hits,
            current_biome_id=normalize_biome_id(event.world.biome),
            current_biome_label=self._biome_label(event.world.biome),
        )
        plausibility_hints = "\n".join(f"- {line}" for line in plausibility_lines)
        LOGGER.warning(
            "player_chat_visual count=%s types=%s recent=%s threat_summary=%s",
            len(event.visual_threats),
            ",".join(str(t.type) for t in event.visual_threats if t.type) or "-",
            ",".join(recent_visual_types) or "-",
            (threat_summary or "")[:120] or "-",
        )
        if structure_ids or plausibility_lines:
            LOGGER.warning(
                "player_chat_plausibility structures=%s lines=%s",
                ",".join(structure_ids) or "-",
                len(plausibility_lines),
            )
        LOGGER.warning(
            "player_chat_topics empty=%s hits=%s stance=%s allowed=%s enforce_wl=%s",
            not bool(topic_hits),
            ",".join(
                f"{hit.get('entry_id')}:{','.join(hit.get('matched_terms') or ())}"
                for hit in topic_hits
            )
            or "-",
            reply_stance,
            ",".join(allowed_speech_labels) or "-",
            speech_whitelist_enforce,
        )
        LOGGER.warning(
            "player_chat_hearing empty=%s named=%s summary=%s auditory=%d ambient=%d buffer=%d",
            not bool(hearing_summary),
            ",".join(hearing_named_mobs) or "-",
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
            "mode": self.state.mode,
            "character_mode": character_mode,
            "combat_active": combat_active,
            "has_visual_threats": has_visual_threats,
            "danger_darkness_high": danger_darkness_high,
            "threat_summary": threat_summary,
            "hearing_summary": hearing_summary,
            "hearing_named_mobs": hearing_named_mobs,
            "catalog_topic_hints": catalog_topic_hints,
            "catalog_topic_ids": [str(hit.get("entry_id") or "") for hit in topic_hits],
            "reply_stance": reply_stance,
            "reply_policy": reply_policy,
            "allowed_speech_labels": allowed_speech_labels,
            "speech_whitelist_enforce": speech_whitelist_enforce,
            "identify_skeleton": identify_skeleton or "",
            "plausibility_hints": plausibility_hints,
            "asks_inventory": self.player_input.asks_inventory,
            "inventory_summary": inventory_summary,
            "held_item_label": held_item_label,
            "nearby_hostile_types": nearby_types,
            "mob_tactics_notes": list(tactics.get("notes") or []),
            "forbidden_advice": list(tactics.get("forbidden_advice") or []),
            "safe_hints": list(tactics.get("safe_hints") or []),
            **self._player_chat_history_details(),
        }
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

    def _player_chat_threat_summary(self, event: GameEvent) -> str:
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
            if len(event.visual_threats) > 1:
                parts.append(f"ほか{len(event.visual_threats) - 1}体")
        else:
            # 今フレーム 0 でも直近バッファがあれば「ついさっき」
            recent_line = self._player_chat_recent_visual_summary_line(event)
            if recent_line:
                parts.append(recent_line)
            elif event.auditory_threats:
                audio = event.auditory_threats[0]
                direction = self._direction_label(audio)
                band = getattr(audio.distance_band, "value", audio.distance_band) or ""
                name = self._resolve_hearing_mob_label(audio.label, getattr(audio, "sound_event", None))
                if name:
                    parts.append(f"音 {name} {direction} {band}".strip())
                else:
                    parts.append(f"音（種別未確定） {direction} {band}".strip())
        if event.combat.combat_active_hint:
            parts.append("交戦中っぽい")
        return "、".join(parts)

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
            label_ja = self._resolve_hearing_mob_label(raw, getattr(sound, "sound_event", None))
            key = f"ambient:{mob_type or raw}:{direction}:{band}"
            by_key[key] = RecentHearingMemo(
                kind="ambient",
                mob_type=mob_type,
                label_ja=label_ja,
                direction=direction,
                distance_band=band,
                heard_at=now,
                dedupe_key=key,
            )

        # 新しい順に上限
        memos = sorted(by_key.values(), key=lambda m: m.heard_at, reverse=True)[:12]
        self.state.recent_hearing_memos = memos

    def _player_chat_hearing_summary(self, event: GameEvent) -> str:
        """今フレーム + 直近バッファの音要約。種名はカタログ解決できたものだけ。"""
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
            label_ja = self._resolve_hearing_mob_label(raw, getattr(sound, "sound_event", None))
            key = f"ambient:{label_ja or raw}:{direction}:{band}"
            if label_ja:
                _add_line(key, f"{label_ja}っぽい声 {direction} {band}".strip())
            else:
                _add_line(key, f"なにかの声 {direction} {band}".strip())

        # 2) 直近バッファ（今フレームで埋まらなかった分）
        for memo in self.state.recent_hearing_memos:
            if len(parts) >= 6:
                break
            age = self._recent_ms(now, memo.heard_at)
            if age is None or age > retention_ms:
                continue
            if memo.dedupe_key in seen_keys:
                continue
            if memo.label_ja:
                if memo.kind == "hostile":
                    line = f"{memo.label_ja}の音 {memo.direction} {memo.distance_band}（ついさっき）".strip()
                else:
                    line = f"{memo.label_ja}っぽい声 {memo.direction} {memo.distance_band}（ついさっき）".strip()
            else:
                line = f"音（種別未確定） {memo.direction} {memo.distance_band}（ついさっき）".strip()
            _add_line(memo.dedupe_key, line)

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
            if age is not None and age <= retention_ms:
                _add(memo.label_ja)
        return names

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
