# state_machine/machine.py
from __future__ import annotations

from dogido_server.config import Settings
from dogido_server.haiku.source_atoms import HaikuSourceAtom
from dogido_server.knowledge_query import LocalKnowledgeProvider
from dogido_server.llm import LLMFrontend
from dogido_server.memory_types import HaikuEmission
from dogido_server.models import EventName, GameEvent, HostileOutcome
from dogido_server.player_input import PlayerInputContext, route_player_input
from dogido_server.py_tree_policy import PyTreeActionPolicy
from dogido_server.state_machine.haiku_context import HaikuContext, IronyContext
from dogido_server.state_machine.mixins.action_builder import ActionBuilderMixin
from dogido_server.state_machine.mixins.auditory import AuditoryMixin
from dogido_server.state_machine.mixins.common import CommonMixin
from dogido_server.state_machine.mixins.cue_reactions import CueReactionsMixin
from dogido_server.state_machine.mixins.environmental_reactions import EnvironmentalReactionsMixin
from dogido_server.state_machine.mixins.haiku import HaikuMixin
from dogido_server.state_machine.mixins.inventory import InventoryMixin
from dogido_server.state_machine.mixins.narration import NarrationMixin
from dogido_server.state_machine.mixins.state_updates import StateUpdatesMixin
from dogido_server.state_machine.mixins.threat_interrupts import ThreatInterruptsMixin
from dogido_server.state_machine.mixins.visual_reports import VisualReportsMixin
from dogido_server.state_machine.mixins.visual_targets import VisualTargetsMixin
from dogido_server.state_machine.mixins.world_analysis import WorldAnalysisMixin
from dogido_server.state_machine.types import RuntimeState, SpeechReference, StateMachineResult


