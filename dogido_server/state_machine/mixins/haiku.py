# state_machine/mixins/haiku.py
from __future__ import annotations

from dataclasses import dataclass
import logging
from datetime import datetime
from math import inf

from dogido_server.entry_catalog import block_entry, item_entry, mob_poetic_tags
from dogido_server.llm.client import STRUCTURED_STATUS_KEY
from dogido_server.llm import StructuredGenerationRequest
from dogido_server.llm.sanitize import summarize_for_log
from dogido_server.models import EventName, GameEvent, NearbyResource
from dogido_server.state_machine.haiku_catalog import (
    HaikuFallbackContext,
    resolve_fallback_haiku,
    resolve_llm_failed_haiku,
)
from dogido_server.state_machine.haiku_context import HaikuContext, HaikuFeature, IronyContext, SceneContext
from dogido_server.state_machine.constants import *  # noqa: F403

LOGGER = logging.getLogger("uvicorn.error")


@dataclass(frozen=True, slots=True)
class _InventoryPoemCandidate:
    label: str
    section: str
    group_path: tuple[str, ...]
    count: int
    order: int


@dataclass(frozen=True, slots=True)
class _HaikuNounFamily:
    key: str
    category: str
    item_suffixes: tuple[str, ...]
    label_markers: tuple[str, ...]
    allowed_terms: tuple[str, ...]
    forbidden_terms: tuple[str, ...]


_HAIKU_NOUN_FAMILIES: tuple[_HaikuNounFamily, ...] = (
    _HaikuNounFamily(
        key="shovel",
        category="tool",
        item_suffixes=("shovel",),
        label_markers=("シャベル", "しゃべる"),
        allowed_terms=("しゃべる",),
        forbidden_terms=("つるはし", "おの", "くわ"),
    ),
    _HaikuNounFamily(
        key="pickaxe",
        category="tool",
        item_suffixes=("pickaxe",),
        label_markers=("ツルハシ", "つるはし"),
        allowed_terms=("つるはし",),
        forbidden_terms=("しゃべる", "おの", "くわ"),
    ),
    _HaikuNounFamily(
        key="axe",
        category="tool",
        item_suffixes=("axe",),
        label_markers=("斧", "オノ", "おの"),
        allowed_terms=("おの",),
        forbidden_terms=("しゃべる", "つるはし", "くわ"),
    ),
    _HaikuNounFamily(
        key="hoe",
        category="tool",
        item_suffixes=("hoe",),
        label_markers=("クワ", "くわ"),
        allowed_terms=("くわ",),
        forbidden_terms=("しゃべる", "つるはし", "おの"),
    ),
)


