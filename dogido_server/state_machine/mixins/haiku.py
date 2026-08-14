# state_machine/mixins/haiku.py
from __future__ import annotations

from dataclasses import dataclass
import logging
from datetime import datetime
from math import inf

from dogido_server.entry_catalog import block_entry, item_entry, mob_entry, mob_poetic_line, mob_poetic_tags
from dogido_server.haiku.generation import generate_grounded_haiku
from dogido_server.haiku.materials import attach_fragment_links, build_workshop_materials_seed
from dogido_server.haiku.preface import validate_preface_clauses
from dogido_server.haiku.source_atoms import (
    CatalogSourceSnapshot,
    HaikuSourceAtom,
    atoms_from_catalog_sources,
    atoms_from_observations,
    atoms_from_preface_clauses,
    catalog_notes_projection,
    catalog_source_snapshot,
    merge_source_atoms,
)
from dogido_server.llm.client import STRUCTURED_STATUS_KEY
from dogido_server.llm import StructuredGenerationRequest
from dogido_server.llm.sanitize import summarize_for_log
from dogido_server.memory_types import HaikuEmission
from dogido_server.models import EventName, GameEvent, NearbyResource
from dogido_server.minecraft_ids import normalize_minecraft_id
from dogido_server.player_activity import player_vehicle_fact
from dogido_server.state_machine.haiku_catalog import (
    HaikuFallbackContext,
    resolve_fallback_haiku,
    resolve_llm_failed_haiku,
)
from dogido_server.state_machine.haiku_context import HaikuContext, HaikuFeature, IronyContext, SceneContext
from dogido_server.state_machine.precipitation import PrecipitationContext
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
        # 脅威は state_updates で prep を消す。雑談では自分の世界を中断しない。
        if event.visual_threats or event.auditory_threats:
            return False
        # pending_special_biome_line 等の ambient 待ちで本句を止めない。
        # 止めると pending_haiku が張り付き、player 入力が永久 hold される。
        # environmental 側は本句を biome 入場コメントより先に出す。
        return True

    def _emit_haiku_line(self, event: GameEvent, now: datetime) -> str | None:
        if self._should_complete_prefaced_haiku(event):
            return self._complete_prefaced_haiku(event, now)
        if not self._should_emit_haiku(event, now):
            return None
        if self._uses_prefaced_haiku_generation():
            return self._begin_prefaced_haiku(event, now)
        self.state.last_haiku_emitted_at = now
        raw_line = self._render_haiku_line(event)
        # LLM は有効だが品質ゲートを通せなかった場合、失敗定型を句として
        # workshop pin・長期保存しない。preface 経路と同じ境界にそろえる。
        if not self._is_llm_failed_haiku_text(raw_line):
            self._remember_haiku_emission(event, now, raw_line, route="haiku")
        else:
            LOGGER.warning(
                "haiku_emit result=failed_no_pin text=%s",
                summarize_for_log(raw_line),
            )
        line = self._format_haiku_line(raw_line)
        LOGGER.warning("haiku_emit result=emitted text=%s", summarize_for_log(line))
        return line

    def _begin_prefaced_haiku(self, event: GameEvent, now: datetime) -> str:
        """見どころ + ここで一句。irony/scene はここで回し、本句は次フレーム。"""
        context = self._haiku_context(event)
        irony, _ = self._detect_haiku_irony(context)
        scene, _ = self._detect_haiku_scene(context, irony)
        self._pending_haiku_interpretation = self._haiku_interpretation_text(irony, scene)
        spoken = self._compose_haiku_preface_speech(scene)
        source_atoms = merge_source_atoms(
            context.source_atoms,
            atoms_from_preface_clauses(scene.clauses),
        )
        self._stash_haiku_materials_seed(
            event,
            context,
            irony,
            scene,
            source_atoms=source_atoms,
            preface_spoken=spoken,
        )
        fallback_text = self._fallback_haiku_line(event)
        llm_failed_text = self._llm_failed_haiku_line()
        skip_reason = self._haiku_generation_skip_reason()

        self._pending_haiku_prompt_details = None
        self._pending_haiku_source_atoms = source_atoms
        self._pending_haiku_fixed_line = None
        constraints = self._haiku_constraint_details(event, scene)
        if constraints and self._pending_haiku_materials is not None:
            # workshop修正でも、発句時の道具・読みhard制約を同じまま検査する。
            # 現在値で再計算せず、句と一緒にsnapshotする。
            self._pending_haiku_materials["haiku_constraints"] = constraints
        if skip_reason is None:
            prompt_details = context.prompt_details(irony, scene)
            prompt_details["haiku_constraints"] = constraints
            self._pending_haiku_prompt_details = prompt_details
        else:
            # 固定カタログ句は LLM 自体が無い場合だけ使う。scene の契約不合格や
            # 材料の薄さは、下流の source-atom 品質ゲートで fail-closed にする。
            LOGGER.warning(
                "haiku_decision result=fallback reason=%s text=%s",
                skip_reason,
                summarize_for_log(fallback_text),
            )
            self._pending_haiku_fixed_line = fallback_text

        self.state.pending_haiku_after_preface = True
        self.state.pending_haiku_started_at = now
        LOGGER.warning("haiku_emit result=preface text=%s", summarize_for_log(spoken))
        return spoken

    def _complete_prefaced_haiku(self, event: GameEvent, now: datetime) -> str:
        self.state.pending_haiku_after_preface = False
        self.state.pending_haiku_started_at = None
        self.state.last_haiku_emitted_at = now
        details = self._pending_haiku_prompt_details
        source_atoms = self._pending_haiku_source_atoms
        fixed = self._pending_haiku_fixed_line
        self._pending_haiku_prompt_details = None
        self._pending_haiku_source_atoms = ()
        self._pending_haiku_fixed_line = None
        llm_failed_text = self._llm_failed_haiku_line()
        if details is not None:
            generated = generate_grounded_haiku(
                self.llm,
                details=details,
                source_atoms=source_atoms,
                fallback_text=llm_failed_text,
                max_tokens=self.settings.haiku_structured_max_tokens,
                generation_strategy=self.settings.haiku_generation_strategy,
                max_regeneration_rounds=self.settings.haiku_max_regeneration_rounds,
            )
            line = generated.text
            if generated.accepted and self._pending_haiku_materials is not None:
                self._pending_haiku_materials["line_sources"] = list(generated.line_sources)
                self._pending_haiku_materials["generation_strategy"] = generated.generation_strategy
                self._pending_haiku_materials["regeneration_rounds"] = generated.regeneration_rounds
                self._pending_haiku_materials["prompt_variant"] = generated.prompt_variant
            if line == llm_failed_text:
                LOGGER.warning(
                    "haiku_decision result=fallback reason=llm_rejected text=%s",
                    summarize_for_log(llm_failed_text),
                )
        elif fixed is not None:
            line = fixed
        else:
            line = self._render_haiku_line(event).strip()
        line = (line or "").strip() or llm_failed_text
        # 失敗定型句は workshop pin にしない（「まとまらんかった」が句として残るのを防ぐ）
        if not self._is_llm_failed_haiku_text(line):
            self._remember_haiku_emission(event, now, line, route="haiku")
        else:
            LOGGER.warning(
                "haiku_emit result=failed_no_pin text=%s",
                summarize_for_log(line),
            )
        LOGGER.warning(
            "haiku_emit result=emitted text=%s",
            summarize_for_log(self._format_haiku_line(line)),
        )
        return line

    def _clear_pending_haiku_prep(self) -> None:
        self.state.pending_haiku_after_preface = False
        self.state.pending_haiku_started_at = None
        self._pending_haiku_prompt_details = None
        self._pending_haiku_source_atoms = ()
        self._pending_haiku_fixed_line = None
        # interpretation / materials は emission 後に残す必要はないが、キャンセル時は捨てる
        self._pending_haiku_interpretation = None
        self._pending_haiku_materials = None

    def _force_clear_stuck_pending_haiku(self, now: datetime, *, max_age_s: float = 20.0) -> bool:
        """本句が何らかの理由で出せず pending が張り付いたとき、入力 hold を解放する。"""
        if not self.state.pending_haiku_after_preface:
            return False
        started = self.state.pending_haiku_started_at
        if started is None:
            # 古い経路で started が無い場合は emitted_at 相当が無いので、開始を今とみなして次回判定
            self.state.pending_haiku_started_at = now
            return False
        age_s = (now - started).total_seconds()
        if age_s < max_age_s:
            return False
        LOGGER.warning(
            "haiku_pending_stuck_cleared age_s=%.1f (releasing player_input hold)",
            age_s,
        )
        self._clear_pending_haiku_prep()
        return True

    def _compose_haiku_preface_speech(self, scene: SceneContext) -> str:
        """見どころを口にする。「ここで一句。」は本句側に任せる。"""
        inspiration = self._haiku_inspiration_spoken_line(scene)
        if inspiration:
            return inspiration if inspiration.endswith(("。", "わ", "や", "で", "ね")) else f"{inspiration}。"
        # 見どころが無いときも二重に「ここで一句」と言わない
        return "なんか浮かんできたわ。"

    def _haiku_inspiration_spoken_line(
        self,
        scene: SceneContext,
    ) -> str | None:
        """検証済み節だけを順に話し、発話後の再分割・切り詰めをしない。"""

        if not scene.found or not scene.clauses:
            return None
        body = scene.spoken_text.strip()
        if not body:
            return None
        # すでに「浮かんだ」系なら重ねない
        if any(marker in body for marker in ("浮か", "おもいつ", "思いつ")):
            return body if body.endswith(("。", "わ", "や", "で", "ね")) else f"{body}。"
        return f"{body}、なんか浮かんできたわ"

    def _render_haiku_line(self, event: GameEvent) -> str:
        context = self._haiku_context(event)
        irony, _ = self._detect_haiku_irony(context)
        scene, _ = self._detect_haiku_scene(context, irony)
        self._pending_haiku_interpretation = self._haiku_interpretation_text(irony, scene)
        self._stash_haiku_materials_seed(event, context, irony, scene)
        fallback_text = self._fallback_haiku_line(event)
        llm_failed_text = self._llm_failed_haiku_line()
        skip_reason = self._haiku_generation_skip_reason()
        if skip_reason is not None:
            LOGGER.warning(
                "haiku_decision result=fallback reason=%s text=%s",
                skip_reason,
                summarize_for_log(fallback_text),
            )
            return fallback_text
        prompt_details = context.prompt_details(irony, scene)
        constraints = self._haiku_constraint_details(event, scene)
        prompt_details["haiku_constraints"] = constraints
        if constraints and self._pending_haiku_materials is not None:
            self._pending_haiku_materials["haiku_constraints"] = constraints
        generated = generate_grounded_haiku(
            self.llm,
            details=prompt_details,
            source_atoms=context.source_atoms,
            fallback_text=llm_failed_text,
            max_tokens=self.settings.haiku_structured_max_tokens,
            generation_strategy=self.settings.haiku_generation_strategy,
            max_regeneration_rounds=self.settings.haiku_max_regeneration_rounds,
        )
        line = generated.text
        if generated.accepted and self._pending_haiku_materials is not None:
            self._pending_haiku_materials["line_sources"] = list(generated.line_sources)
            self._pending_haiku_materials["generation_strategy"] = generated.generation_strategy
            self._pending_haiku_materials["regeneration_rounds"] = generated.regeneration_rounds
            self._pending_haiku_materials["prompt_variant"] = generated.prompt_variant
        if line == llm_failed_text:
            LOGGER.warning(
                "haiku_decision result=fallback reason=llm_rejected text=%s",
                summarize_for_log(llm_failed_text),
            )
        return line

    def _haiku_interpretation_text(self, irony: IronyContext, scene: SceneContext) -> str | None:
        if irony.found and irony.description.strip():
            return irony.description.strip()
        if scene.found and scene.spoken_text.strip():
            return scene.spoken_text.strip()
        return None

    def _stash_haiku_materials_seed(
        self,
        event: GameEvent,
        context: HaikuContext,
        irony: IronyContext,
        scene: SceneContext,
        *,
        source_atoms: tuple[HaikuSourceAtom, ...] | None = None,
        preface_spoken: str | None = None,
    ) -> None:
        """発句時点の材料を workshop 用に保持（句テキストへの制御タグは付けない）。"""
        motifs: list[str] = []
        if scene.found:
            motifs.extend(str(m) for m in scene.motifs if m)
        focus: list[str] = []
        if scene.found:
            focus.extend(str(f) for f in scene.focus if f)
        if irony.found:
            focus.extend(str(f) for f in irony.focus if f)
        elements = list(irony.elements) if irony.found else []
        held = context.held_item if context.held_item and context.held_item != "なし" else None
        self._pending_haiku_materials = build_workshop_materials_seed(
            interpretation=self._pending_haiku_interpretation or self._haiku_interpretation_text(irony, scene),
            biome=normalize_minecraft_id(event.world.biome) or context.biome_id,
            structure=normalize_minecraft_id(event.world.structure) or context.structure_id or None,
            time_phase=str(context.time_phase) if context.time_phase else None,
            motifs=motifs,
            focus=focus,
            elements=elements,
            held_item=held,
            nearby_blocks=list(context.nearby_blocks),
            passive_mobs=list(context.passive_mobs),
        )
        # カタログ日本語ラベルが context にあれば優先（seed の lookup より確実）
        mats = self._pending_haiku_materials
        if context.biome_label and not mats.get("biome_ja"):
            mats["biome_ja"] = context.biome_label
        if context.structure_label and not mats.get("structure_ja"):
            mats["structure_ja"] = context.structure_label
        # あんちょこの正本へ戻れるよう、原文snapshotと派生atomを句に添える。
        mats["catalog_sources"] = [source.to_dict() for source in context.catalog_sources]
        effective_atoms = context.source_atoms if source_atoms is None else source_atoms
        mats["source_atoms"] = [atom.to_prompt_dict() for atom in effective_atoms]
        if preface_spoken:
            mats["preface_spoken"] = preface_spoken

    def _remember_haiku_emission(
        self,
        event: GameEvent,
        now: datetime,
        text: str,
        *,
        route: str | None,
    ) -> None:
        stripped = self._strip_haiku_preface(text).strip()
        if not stripped:
            return
        time_phase = getattr(event.world.time_phase, "value", event.world.time_phase)
        biome = normalize_minecraft_id(event.world.biome)
        structure = normalize_minecraft_id(event.world.structure)
        phase = str(time_phase) if time_phase else None
        materials = dict(self._pending_haiku_materials or {})
        if not materials:
            # preface なし経路や stash 漏れでも最低限の材料は載せる
            try:
                context = self._haiku_context(event)
                materials = build_workshop_materials_seed(
                    interpretation=self._pending_haiku_interpretation,
                    biome=biome or context.biome_id,
                    structure=structure or context.structure_id or None,
                    time_phase=phase or (str(context.time_phase) if context.time_phase else None),
                    held_item=context.held_item if context.held_item != "なし" else None,
                    nearby_blocks=list(context.nearby_blocks),
                    passive_mobs=list(context.passive_mobs),
                )
                if context.biome_label:
                    materials["biome_ja"] = context.biome_label
                if context.structure_label:
                    materials["structure_ja"] = context.structure_label
                materials["catalog_sources"] = [
                    source.to_dict() for source in context.catalog_sources
                ]
                materials["source_atoms"] = [
                    atom.to_prompt_dict() for atom in context.source_atoms
                ]
            except Exception:  # noqa: BLE001
                materials = build_workshop_materials_seed(
                    interpretation=self._pending_haiku_interpretation,
                    biome=biome,
                    structure=structure,
                    time_phase=phase,
                )
        if self._pending_haiku_interpretation and "interpretation" not in materials:
            materials["interpretation"] = self._pending_haiku_interpretation
        materials = attach_fragment_links(materials, stripped)
        self._pending_haiku_materials = None
        self.emitted_haiku = HaikuEmission(
            created_at=now,
            text=stripped,
            preface="ここで一句。",
            interpretation=self._pending_haiku_interpretation,
            biome=biome,
            structure=structure,
            time_phase=phase,
            dimension=event.player.dimension,
            event_sequence=event.sequence,
            route=route,
            materials=materials,
        )

    def _strip_haiku_preface(self, text: str) -> str:
        stripped = text.strip()
        for prefix in ("ここで一句。", "ここで一句"):
            if stripped.startswith(prefix):
                return stripped[len(prefix):].strip()
        return stripped

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

    def _is_llm_failed_haiku_text(self, text: str) -> bool:
        stripped = self._strip_haiku_preface(text or "").strip()
        if not stripped:
            return True
        failed = (self._llm_failed_haiku_line() or "").strip()
        if failed and stripped == failed:
            return True
        # カタログ文言の揺れ用
        return "まとまらん" in stripped

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
        scene = SceneContext.from_mapping(
            payload,
            source_atoms=context.source_atoms,
        )
        status = self._structured_status(payload)
        if not scene.found:
            # structured client の accepted は JSON として読めたことだけを示す。
            # found=false は正常な「見どころなし」だが、found/clauses を欠く旧形式や
            # 壊れた atom ID はドメイン契約不合格としてログ上も区別する。
            explicitly_not_found = isinstance(payload, dict) and payload.get("found") is False
            if status == "accepted" and not explicitly_not_found:
                keys = ",".join(sorted(str(key) for key in (payload or {}).keys())) or "-"
                LOGGER.warning(
                    "haiku_scene result=rejected reason=invalid_contract keys=%s",
                    keys,
                )
                return scene, "invalid_payload"
            return scene, status
        if not validate_preface_clauses(
            self.llm,
            clauses=scene.clauses,
            source_atoms=context.source_atoms,
            max_tokens=self.settings.haiku_structured_max_tokens,
        ):
            LOGGER.warning("haiku_preface_grounding result=rejected")
            return SceneContext(), "preface_rejected"
        return scene, status

    def _haiku_context(self, event: GameEvent) -> HaikuContext:
        time_phase = getattr(event.world.time_phase, "value", event.world.time_phase) or "unknown"
        weather = self._weather_value(event.world.weather) or "unknown"
        biome = event.world.biome
        # 実際の手持ち（道具 hard 制約・対比テンション用）
        real_held_label = self._item_label(event.player.held_item)
        # 句の主役: 作業道具を持っているときは所持の非道具を重み付きで1つ
        poem_item_id, poem_held, poem_source = self._haiku_poem_item_choice(event, real_held_label)
        inventory_close_pair, inventory_far_item, inventory_items = self._haiku_inventory_values(
            event.inventory,
            held_item_id=event.player.held_item,
        )
        nearby_blocks = tuple(self._haiku_nearby_block_values(event.nearby_resources))
        passive_mobs = tuple(self._haiku_passive_mob_values(event))
        precipitation_context = self._precipitation_context(event)
        LOGGER.warning(
            "haiku_precipitation y=%s temp=%s snow_start_y=%s snowfall_zone=%s "
            "local_precipitation=%s snow_evidence=%s surface_snow=%s",
            precipitation_context.current_y,
            precipitation_context.biome_temperature,
            precipitation_context.snow_start_y,
            precipitation_context.snowfall_zone,
            precipitation_context.precipitation_kind,
            precipitation_context.snow_evidence,
            precipitation_context.surface_snow_observed,
        )
        feature_candidates = tuple(
            self._haiku_feature_candidates(
                event,
                held_item=poem_held,
                poem_item_source=poem_source,
                inventory_items=inventory_items,
                nearby_blocks=nearby_blocks,
                passive_mobs=passive_mobs,
                precipitation_context=precipitation_context,
            )
        )
        poetic_lines, poetic_mob_keys = self._haiku_poetic_lines(event)
        structure_id, structure_label = self._haiku_structure_fields(event)
        climate_hint = self._haiku_climate_hint(biome)
        catalog_sources = tuple(
            self._haiku_catalog_sources(
                event,
                poem_item_id=poem_item_id,
                poem_item_label=poem_held,
            )
        )
        # カタログ由来を先に置き、同じラベルの観測atomは二重に作らない。
        source_atoms = merge_source_atoms(
            atoms_from_catalog_sources(catalog_sources),
            atoms_from_observations(feature_candidates),
        )
        return HaikuContext(
            player_name=self._player_call_name(event),
            biome_id=self._normalized_biome(biome) or "unknown",
            biome_label=self._biome_label_with_reading(biome),
            biome_group=self._biome_group_label(biome) or "不明",
            biome_traits=tuple(self._haiku_biome_traits(biome)),
            time_phase=time_phase,
            time_label=TIME_PHASE_LABELS.get(time_phase, "不明"),
            weather=weather,
            weather_label=(
                "雪"
                if precipitation_context.precipitation_kind == "snow"
                else WEATHER_LABELS.get(weather, "不明")
            ),
            precipitation_context=precipitation_context,
            poem_item_id=poem_item_id,
            held_item=poem_held,
            poem_item_source=poem_source,
            inventory_items=inventory_items,
            inventory_close_pair=inventory_close_pair,
            inventory_far_item=inventory_far_item,
            nearby_blocks=nearby_blocks,
            passive_mobs=passive_mobs,
            haiku_tags=tuple(
                self._haiku_tags(
                    event,
                    feature_candidates,
                    covered_mob_keys=poetic_mob_keys,
                )
            ),
            feature_candidates=feature_candidates,
            # 対比テンションは「実際に手にある道具」を使う
            candidate_tensions=tuple(
                self._haiku_candidate_tensions(
                    event,
                    real_held_label or poem_held,
                    passive_mobs,
                    nearby_blocks,
                )
            ),
            catalog_notes=catalog_notes_projection(catalog_sources),
            catalog_sources=catalog_sources,
            source_atoms=source_atoms,
            poetic_lines=tuple(poetic_lines),
            structure_id=structure_id,
            structure_label=structure_label,
            climate_hint=climate_hint,
        )

    def _is_haiku_work_tool_item(self, item_id: str | None) -> bool:
        """探索中に手に握りがちで句が単調になりやすい作業・戦闘道具。"""
        nid = str(item_id or "").split(":")[-1].strip().lower()
        if not nid or nid == "air":
            return False
        suffixes = (
            "pickaxe",
            "shovel",
            "axe",
            "hoe",
            "sword",
            "bow",
            "crossbow",
            "trident",
            "mace",
            "spear",
            "shears",
            "fishing_rod",
            "brush",
            "shield",
        )
        return any(nid.endswith(suffix) or nid == suffix for suffix in suffixes)

    def _haiku_pocket_weight(
        self,
        item_id: str,
        *,
        label: str,
        count: int,
    ) -> int:
        """所持から句の主役を選ぶときの重み（高いほど選ばれやすい）。"""
        nid = str(item_id).split(":")[-1].strip().lower()
        # 平凡な埋め草は弱く
        junk = {
            "dirt",
            "coarse_dirt",
            "cobblestone",
            "cobbled_deepslate",
            "stone",
            "netherrack",
            "gravel",
            "sand",
            "red_sand",
            "andesite",
            "diorite",
            "granite",
            "tuff",
            "deepslate",
            "stick",
            "arrow",
        }
        if nid in junk:
            return 1
        if any(
            nid.endswith(sfx)
            for sfx in ("_ore", "_ingot", "_nugget", "diamond", "emerald", "netherite")
        ) or nid in {"totem_of_undying", "elytra", "nether_star"}:
            return 12
        if any(
            marker in nid
            for marker in (
                "flower",
                "tulip",
                "orchid",
                "lilac",
                "rose",
                "peony",
                "sunflower",
                "dandelion",
                "poppy",
                "allium",
                "azure",
                "cornflower",
                "lily",
                "torchflower",
                "pitcher",
                "spore_blossom",
            )
        ) or "dye" in nid:
            return 11
        if any(
            marker in nid
            for marker in (
                "pressure_plate",
                "button",
                "lantern",
                "candle",
                "book",
                "map",
                "banner",
                "music_disc",
                "goat_horn",
                "pottery",
                "sherd",
                "smithing",
            )
        ):
            return 10
        if any(
            marker in nid
            for marker in (
                "apple",
                "bread",
                "stew",
                "soup",
                "berry",
                "melon",
                "potato",
                "carrot",
                "beef",
                "pork",
                "chicken",
                "mutton",
                "fish",
                "salmon",
                "cookie",
                "cake",
                "pie",
                "honey",
            )
        ):
            return 9
        if nid.endswith(("_log", "_planks", "_sapling", "_leaves", "_wool", "_carpet")):
            return 4
        if "torch" in nid or nid == "campfire" or nid == "soul_campfire":
            return 5
        # 個数は少しだけ効かせる（山積み junk をさらに押し上げない）
        return 6 + min(max(int(count), 0), 4)

    def _select_haiku_pocket_motif(self, event: GameEvent) -> tuple[str, str] | None:
        """所持から非道具を重み付き・決定的に1つ選び、IDも失わず返す。"""
        scored: list[tuple[int, str, str]] = []
        seen_labels: set[str] = set()
        for item_id, count in (event.inventory or {}).items():
            if not count or int(count) <= 0:
                continue
            if self._is_haiku_work_tool_item(item_id):
                continue
            nid = str(item_id).split(":")[-1].strip().lower()
            if not nid or nid == "air":
                continue
            label = self._item_label(item_id)
            if not label or label in seen_labels:
                continue
            seen_labels.add(label)
            weight = self._haiku_pocket_weight(str(item_id), label=label, count=int(count))
            scored.append((weight, label, nid))
        if not scored:
            return None
        max_w = max(row[0] for row in scored)
        # 最高重み近傍だけを候補にして、sequence で決定的に1つ
        pool = [row for row in scored if row[0] >= max_w - 2]
        pool.sort(key=lambda row: (row[1], row[2]))
        seed = int(event.sequence or 0)
        seed = seed * 1009 + sum(ord(ch) for ch in (event.player.name or "p")[:12])
        selected = pool[seed % len(pool)]
        return selected[2], selected[1]

    def _haiku_poem_item_choice(
        self,
        event: GameEvent,
        real_held_label: str,
    ) -> tuple[str, str, str]:
        """(句の主役ID, ラベル, source hand|pocket)。

        作業道具を握っているときは所持の非道具を優先。道具 hard 制約は event.held のまま。
        """
        held_id = event.player.held_item
        if not self._is_haiku_work_tool_item(held_id):
            normalized = normalize_minecraft_id(held_id) or ""
            return normalized, (real_held_label or ""), "hand"
        pocket = self._select_haiku_pocket_motif(event)
        if pocket:
            pocket_id, pocket_label = pocket
            return pocket_id, pocket_label, "pocket"
        # 非道具が無いときだけ道具を句に出す
        normalized = normalize_minecraft_id(held_id) or ""
        return normalized, (real_held_label or ""), "hand"

    def _haiku_feature_candidates(
        self,
        event: GameEvent,
        *,
        held_item: str,
        poem_item_source: str = "hand",
        inventory_items: tuple[str, ...],
        nearby_blocks: tuple[str, ...],
        passive_mobs: tuple[str, ...],
        precipitation_context: PrecipitationContext,
    ) -> list[HaikuFeature]:
        time_phase = getattr(event.world.time_phase, "value", event.world.time_phase) or "unknown"
        weather = self._weather_value(event.world.weather) or "unknown"
        portal_type = (event.world.nearby_portal_type or "").strip().lower()
        structure_id, structure_label = self._haiku_structure_fields(event)
        has_structure = bool(structure_label)
        climate_hint = self._haiku_climate_hint(event.world.biome)
        candidates: list[HaikuFeature] = []
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
        # structure あり: 場所の主役は構造物。バイオーム名は候補に載せない（名称に気候が含まれることが多い）。
        # 気候は参考程度だけ。
        if has_structure:
            candidates.append(HaikuFeature("構造物", "structure", structure_label))
            if climate_hint:
                candidates.append(HaikuFeature("気候", "climate", climate_hint))
        else:
            candidates.extend([
                HaikuFeature("バイオーム", "biome", self._biome_label_with_reading(event.world.biome)),
                HaikuFeature("地帯", "biome_group", self._biome_group_label(event.world.biome) or "不明"),
            ])
            candidates.extend(
                HaikuFeature("地形", f"trait_{index}", trait)
                for index, trait in enumerate(self._haiku_biome_traits(event.world.biome)[:4], start=1)
            )
        if precipitation_context.precipitation_kind == "snow":
            candidates.append(HaikuFeature("降雪", "local_precipitation", "現在は雪"))
        candidates.extend([
            HaikuFeature(
                "天気",
                "weather",
                "雪" if precipitation_context.precipitation_kind == "snow" else WEATHER_LABELS.get(weather, "不明"),
            ),
            HaikuFeature("時間", "time_phase", TIME_PHASE_LABELS.get(time_phase, "不明")),
        ])
        vehicle_fact = player_vehicle_fact(event.player.vehicle)
        if vehicle_fact:
            # 主語を省くとドギド自身の乗車と誤解しうるため、一文を崩さず材料化する。
            candidates.append(HaikuFeature("乗車", "vehicle_activity", vehicle_fact))
        if held_item:
            if poem_item_source == "pocket":
                candidates.append(HaikuFeature("持ち物", "pocket_item", held_item))
            else:
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

    def _haiku_structure_fields(self, event: GameEvent) -> tuple[str, str]:
        raw = event.world.structure or getattr(self.state, "current_structure", None)
        if not raw:
            return "", ""
        structure_id = str(raw).removeprefix("minecraft:").strip()
        if not structure_id:
            return "", ""
        entry = self._structure_entry(structure_id) or {}
        label = (
            str(entry.get("label") or "").strip()
            or self._structure_label(structure_id)
            or structure_id
        )
        return structure_id, label

    def _haiku_climate_hint(self, biome: str | None) -> str:
        """気温・地帯の弱い参考（structure 主役時の背景用）。数値は出さない。"""
        temperature = self._biome_temperature(biome)
        group = self._biome_group_label(biome) or ""
        feel = ""
        if temperature is not None:
            if temperature >= 1.5:
                feel = "とても暑い"
            elif temperature >= 1.0:
                feel = "暑い"
            elif temperature >= 0.5:
                feel = "穏やか"
            elif temperature >= 0.2:
                feel = "涼しい"
            elif temperature >= 0.0:
                feel = "寒い"
            else:
                feel = "とても寒い"
        # group_label は「乾燥帯バイオーム」など。短くする
        band = group.replace("バイオーム", "").strip()
        if feel and band and band not in feel:
            return f"{feel}（{band}）"
        return feel or band

    def _haiku_biome_traits(self, biome: str | None) -> list[str]:
        """創作材料には気候係数を出さず、コードで解釈済みの気配だけを載せる。"""

        climate_hint = self._haiku_climate_hint(biome)
        return [climate_hint] if climate_hint else []

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

    def _haiku_catalog_sources(
        self,
        event: GameEvent,
        *,
        poem_item_id: str,
        poem_item_label: str,
    ) -> list[CatalogSourceSnapshot]:
        """実際に選んだ ID だけから、川柳用の読み取りsnapshotを作る。

        entry_catalog の返却型や元JSONは変えない。表示名からの逆引きもしない。
        """

        sources: list[CatalogSourceSnapshot] = []
        seen: set[str] = set()

        def append(source: CatalogSourceSnapshot | None) -> None:
            if source is None or source.source_ref in seen:
                return
            seen.add(source.source_ref)
            sources.append(source)

        # 場所の主役 → 選択した手元 → 距離順の周辺 → biome → mob の順。
        structure_id, structure_label = self._haiku_structure_fields(event)
        if structure_id:
            append(
                catalog_source_snapshot(
                    catalog_type="structure",
                    catalog_id=structure_id,
                    entry=self._structure_entry(structure_id),
                    observation_role="current_structure",
                    fallback_label=structure_label,
                )
            )

        if poem_item_id and poem_item_id != "air":
            append(
                catalog_source_snapshot(
                    catalog_type="item",
                    catalog_id=poem_item_id,
                    entry=item_entry(poem_item_id),
                    observation_role="selected_item",
                    fallback_label=poem_item_label,
                )
            )

        for resource in sorted(event.nearby_resources, key=lambda candidate: candidate.distance or inf)[:3]:
            resource_id = normalize_minecraft_id(resource.name) or ""
            append(
                catalog_source_snapshot(
                    catalog_type="block",
                    catalog_id=resource_id,
                    entry=block_entry(resource.name),
                    observation_role="nearby_block",
                    fallback_label=self._block_label(resource.name),
                )
            )

        biome_id = self._normalized_biome(event.world.biome) or ""
        append(
            catalog_source_snapshot(
                catalog_type="biome",
                catalog_id=biome_id,
                entry=self._biome_entry(event.world.biome),
                observation_role="current_biome",
                fallback_label=self._biome_label_with_reading(event.world.biome),
            )
        )

        for passive in event.passive_mobs[:3]:
            mob_id = normalize_minecraft_id(passive.type) or ""
            append(
                catalog_source_snapshot(
                    catalog_type="mob",
                    catalog_id=mob_id,
                    entry=mob_entry(passive.type),
                    observation_role="passive_mob",
                    fallback_label=self._mob_label(passive.type),
                )
            )

        return sources

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

    def _haiku_poetic_lines(self, event: GameEvent) -> tuple[list[str], frozenset[str]]:
        """主役平和 mob 最大2体の1行詩語。covered な mob id は haiku_tags で再展開しない。"""
        lines: list[str] = []
        covered: set[str] = set()
        seen_labels: set[str] = set()
        for mob in event.passive_mobs:
            mob_key = normalize_minecraft_id(mob.type) or str(mob.type or "").strip().lower()
            if not mob_key or mob_key in covered:
                continue
            label = self._mob_label(mob.type)
            if label and label in seen_labels:
                continue
            line = mob_poetic_line(mob.type)
            if not line:
                continue
            lines.append(line)
            covered.add(mob_key)
            if label:
                seen_labels.add(label)
            if len(lines) >= 2:
                break
        return lines, frozenset(covered)

    def _haiku_tags(
        self,
        event: GameEvent,
        features: tuple[HaikuFeature, ...],
        *,
        covered_mob_keys: frozenset[str] = frozenset(),
    ) -> list[str]:
        """補助のフラット詩語。poetic_lines 済み mob は二重に載せない。"""
        tags: list[str] = []
        for feature in features:
            # feature 側の tags は主に mob 由来。covered のラベルに紐づくものは後で文字列重複除去
            tags.extend(feature.tags)
        for mob in event.passive_mobs[:4]:
            mob_key = normalize_minecraft_id(mob.type) or str(mob.type or "").strip().lower()
            if mob_key in covered_mob_keys:
                continue
            tags.extend(mob_poetic_tags(mob.type))
        covered_fragments: set[str] = set()
        for mob_key in covered_mob_keys:
            for fragment in mob_poetic_tags(mob_key):
                covered_fragments.add(fragment)
        seen: set[str] = set()
        result: list[str] = []
        limit = 8 if covered_mob_keys else 16
        for tag in tags:
            if not tag or tag in seen or tag in covered_fragments:
                continue
            seen.add(tag)
            result.append(tag)
            if len(result) >= limit:
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

    def _haiku_generation_skip_reason(self) -> str | None:
        """固定カタログへ切り替える理由を返す。

        scene は見どころ発話の品質にだけ使う。本句は一次 source atom を正として
        共通生成器が材料数・出典・音数を検査するため、scene の弱さを理由に
        生成前から固定句へ置き換えない。
        """

        if self.llm is None:
            return "llm_unavailable"
        route_enabled = getattr(self.llm, "route_enabled", None)
        if callable(route_enabled) and not route_enabled("haiku"):
            return "llm_unavailable"
        enabled = getattr(self.llm, "enabled", None)
        if callable(enabled) and not enabled():
            return "llm_unavailable"
        return None

    def _haiku_constraint_details(self, event: GameEvent, scene: SceneContext) -> dict[str, object] | None:
        from dogido_server.catalog_readings import haiku_reading_terms
        from dogido_server.entry_catalog import biome_reading

        families = self._haiku_selected_noun_families(event, scene)
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

        biome_label = self._biome_label(event.world.biome)
        catalog_reading = biome_reading(event.world.biome)
        reading_allowed, reading_forbidden = haiku_reading_terms(
            [biome_label],
            catalog_readings={biome_label: catalog_reading} if catalog_reading else None,
        )
        for term in reading_allowed:
            if term and term not in seen_allowed:
                seen_allowed.add(term)
                allowed_terms.append(term)
        for term in reading_forbidden:
            if term and term not in seen_forbidden:
                seen_forbidden.add(term)
                forbidden_terms.append(term)

        # H5.1: player lessons は soft のみ。forbidden_fragments を hard 禁止に合流しない
        # （道具・読みの forbidden_terms だけが hard）
        player_lessons: list[str] = []
        provider = getattr(self, "haiku_lessons_provider", None)
        if provider is not None:
            try:
                rows = provider() or []
            except Exception:
                rows = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                polarity = str(row.get("polarity") or "tighten").strip().lower()
                if polarity == "loosen":
                    continue
                note = str(row.get("note") or "").strip()
                if note and note not in player_lessons:
                    player_lessons.append(note)

        if not allowed_terms and not forbidden_terms and not player_lessons:
            return None
        details: dict[str, object] = {
            "allowed_terms": allowed_terms,
            "forbidden_terms": forbidden_terms,
        }
        # 空配列は載せない。soft は最大 3（provider 側でも絞る）
        if player_lessons:
            details["player_lessons"] = player_lessons[:3]
        return details

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