class DogidoStateMachine(
    StateUpdatesMixin,
    ActionBuilderMixin,
    CommonMixin,
    CueReactionsMixin,
    EnvironmentalReactionsMixin,
    HaikuMixin,
    ThreatInterruptsMixin,
    NarrationMixin,
    VisualTargetsMixin,
    VisualReportsMixin,
    AuditoryMixin,
    WorldAnalysisMixin,
    InventoryMixin,
):
    def __init__(
        self,
        settings: Settings,
        llm: LLMFrontend | None = None,
        *,
        knowledge_authority: LocalKnowledgeProvider | None = None,
    ) -> None:
        self.settings = settings
        self.state = RuntimeState()
        self.llm = llm
        self.player_input = PlayerInputContext()
        self.policy_tree = PyTreeActionPolicy() if settings.decision_policy == "py_trees" else None
        self.emitted_haiku: HaikuEmission | None = None
        self._pending_haiku_interpretation: str | None = None
        # 先行発話後に scene 根拠を整理して作る、本句用 details。
        self._pending_haiku_prompt_details: dict[str, object] | None = None
        # details の JSON 表現とは別に、検証器へ渡す不変 source atom を保持する。
        self._pending_haiku_source_atoms: tuple[HaikuSourceAtom, ...] = ()
        # LLM 句をスキップするときの本句固定文（カタログ fallback 等）
        self._pending_haiku_fixed_line: str | None = None
        # workshop materials シード（motifs/held/nearby。本句で fragment_links を付与）
        self._pending_haiku_materials: dict[str, object] | None = None
        # 見どころ文を先に返し、その音声再生中に次フレームで scene の根拠整理と
        # 本句生成を進めるための発句時 snapshot。
        self._pending_haiku_context: HaikuContext | None = None
        self._pending_haiku_irony: IronyContext | None = None
        self._pending_haiku_origin_event: GameEvent | None = None
        # service が session.dialogue / haiku_workshop / lessons を返す callable を差し込む
        self.dialogue_context_provider = None
        self.haiku_workshop_provider = None
        self.haiku_lessons_provider = None
        # 正本providerは通常の検索にも使い、差替えproviderの候補だけを
        # 同じ正本から再構成して照合する。通常経路では二重検索しない。
        self._knowledge_authority = knowledge_authority or LocalKnowledgeProvider()
        self.knowledge_provider = self._knowledge_authority
        # serviceが、高優先発話に先送りされた質問だけを再キューするための
        # 現在tick限定フラグ。DB回答と現在句の質問処理でだけTrueにする。
        self.knowledge_query_handled = False
        # 現在tickの知識回答に対応する参考資料。本文とは別にAudioActionへ載せる。
        self.knowledge_reply_references: tuple[SpeechReference, ...] = ()
        # service が pending_player_text 待ちのとき True（ambient 抑止用）
        self.player_input_queued = False
        # 今tickで初めて届いた即時戦闘結果。同じentity_idの再配送では空にする。
        self._fresh_immediate_hostile_outcomes: tuple[HostileOutcome, ...] = ()

    def process(
        self,
        event: GameEvent,
        *,
        interpreted_user_text: str | None = None,
        player_input_context: PlayerInputContext | None = None,
    ) -> StateMachineResult:
        now = event.observed_at
        self.knowledge_query_handled = False
        self.knowledge_reply_references = ()
        previous_mode = self.state.mode
        self.emitted_haiku = None
        # preface 待ち中は見どころ・prompt・materials を消さない（次フレームの本句で使う）
        if not self.state.pending_haiku_after_preface:
            self._pending_haiku_interpretation = None
            self._pending_haiku_prompt_details = None
            self._pending_haiku_source_atoms = ()
            self._pending_haiku_fixed_line = None
            self._pending_haiku_materials = None
            self._pending_haiku_context = None
            self._pending_haiku_irony = None
            self._pending_haiku_origin_event = None
        self.player_input = player_input_context or route_player_input(
            event.meta.user_text,
            interpreted_text=interpreted_user_text,
        )
        self._fresh_immediate_hostile_outcomes = ()
        if event.event.name in {
            EventName.HOSTILE_DEFEATED,
            EventName.CREEPER_DETONATED,
        }:
            self._fresh_immediate_hostile_outcomes = tuple(
                outcome
                for outcome in (event.combat.hostile_outcomes or [])
                if self._hostile_outcome_key(outcome)
                not in self.state.announced_hostile_outcome_ids
            )
        dimension_changed = self._did_change_dimension(event)
        self._handle_dimension_change(event)
        newly_burning_visual = self._find_newly_burning_visual(event)
        weather_transition = None if dimension_changed else self._weather_transition(event)
        entered_occluded_dark_zone = False if dimension_changed else self._entered_occluded_dark_zone(event)
        entered_safe_zone_with_door = False if dimension_changed else self._entered_safe_zone_with_door(event)
        entered_emergency_shelter = False if dimension_changed else self._entered_emergency_shelter(event)
        entered_close_flying_visual = None if dimension_changed else self._entered_close_flying_visual(event)
        exited_safe_zone_with_door = False if dimension_changed else self._exited_safe_zone_with_door(event)
        entered_submerged_dark_zone = False if dimension_changed else self._entered_submerged_dark_zone(event)
        entered_mining_fatigue = False if dimension_changed else self._entered_status_effect(event, "mining_fatigue")
        light_source_crafted = self._light_source_crafted(event)

        self._update_memory(event, now)
        signals = self._derive_signals(event, now)
        signals.dimension_changed = dimension_changed
        signals.newly_burning_visual = newly_burning_visual
        signals.entered_occluded_dark_zone = entered_occluded_dark_zone
        signals.entered_safe_zone_with_door = entered_safe_zone_with_door
        signals.entered_emergency_shelter = entered_emergency_shelter
        signals.entered_close_flying_visual = entered_close_flying_visual
        signals.exited_safe_zone_with_door = exited_safe_zone_with_door
        signals.entered_submerged_dark_zone = entered_submerged_dark_zone
        signals.entered_mining_fatigue = entered_mining_fatigue
        signals.light_source_crafted = light_source_crafted
        signals.weather_transition_from = weather_transition[0] if weather_transition is not None else None
        signals.weather_transition_to = weather_transition[1] if weather_transition is not None else None
        if weather_transition is not None:
            self.state.pending_weather_transition_from = weather_transition[0]
            self.state.pending_weather_transition_to = weather_transition[1]
        signals.cold_weather_biome = self._is_cold_weather_biome(event.world.biome)
        signals.dry_weather_biome = self._is_dry_weather_biome(event.world.biome)
        self.state.emergency_shelter_active = signals.emergency_shelter
        next_mode = self._resolve_mode(event, signals, now)
        self._apply_mode_transition(
            previous_mode,
            next_mode,
            now,
            combat_end_already_flushed=event.event.name == EventName.COMBAT_ENDED,
        )
        actions = self._build_actions(event, previous_mode, next_mode, signals, now)
        self._log_emitted_actions(event, previous_mode, next_mode, actions)
        self._update_silence_break_state(event, actions, now)
        for threat in event.visual_threats:
            visual_key = self._visual_identity_key(threat)
            self.state.seen_visual_keys[visual_key] = now
            if self._is_boss_type(threat.type):
                self.state.seen_boss_visual_keys.add(visual_key)
        self.state.active_creeper_fuse_keys = {
            self._visual_identity_key(threat)
            for threat in event.visual_threats
            if threat.type in {"creeper", "charged_creeper"} and threat.fuse_active
        }
        if event.event.name in {
            EventName.HOSTILE_DEFEATED,
            EventName.CREEPER_DETONATED,
        }:
            consumed_entity_ids = {
                str(outcome.entity_id).strip()
                for outcome in (event.combat.hostile_outcomes or [])
                if outcome.entity_id
            }
            consumed_types = {
                outcome.type.removeprefix("minecraft:").strip().lower()
                for outcome in (event.combat.hostile_outcomes or [])
            }
            self.state.last_confirmed_hostiles = [
                hostile
                for hostile in self.state.last_confirmed_hostiles
                if hostile.removeprefix("minecraft:").strip().lower()
                not in consumed_types
            ]
            self.state.recent_visual_memos = [
                memo
                for memo in self.state.recent_visual_memos
                if (
                    memo.dedupe_key.removeprefix("visual:")
                    not in consumed_entity_ids
                    and not (
                        not consumed_entity_ids and memo.mob_type in consumed_types
                    )
                )
            ]
            self.state.recent_hearing_memos = [
                memo
                for memo in self.state.recent_hearing_memos
                if (
                    memo.dedupe_key.removeprefix("hostile:")
                    not in consumed_entity_ids
                    and not (
                        not consumed_entity_ids and memo.mob_type in consumed_types
                    )
                )
            ]
        if (
            event.event.name == EventName.COMBAT_ENDED
            and self._combat_end_clear_confirmed(event)
        ):
            # 余韻文で使った敵名は次の戦闘へ持ち越さない。
            self.state.last_confirmed_hostiles = []
            self.state.last_known_hostile_directions = []
            self.state.announced_hostile_outcome_ids.clear()
        elif event.event.name == EventName.PLAYER_DIED:
            self.state.announced_hostile_outcome_ids.clear()
        self.state.last_foliage_shade_context = self._is_foliage_shade_context(event)

        combat_active = next_mode in {"panic", "suppressed_panic"} or signals.combat_active_hint
        if next_mode == "aftermath" and self._combat_end_clear_confirmed(event):
            # 旧 Fabric adapter では combat_ended だけ
            # combat_active_hint=true のまま届くことがある。
            combat_active = False
        return StateMachineResult(
            state=self.state,
            combat_active=combat_active,
            actions=actions,
            haiku_emission=self.emitted_haiku,
        )
