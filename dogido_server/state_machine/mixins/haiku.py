# state_machine/mixins/haiku.py
from __future__ import annotations

import logging
from datetime import datetime
from math import inf

from dogido_server.entry_catalog import mob_poetic_tags
from dogido_server.llm import StructuredGenerationRequest
from dogido_server.llm.sanitize import summarize_for_log
from dogido_server.models import EventName, GameEvent, NearbyResource
from dogido_server.state_machine.haiku_catalog import HaikuFallbackContext, resolve_fallback_haiku
from dogido_server.state_machine.haiku_context import HaikuContext, HaikuFeature, IronyContext
from dogido_server.state_machine.constants import *  # noqa: F403

LOGGER = logging.getLogger("uvicorn.error")


class HaikuMixin:
    def _should_emit_haiku(self, event: GameEvent, now: datetime) -> bool:
        if event.event.name != EventName.STATUS_SNAPSHOT:
            return False
        if self.state.haiku_emitted_this_cycle:
            return False
        if event.visual_threats or event.auditory_threats or self.player_input.should_block_ambient:
            return False
        if self.state.pending_special_biome_line is not None:
            return False
        quiet_ms = self._recent_ms(now, self.state.last_non_silent_at)
        if quiet_ms is None:
            return False
        return quiet_ms >= self.settings.haiku_silence_time_ms

    def _emit_haiku_line(self, event: GameEvent, now: datetime) -> str | None:
        if not self._should_emit_haiku(event, now):
            return None
        self.state.haiku_emitted_this_cycle = True
        line = self._format_haiku_line(self._render_haiku_line(event))
        LOGGER.warning("haiku_emit result=emitted text=%s", summarize_for_log(line))
        return line

    def _render_haiku_line(self, event: GameEvent) -> str:
        context = self._haiku_context(event)
        irony = self._detect_haiku_irony(context)
        fallback_text = self._fallback_haiku_line(event)
        skip_reason = self._haiku_llm_skip_reason(context, irony)
        if skip_reason is not None:
            LOGGER.warning(
                "haiku_decision result=fallback reason=%s text=%s",
                skip_reason,
                summarize_for_log(fallback_text),
            )
            return fallback_text
        line = self._generate_leaf_text(
            kind="haiku",
            fallback_text=fallback_text,
            details=context.prompt_details(irony),
            temperature=0.82,
            route="haiku",
        )
        if line == fallback_text:
            LOGGER.warning(
                "haiku_decision result=fallback reason=llm_rejected text=%s",
                summarize_for_log(fallback_text),
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
            peaceful_mob_types=frozenset(mob.type for mob in event.peaceful_mobs if mob.type),
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

    def _detect_haiku_irony(self, context: HaikuContext) -> IronyContext:
        if self.llm is None:
            return IronyContext()
        payload = self.llm.generate_structured_json(
            StructuredGenerationRequest(
                kind="haiku_irony",
                fallback_value={"found": False},
                details=context.irony_details(),
                temperature=0.15,
                route="chat",
            )
        )
        return IronyContext.from_mapping(payload)

    def _haiku_context(self, event: GameEvent) -> HaikuContext:
        time_phase = getattr(event.world.time_phase, "value", event.world.time_phase) or "unknown"
        weather = self._weather_value(event.world.weather) or "unknown"
        z_value = event.player.position.z
        biome = event.world.biome
        held_item = self._item_label(event.player.held_item)
        inventory_items = tuple(self._haiku_inventory_values(event.inventory))
        nearby_blocks = tuple(self._haiku_nearby_block_values(event.nearby_resources))
        peaceful_mobs = tuple(self._haiku_peaceful_mob_values(event))
        feature_candidates = tuple(
            self._haiku_feature_candidates(
                event,
                held_item=held_item,
                inventory_items=inventory_items,
                nearby_blocks=nearby_blocks,
                peaceful_mobs=peaceful_mobs,
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
            nearby_blocks=nearby_blocks,
            peaceful_mobs=peaceful_mobs,
            haiku_tags=tuple(self._haiku_tags(event, feature_candidates)),
            feature_candidates=feature_candidates,
            candidate_tensions=tuple(self._haiku_candidate_tensions(event, held_item, peaceful_mobs, nearby_blocks)),
        )

    def _haiku_feature_candidates(
        self,
        event: GameEvent,
        *,
        held_item: str,
        inventory_items: tuple[str, ...],
        nearby_blocks: tuple[str, ...],
        peaceful_mobs: tuple[str, ...],
    ) -> list[HaikuFeature]:
        time_phase = getattr(event.world.time_phase, "value", event.world.time_phase) or "unknown"
        weather = self._weather_value(event.world.weather) or "unknown"
        z_value = event.player.position.z
        candidates = [
            HaikuFeature("バイオーム", "biome", self._biome_label(event.world.biome)),
            HaikuFeature("地帯", "biome_group", self._biome_group_label(event.world.biome) or "不明"),
            HaikuFeature("Z座標", "z_value", str(int(round(z_value)) if z_value is not None else 0)),
            HaikuFeature("天気", "weather", WEATHER_LABELS.get(weather, "不明")),
            HaikuFeature("時間", "time_phase", TIME_PHASE_LABELS.get(time_phase, "不明")),
        ]
        candidates.extend(
            HaikuFeature("地形", f"trait_{index}", trait)
            for index, trait in enumerate(self._haiku_biome_traits(event.world.biome)[:4], start=1)
        )
        if held_item:
            candidates.append(HaikuFeature("手持ち", "held_item", held_item))
        candidates.extend(
            HaikuFeature("持ち物", f"inventory_{index}", label)
            for index, label in enumerate(inventory_items[:4], start=1)
        )
        candidates.extend(
            HaikuFeature("周辺", f"nearby_{index}", label)
            for index, label in enumerate(nearby_blocks[:4], start=1)
        )
        for index, mob_label in enumerate(peaceful_mobs[:3], start=1):
            candidates.append(
                HaikuFeature(
                    "Mob",
                    f"mob_{index}",
                    mob_label,
                    tags=mob_poetic_tags(self._peaceful_mob_type_for_label(event, mob_label)),
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

    def _haiku_inventory_values(self, inventory: dict[str, int]) -> list[str]:
        items = sorted(inventory.items(), key=lambda entry: (-entry[1], entry[0]))
        values: list[str] = []
        for item_id, count in items:
            if count <= 0:
                continue
            label = self._item_label(item_id)
            if not label:
                continue
            values.append(label)
            if len(values) >= 6:
                break
        return values

    def _haiku_nearby_block_values(self, resources: list[NearbyResource]) -> list[str]:
        values: list[str] = []
        seen: set[str] = set()
        for resource in sorted(resources, key=lambda candidate: candidate.distance or inf):
            label = self._block_label(resource.name)
            if not label or label in seen:
                continue
            seen.add(label)
            values.append(label)
            if len(values) >= 6:
                break
        return values

    def _haiku_peaceful_mob_values(self, event: GameEvent) -> list[str]:
        values: list[str] = []
        seen: set[str] = set()
        for mob in event.peaceful_mobs:
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
        for mob in event.peaceful_mobs[:4]:
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
        peaceful_mobs: tuple[str, ...],
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
        if biome_group_id == "dry" and any(label in {"熱帯魚", "イカ", "フグ", "サケ", "タラ"} for label in peaceful_mobs):
            tensions.append(f"{biome_label}なのに水のいきものがおる")
        if any(label == "熱帯魚" for label in peaceful_mobs) and "ocean" not in biome:
            tensions.append("海やないのに熱帯魚がおる")
        if any(label == "ヒツジ" for label in peaceful_mobs) and biome not in {"plains", "savanna", "meadow"}:
            tensions.append(f"{biome_label}やのにヒツジがのんびりしとる")
        if "シラカバの葉" in nearby_blocks and not biome.startswith("birch_") and biome != "old_growth_birch_forest":
            tensions.append(f"{biome_label}やのにシラカバの気配がある")
        if event.player.position.y is not None and event.player.position.y <= 16:
            tensions.append("深い地下でダイヤを夢みとる")
            if held_item:
                tensions.append(f"深い地下なのに手には{held_item}がある")
        if biome == "mushroom_fields":
            tensions.append("安全すぎて逆に妙や")
        if time_phase == "night" and peaceful_mobs:
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

    def _peaceful_mob_type_for_label(self, event: GameEvent, label: str) -> str | None:
        for mob in event.peaceful_mobs:
            if self._mob_label(mob.type) == label:
                return mob.type
        return None

    def _should_use_llm_haiku(self, context: HaikuContext, irony: IronyContext) -> bool:
        return self._haiku_llm_skip_reason(context, irony) is None

    def _haiku_llm_skip_reason(self, context: HaikuContext, irony: IronyContext) -> str | None:
        if self.llm is None:
            return "llm_unavailable"
        if irony.found:
            return None
        concrete_subjects = {
            label
            for label in (
                *context.nearby_blocks,
                *context.peaceful_mobs,
            )
            if label
        }
        if context.held_item and context.held_item != "なし":
            concrete_subjects.add(context.held_item)
        if len(concrete_subjects) < 2:
            return "weak_scene"
        return None