class HaikuMixin:
    def _uses_prefaced_haiku_generation(self) -> bool:
        return self.settings.llm_enabled and self.llm is not None

    def _should_emit_haiku(self, event: GameEvent, now: datetime) -> bool:
        if event.event.name != EventName.STATUS_SNAPSHOT:
            return False
        if self.state.mode != "normal":
            return False
        if event.visual_threats or event.auditory_threats or self.player_input.should_block_ambient:
            return False
        if self.state.pending_special_biome_line is not None:
            return False
        interval_ms = self._recent_ms(now, self.state.last_haiku_emitted_at)
        if interval_ms is None or interval_ms < self.settings.haiku_interval_ms:
            return False
        quiet_ms = self._recent_ms(now, self.state.last_non_silent_at)
        if quiet_ms is None:
            return False
        return quiet_ms >= self.settings.haiku_quiet_time_ms

    def _haiku_block_reason(self, event: GameEvent, now: datetime) -> str | None:
        if self.state.mode != "normal":
            return f"mode_{self.state.mode}"
        if event.visual_threats:
            return "visual_threats"
        if event.auditory_threats:
            return "auditory_threats"
        if self.player_input.should_block_ambient:
            return "player_input"
        if self._player_input_priority_active(now):
            return "player_input_priority"
        if self.state.pending_special_biome_line is not None:
            return "pending_biome_line"
        quiet_ms = self._recent_ms(now, self.state.last_non_silent_at)
        if quiet_ms is not None and quiet_ms < self.settings.haiku_quiet_time_ms:
            return "quiet_not_reached"
        return None

    def _log_haiku_block_state(self, event: GameEvent, now: datetime) -> None:
        """川柳の周期が満ちているのに出ない理由を60秒に1回ログへ残す（デバッグ用）。"""
        if event.event.name != EventName.STATUS_SNAPSHOT:
            return
        if self.state.pending_haiku_after_preface:
            return
        interval_ms = self._recent_ms(now, self.state.last_haiku_emitted_at)
        if interval_ms is None or interval_ms < self.settings.haiku_interval_ms:
            return
        reason = self._haiku_block_reason(event, now)
        if reason is None:
            return
        recent_log_ms = self._recent_ms(now, self.state.last_haiku_block_log_at)
        if recent_log_ms is not None and recent_log_ms < 60000:
            return
        self.state.last_haiku_block_log_at = now
        quiet_ms = self._recent_ms(now, self.state.last_non_silent_at)
        LOGGER.warning(
            "haiku_block reason=%s mode=%s light=%s visual=%d audio=%d quiet_ms=%s overdue_ms=%d",
            reason,
            self.state.mode,
            event.world.local_light,
            len(event.visual_threats),
            len(event.auditory_threats),
            quiet_ms,
            interval_ms - self.settings.haiku_interval_ms,
        )

    def _should_complete_prefaced_haiku(self, event: GameEvent) -> bool:
        if not self.state.pending_haiku_after_preface:
            return False
        if event.event.name != EventName.STATUS_SNAPSHOT:
            return False
        if self.state.mode != "normal":
            return False
        if event.visual_threats or event.auditory_threats or self.player_input.should_block_ambient:
            return False
        if self.state.pending_special_biome_line is not None:
            return False
        return True

    def _emit_haiku_line(self, event: GameEvent, now: datetime) -> str | None:
        if self._should_complete_prefaced_haiku(event):
            self.state.pending_haiku_after_preface = False
            self.state.last_haiku_emitted_at = now
            line = self._render_haiku_line(event).strip()
            LOGGER.warning(
                "haiku_emit result=emitted text=%s",
                summarize_for_log(self._format_haiku_line(line)),
            )
            return line or "まとまらんかった。。。"
        if not self._should_emit_haiku(event, now):
            return None
        if self._uses_prefaced_haiku_generation():
            self.state.pending_haiku_after_preface = True
            LOGGER.warning("haiku_emit result=preface text=%s", summarize_for_log("ここで一句。"))
            return "ここで一句。"
        self.state.last_haiku_emitted_at = now
        line = self._format_haiku_line(self._render_haiku_line(event))
        LOGGER.warning("haiku_emit result=emitted text=%s", summarize_for_log(line))
        return line

    def _render_haiku_line(self, event: GameEvent) -> str:
        context = self._haiku_context(event)
        irony, irony_status = self._detect_haiku_irony(context)
        scene, scene_status = self._detect_haiku_scene(context, irony)
        fallback_text = self._fallback_haiku_line(event)
        llm_failed_text = self._llm_failed_haiku_line()
        skip_reason = self._haiku_llm_skip_reason(context, irony, scene)
        if skip_reason is not None:
            if self._should_use_llm_failed_haiku(skip_reason, irony_status, scene_status):
                LOGGER.warning(
                    "haiku_decision result=fallback reason=%s text=%s",
                    self._haiku_llm_failure_reason(skip_reason, irony_status, scene_status),
                    summarize_for_log(llm_failed_text),
                )
                return llm_failed_text
            LOGGER.warning(
                "haiku_decision result=fallback reason=%s text=%s",
                skip_reason,
                summarize_for_log(fallback_text),
            )
            return fallback_text
        prompt_details = context.prompt_details(irony, scene)
        prompt_details["haiku_constraints"] = self._haiku_constraint_details(event, scene)
        line = self._generate_leaf_text(
            kind="haiku",
            fallback_text=llm_failed_text,
            details=prompt_details,
            temperature=0.82,
            route="haiku",
        )
        if line == llm_failed_text:
            LOGGER.warning(
                "haiku_decision result=fallback reason=llm_rejected text=%s",
                summarize_for_log(llm_failed_text),
            )
        return line

    def _format_haiku_line(self, text: str) -> str:
        stripped = text.strip()
        if not stripped:
            return "ここで一句。"
        separator = "\n" if "\n" in stripped else " "
        return f"ここで一句。{separator}{stripped}"

    def _fallback_haiku_line(self, event: GameEvent) -> str:
        context = HaikuFallbackContext(
            biome=self._normalized_biome(event.world.biome),
            time_phase=getattr(event.world.time_phase, "value", event.world.time_phase),
            weather=self._weather_value(event.world.weather),
            player_y=event.player.position.y,
            danger_darkness_score=event.world.danger_darkness_score,
            visual_threat_types=frozenset(threat.type for threat in event.visual_threats if threat.type),
            passive_mob_types=frozenset(mob.type for mob in event.passive_mobs if mob.type),
            nearby_resources=tuple(
                (
                    resource.name.split(":")[-1].strip().lower(),
                    resource.distance,
                )
                for resource in event.nearby_resources
                if resource.name
            ),
        )
        return resolve_fallback_haiku(context)

    def _llm_failed_haiku_line(self) -> str:
        return resolve_llm_failed_haiku()

    def _structured_status(self, payload: dict[str, object] | None) -> str:
        if not isinstance(payload, dict):
            return "invalid_payload"
        status = payload.get(STRUCTURED_STATUS_KEY)
        return str(status or "accepted")

    def _detect_haiku_irony(self, context: HaikuContext) -> tuple[IronyContext, str]:
        if self.llm is None:
            return IronyContext(), "unavailable"
        payload = self.llm.generate_structured_json(
            StructuredGenerationRequest(
                kind="haiku_irony",
                fallback_value={"found": False},
                details=context.irony_details(),
                temperature=0.15,
                route="chat",
                max_tokens=self.settings.haiku_structured_max_tokens,
            )
        )
        return IronyContext.from_mapping(payload), self._structured_status(payload)

    def _detect_haiku_scene(self, context: HaikuContext, irony: IronyContext) -> tuple[SceneContext, str]:
        if self.llm is None:
            return SceneContext(), "unavailable"
        payload = self.llm.generate_structured_json(
            StructuredGenerationRequest(
                kind="haiku_scene",
                fallback_value={"found": False},
                details=context.scene_details(irony),
                temperature=0.2,
                route="chat",
                max_tokens=self.settings.haiku_structured_max_tokens,
            )
        )
        return SceneContext.from_mapping(payload), self._structured_status(payload)

    def _haiku_context(self, event: GameEvent) -> HaikuContext:
        time_phase = getattr(event.world.time_phase, "value", event.world.time_phase) or "unknown"
        weather = self._weather_value(event.world.weather) or "unknown"
        z_value = event.player.position.z
        biome = event.world.biome
        held_item = self._item_label(event.player.held_item)
        inventory_close_pair, inventory_far_item, inventory_items = self._haiku_inventory_values(
            event.inventory,
            held_item_id=event.player.held_item,
        )
        nearby_blocks = tuple(self._haiku_nearby_block_values(event.nearby_resources))
        passive_mobs = tuple(self._haiku_passive_mob_values(event))
        feature_candidates = tuple(
            self._haiku_feature_candidates(
                event,
                held_item=held_item,
                inventory_items=inventory_items,
                nearby_blocks=nearby_blocks,
                passive_mobs=passive_mobs,
            )
        )
        return HaikuContext(
            player_name=self._player_call_name(event),
            biome_id=self._normalized_biome(biome) or "unknown",
            biome_label=self._biome_label(biome),
            biome_group=self._biome_group_label(biome) or "不明",
            biome_traits=tuple(self._haiku_biome_traits(biome)),
            time_phase=time_phase,
            time_label=TIME_PHASE_LABELS.get(time_phase, "不明"),
            weather=weather,
            weather_label=WEATHER_LABELS.get(weather, "不明"),
            z_value=int(round(z_value)) if z_value is not None else 0,
            held_item=held_item,
            inventory_items=inventory_items,
            inventory_close_pair=inventory_close_pair,
            inventory_far_item=inventory_far_item,
            nearby_blocks=nearby_blocks,
            passive_mobs=passive_mobs,
            haiku_tags=tuple(self._haiku_tags(event, feature_candidates)),
            feature_candidates=feature_candidates,
            candidate_tensions=tuple(self._haiku_candidate_tensions(event, held_item, passive_mobs, nearby_blocks)),
        )

    def _haiku_feature_candidates(
        self,
        event: GameEvent,
        *,
        held_item: str,
        inventory_items: tuple[str, ...],
        nearby_blocks: tuple[str, ...],
        passive_mobs: tuple[str, ...],
    ) -> list[HaikuFeature]:
        time_phase = getattr(event.world.time_phase, "value", event.world.time_phase) or "unknown"
        weather = self._weather_value(event.world.weather) or "unknown"
        z_value = event.player.position.z
        portal_type = (event.world.nearby_portal_type or "").strip().lower()
        candidates = []
        if portal_type:
            portal_labels = {
                "nether_portal": "ネザーポータル",
                "end_portal": "エンドポータル",
                "end_gateway": "エンドゲートウェイ",
            }
            candidates.append(HaikuFeature(
                "ポータル", "portal", portal_labels.get(portal_type, portal_type),
                tags=frozenset({"異世界", "ワープ", "光", "不思議"}),
            ))
        candidates.extend([
            HaikuFeature("バイオーム", "biome", self._biome_label(event.world.biome)),
            HaikuFeature("地帯", "biome_group", self._biome_group_label(event.world.biome) or "不明"),
            HaikuFeature("Z座標", "z_value", str(int(round(z_value)) if z_value is not None else 0)),
            HaikuFeature("天気", "weather", WEATHER_LABELS.get(weather, "不明")),
            HaikuFeature("時間", "time_phase", TIME_PHASE_LABELS.get(time_phase, "不明")),
        ])
        candidates.extend(
            HaikuFeature("地形", f"trait_{index}", trait)
            for index, trait in enumerate(self._haiku_biome_traits(event.world.biome)[:4], start=1)
        )
        if held_item:
            candidates.append(HaikuFeature("手持ち", "held_item", held_item))
        candidates.extend(
            HaikuFeature("周辺", f"nearby_{index}", label)
            for index, label in enumerate(nearby_blocks[:4], start=1)
        )
        for index, mob_label in enumerate(passive_mobs[:3], start=1):
            candidates.append(
                HaikuFeature(
                    "Mob",
                    f"mob_{index}",
                    mob_label,
                    tags=mob_poetic_tags(self._passive_mob_type_for_label(event, mob_label)),
                )
            )
        return candidates[:14]

    def _haiku_biome_traits(self, biome: str | None) -> list[str]:
        traits: list[str] = []
        temperature = self._biome_temperature(biome)
        downfall = self._biome_downfall(biome)
        snow_start_y = self._biome_snow_start_y(biome)
        if temperature is not None:
            traits.append(f"気温 {temperature:g}")
        if downfall is not None:
            traits.append(f"降水 {downfall:g}")
        if snow_start_y is not None:
            traits.append(f"雪は Y{snow_start_y}から")
        return traits

    def _haiku_inventory_values(
        self,
        inventory: dict[str, int],
        *,
        held_item_id: str | None = None,
    ) -> tuple[tuple[str, ...], str, tuple[str, ...]]:
        items = sorted(inventory.items(), key=lambda entry: (-entry[1], entry[0]))
        held_normalized = str(held_item_id or "").split(":")[-1].strip().lower()
        seen: set[str] = set()
        candidates: list[_InventoryPoemCandidate] = []
        for order, (item_id, count) in enumerate(items):
            if count <= 0:
                continue
            normalized = str(item_id).split(":")[-1].strip().lower()
            if normalized == held_normalized:
                continue
            label = self._item_label(item_id)
            if not label:
                continue
            if label in seen:
                continue
            seen.add(label)
            entry = item_entry(item_id) or {}
            candidates.append(
                _InventoryPoemCandidate(
                    label=label,
                    section=str(entry.get("section") or ""),
                    group_path=tuple(str(value) for value in entry.get("group_path") or [] if value),
                    count=count,
                    order=order,
                )
            )

        if not candidates:
            return tuple(), "", tuple()
        if len(candidates) == 1:
            only = candidates[0].label
            return tuple(), "", (only,)

        best_pair: tuple[_InventoryPoemCandidate, _InventoryPoemCandidate] | None = None
        best_score: tuple[int, int, int] | None = None
        for left_index, left in enumerate(candidates[:-1]):
            for right in candidates[left_index + 1:]:
                score = (
                    self._haiku_inventory_similarity(left, right),
                    left.count + right.count,
                    -min(left.order, right.order),
                )
                if best_score is None or score > best_score:
                    best_score = score
                    best_pair = (left, right)

        if best_pair is None:
            selected = tuple(candidate.label for candidate in candidates[:3])
            return tuple(selected[:2]), "", selected

        close_pair = (best_pair[0].label, best_pair[1].label)
        remaining = [candidate for candidate in candidates if candidate not in best_pair]
        far_item = ""
        if remaining:
            outlier = min(
                remaining,
                key=lambda candidate: (
                    max(
                        self._haiku_inventory_similarity(candidate, best_pair[0]),
                        self._haiku_inventory_similarity(candidate, best_pair[1]),
                    ),
                    -candidate.count,
                    candidate.order,
                ),
            )
            far_item = outlier.label
        selected_items = close_pair if not far_item else (*close_pair, far_item)
        return close_pair, far_item, tuple(selected_items)

    def _haiku_inventory_similarity(
        self,
        left: _InventoryPoemCandidate,
        right: _InventoryPoemCandidate,
    ) -> int:
        shared_prefix = 0
        for left_part, right_part in zip(left.group_path, right.group_path):
            if left_part != right_part:
                break
            shared_prefix += 1
        score = shared_prefix * 3
        if left.section and left.section == right.section:
            score += 4
        return score

    def _haiku_nearby_block_values(self, resources: list[NearbyResource]) -> list[str]:
        natural_values: list[str] = []
        other_values: list[str] = []
        seen: set[str] = set()
        for resource in sorted(resources, key=lambda candidate: candidate.distance or inf):
            label = self._block_label(resource.name)
            if not label or label in seen:
                continue
            seen.add(label)
            entry = block_entry(resource.name) or {}
            target = natural_values if entry.get("section") == "natural_blocks" else other_values
            target.append(label)
            if len(natural_values) + len(other_values) >= 6:
                break
        return natural_values + other_values

    def _haiku_passive_mob_values(self, event: GameEvent) -> list[str]:
        values: list[str] = []
        seen: set[str] = set()
        for mob in event.passive_mobs:
            label = self._mob_label(mob.type)
            if not label or label in seen:
                continue
            seen.add(label)
            values.append(label)
            if len(values) >= 4:
                break
        return values

    def _haiku_tags(self, event: GameEvent, features: tuple[HaikuFeature, ...]) -> list[str]:
        tags: list[str] = []
        for feature in features:
            tags.extend(feature.tags)
        for mob in event.passive_mobs[:4]:
            tags.extend(mob_poetic_tags(mob.type))
        seen: set[str] = set()
        result: list[str] = []
        for tag in tags:
            if not tag or tag in seen:
                continue
            seen.add(tag)
            result.append(tag)
            if len(result) >= 16:
                break
        return result

    def _haiku_candidate_tensions(
        self,
        event: GameEvent,
        held_item: str,
        passive_mobs: tuple[str, ...],
        nearby_blocks: tuple[str, ...],
    ) -> list[str]:
        tensions: list[str] = []
        biome = self._normalized_biome(event.world.biome) or "unknown"
        biome_label = self._biome_label(event.world.biome)
        biome_entry = self._biome_entry(event.world.biome) or {}
        biome_group_id = str(biome_entry.get("group_id") or "")
        time_phase = getattr(event.world.time_phase, "value", event.world.time_phase) or "unknown"
        weather = self._weather_value(event.world.weather) or "unknown"

        if biome_group_id == "dry" and weather in {"rain", "thunder"}:
            tensions.append("乾いた土地やのに空だけ荒れとる")
        if biome_group_id == "dry" and any(label in {"熱帯魚", "イカ", "フグ", "サケ", "タラ"} for label in passive_mobs):
            tensions.append(f"{biome_label}なのに水のいきものがおる")
        if any(label == "熱帯魚" for label in passive_mobs) and "ocean" not in biome:
            tensions.append("海やないのに熱帯魚がおる")
        if any(label == "ヒツジ" for label in passive_mobs) and biome not in {"plains", "savanna", "meadow"}:
            tensions.append(f"{biome_label}やのにヒツジがのんびりしとる")
        if "シラカバの葉" in nearby_blocks and not biome.startswith("birch_") and biome != "old_growth_birch_forest":
            tensions.append(f"{biome_label}やのにシラカバの気配がある")
        if event.player.position.y is not None and event.player.position.y <= 16:
            tensions.append("深い地下でダイヤを夢みとる")
            if held_item:
                tensions.append(f"深い地下なのに手には{held_item}がある")
        if biome == "mushroom_fields":
            tensions.append("安全すぎて逆に妙や")
        if time_phase == "night" and passive_mobs:
            tensions.append("夜やのにのどかな気配が残っとる")
        if time_phase == "day" and event.player.position.y is not None and event.player.position.y <= 16:
            tensions.append("昼やのに地の底みたいや")
        seen: set[str] = set()
        result: list[str] = []
        for tension in tensions:
            if not tension or tension in seen:
                continue
            seen.add(tension)
            result.append(tension)
            if len(result) >= 8:
                break
        return result

    def _passive_mob_type_for_label(self, event: GameEvent, label: str) -> str | None:
        for mob in event.passive_mobs:
            if self._mob_label(mob.type) == label:
                return mob.type
        return None

    def _should_use_llm_haiku(
        self,
        context: HaikuContext,
        irony: IronyContext,
        scene: SceneContext,
    ) -> bool:
        return self._haiku_llm_skip_reason(context, irony, scene) is None

    def _haiku_llm_skip_reason(
        self,
        context: HaikuContext,
        irony: IronyContext,
        scene: SceneContext,
    ) -> str | None:
        if self.llm is None:
            return "llm_unavailable"
        scene_strength = self._haiku_scene_strength(context)
        if scene.found and scene_strength >= 3:
            return None
        if irony.found and scene_strength >= 4:
            return None
        if scene_strength < 4:
            return "weak_scene"
        return None

    def _should_use_llm_failed_haiku(
        self,
        skip_reason: str,
        irony_status: str,
        scene_status: str,
    ) -> bool:
        if skip_reason == "llm_unavailable":
            return False
        failure_statuses = {"invalid_json", "generation_error", "invalid_payload"}
        return irony_status in failure_statuses or scene_status in failure_statuses

    def _haiku_llm_failure_reason(
        self,
        skip_reason: str,
        irony_status: str,
        scene_status: str,
    ) -> str:
        if irony_status != "accepted":
            return f"{skip_reason}:{irony_status}"
        if scene_status != "accepted":
            return f"{skip_reason}:{scene_status}"
        return skip_reason

    def _haiku_scene_strength(self, context: HaikuContext) -> int:
        score = 0
        if context.passive_mobs:
            score += 3
        if context.nearby_blocks:
            score += 3
        if context.biome_id != "unknown" or context.weather != "unknown":
            score += 2
        if (
            (context.held_item and context.held_item != "なし")
            or context.inventory_items
        ):
            score += 1
        return score

    def _haiku_constraint_details(self, event: GameEvent, scene: SceneContext) -> dict[str, object] | None:
        families = self._haiku_selected_noun_families(event, scene)
        if not families:
            return None
        allowed_terms: list[str] = []
        forbidden_terms: list[str] = []
        seen_allowed: set[str] = set()
        seen_forbidden: set[str] = set()
        for family in families:
            for term in family.allowed_terms:
                if term and term not in seen_allowed:
                    seen_allowed.add(term)
                    allowed_terms.append(term)
            for term in family.forbidden_terms:
                if term and term not in seen_forbidden:
                    seen_forbidden.add(term)
                    forbidden_terms.append(term)
        if not allowed_terms and not forbidden_terms:
            return None
        return {
            "allowed_terms": allowed_terms,
            "forbidden_terms": forbidden_terms,
        }

    def _haiku_selected_noun_families(self, event: GameEvent, scene: SceneContext) -> tuple[_HaikuNounFamily, ...]:
        selected: list[_HaikuNounFamily] = []
        seen: set[str] = set()
        held_item_id = str(event.player.held_item or "").split(":")[-1].strip().lower()
        for family in _HAIKU_NOUN_FAMILIES:
            if any(held_item_id.endswith(suffix) for suffix in family.item_suffixes):
                if family.key not in seen:
                    seen.add(family.key)
                    selected.append(family)
        for motif in scene.motifs:
            motif_text = str(motif or "")
            for family in _HAIKU_NOUN_FAMILIES:
                if family.key in seen:
                    continue
                if any(marker and marker in motif_text for marker in family.label_markers):
                    seen.add(family.key)
                    selected.append(family)
        return tuple(selected)
