from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import logging
import threading
from typing import Callable, Iterable
from uuid import uuid4

from dogido_server.assist import ActionContext, ActionName, build_assist_registry
from dogido_server.assist.select_sword import (
    SELECT_SWORD_RULE_VERSION,
    finalize_select_sword_intent_payload,
    interpret_voice_select_sword_request,
    is_bare_sword_target,
    is_explicit_select_sword_request,
    is_explicit_voice_select_sword_request,
    is_unambiguous_select_sword_request,
    is_unambiguous_voice_select_sword_request,
    mentions_sword_target,
    select_weapon_slot,
)
from dogido_server.audio import AudioDispatcher
from dogido_server.config import Settings
from dogido_server.diagnostics import DiagnosticHistory
from dogido_server.display import DisplayHistory, RuntimeStatus
from dogido_server.dialogue_context import DialogueContext
from dogido_server.dialogue.foreground import ForegroundDialogue
from dogido_server.dialogue.main_runtime import MainLanguageRuntime
from dogido_server.episode_log import EpisodeRecorder
from dogido_server.haiku.combat_pause import (
    CombatWorkshopInputAnalysis,
    build_combat_workshop_input_details,
    event_interrupts_workshop,
    fallback_combat_workshop_input_analysis,
    finalize_combat_workshop_input_payload,
    update_workshop_combat_state,
)
from dogido_server.haiku.materials import material_context_visible
from dogido_server.haiku.hud import WorkshopHudSnapshots
from dogido_server.haiku.workshop_agent import (
    WorkshopAgentStep,
    build_workshop_agent_details,
    finalize_workshop_agent_step,
    inspect_workshop,
    record_workshop_agent_step,
    workshop_mutation_evidence_is_valid,
    workshop_player_edit_is_grounded_in_original,
)
from dogido_server.haiku.workshop import (
    PendingRevisionAnalysis,
    PlayerLineReplacement,
    RecentHaikuWorkshop,
    advance_workshop_revision,
    build_player_line_revision,
    build_pending_revision_llm_details,
    build_workshop_intent_llm_details,
    classify_workshop_intent,
    clear_pending_revision,
    combat_resume_confirmation_decision,
    close_confirmation_decision,
    close_workshop,
    extract_conversational_revise,
    finalize_pending_revision_payload,
    finalize_workshop_analysis_payload,
    grounded_material_for_question,
    is_active,
    is_explicit_player_line_replacement_command,
    is_open,
    is_meaning_acknowledgement,
    lessons_from_critique_kind,
    loosen_all_lessons,
    materials_debug_line,
    materials_speech_line,
    mentioned_workshop_line_fragment,
    maybe_close_for_time,
    pending_revision_decision,
    pending_revision_is_current,
    parse_player_line_replacement,
    repair_target_indices,
    wants_clear_haiku_lessons,
    open_from_emission,
    record_drift,
    record_workshop_activity,
    render_workshop_reply,
    should_handle_as_workshop,
    update_marked_workshop_line,
    wants_show_workshop_verse,
    WorkshopAnalysis,
    WorkshopEvaluation,
    validate_workshop_evaluation_payload,
    workshop_findings_from_records,
    workshop_open_intent,
    workshop_verse_lines,
)
from dogido_server.haiku.generation import generate_workshop_revision
from dogido_server.haiku.workshop_context import workshop_context_details
from dogido_server.haiku.edit_contract import PLAYER_LINE_EDIT_CONTRACT_VERSION
from dogido_server.haiku.source_atoms import (
    line_source_ids_from_materials,
    source_atoms_from_materials,
)
from dogido_server.haiku.verse import (
    build_haiku_lines,
    verse_reading_text,
)
from dogido_server.llm import DogidoLLMRouter, LeafGenerationRequest, StructuredGenerationRequest
from dogido_server.language_dialogue.conversation_turns import TurnLedger
from dogido_server.language_dialogue.main_web import build_main_web_research
from dogido_server.memory import MemoryStore
from dogido_server.models import (
    AcceptedEventResponse,
    AdapterSessionCreateRequest,
    AdapterSessionCreateResponse,
    AdapterCommandResult,
    BatchAcceptedResponse,
    CloseSessionResponse,
    EventName,
    GameEvent,
    HeartbeatResponse,
    OutputFlags,
    SelectHotbarCommand,
    StateResponse,
    VoiceInputContextResponse,
    VoiceInputDiagnosticRequest,
)
from dogido_server.player_input import PlayerInputContext, route_player_input
from dogido_server.platform_ai import PlatformStructuredAIRouter
from dogido_server.state_machine import (
    AudioAction,
    DogidoStateMachine,
    HaikuEmission,
    StateMachineResult,
)
from dogido_server.state_machine.fallback_catalog import fallback_prewarm_texts
from dogido_server.state_machine.response_catalog import response_prewarm_texts
from dogido_server.state_machine.types import SpeechReference

LOGGER = logging.getLogger("uvicorn.error")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


@dataclass(slots=True)
class SessionInfo:
    session_id: str
    schema_version: str
    adapter_name: str
    adapter_version: str
    game: str
    player_name: str
    profile_name: str | None
    call_name: str | None
    capabilities: list[str]
    execution_capabilities: list[str]
    created_at: datetime
    machine: DogidoStateMachine
    last_seen_at: datetime | None = None
    last_sequence: int | None = None
    seen_sequences: deque[int] = field(default_factory=lambda: deque(maxlen=2048))
    seen_sequence_set: set[int] = field(default_factory=set)
    seen_idempotency: deque[str] = field(default_factory=lambda: deque(maxlen=2048))
    seen_idempotency_set: set[str] = field(default_factory=set)
    pending_commands: dict[str, SelectHotbarCommand] = field(default_factory=dict)
    seen_command_results: deque[str] = field(default_factory=lambda: deque(maxlen=2048))
    seen_command_result_set: set[str] = field(default_factory=set)
    last_select_sword_command_at: datetime | None = None
    first_event_logged: bool = False
    last_haiku_emission: HaikuEmission | None = None
    # 発句の pin（会話履歴とは別。open 中は句本文を忘れない）
    haiku_workshop: RecentHaikuWorkshop | None = None
    # 音声入力など外部から届いたプレイヤー発話。次のイベントの user_text に相乗りさせる
    pending_player_text: str | None = None
    pending_player_source: str | None = None
    pending_player_display_text: str | None = None
    pending_player_turn_id: str | None = None
    pending_player_force_main_chat: bool = False
    pending_player_foreground_route: str | None = None
    # 直接入力と保留入力が同じtickで衝突した場合の待ち列。先着順・最大8件とし、
    # 1件用のpendingを上書きして質問を失わない。
    deferred_player_inputs: deque[tuple[str, str, str, str, bool, str]] = field(
        default_factory=lambda: deque(maxlen=8)
    )
    pending_voice_vocalization_at: datetime | None = None
    # panic hold ログの重複抑制（同じ文は1回だけ）
    panic_hold_logged_text: str | None = None
    # 戦闘中断中にpanicで保留している同じ発話を、毎tick OS AIへ再送しない。
    combat_input_analysis_text: str | None = None
    combat_input_analysis: CombatWorkshopInputAnalysis | None = None
    combat_input_analysis_path: str = "none"
    # player_chat 用: 直近5往復 + 粗い出来事メモ
    dialogue: DialogueContext = field(default_factory=DialogueContext)
    # 本文履歴とは別の、foreground所有権と再生完了台帳。
    foreground_dialogue: ForegroundDialogue = field(default_factory=ForegroundDialogue)
    dialogue_turns: TurnLedger = field(default_factory=TurnLedger)
    language_runtime: MainLanguageRuntime | None = None
    pending_language_results: deque[dict[str, object]] = field(
        default_factory=lambda: deque(maxlen=4)
    )
    playback_events: deque[dict[str, str]] = field(
        default_factory=lambda: deque(maxlen=128)
    )
    completed_playback_ids: deque[str] = field(
        default_factory=lambda: deque(maxlen=256)
    )
    playback_events_lock: threading.Lock = field(
        default_factory=threading.Lock,
        repr=False,
    )

    def is_stale_sequence(self, sequence: int) -> bool:
        return (
            self.last_sequence is not None
            and sequence <= self.last_sequence
            and sequence not in self.seen_sequence_set
        )

    def remember_sequence(self, sequence: int) -> bool:
        if sequence in self.seen_sequence_set:
            return True
        if len(self.seen_sequences) == self.seen_sequences.maxlen:
            old = self.seen_sequences.popleft()
            self.seen_sequence_set.discard(old)
        self.seen_sequences.append(sequence)
        self.seen_sequence_set.add(sequence)
        if self.last_sequence is None or sequence > self.last_sequence:
            self.last_sequence = sequence
        return False

    def remember_idempotency(self, key: str) -> bool:
        if key in self.seen_idempotency_set:
            return True
        if len(self.seen_idempotency) == self.seen_idempotency.maxlen:
            old = self.seen_idempotency.popleft()
            self.seen_idempotency_set.discard(old)
        self.seen_idempotency.append(key)
        self.seen_idempotency_set.add(key)
        return False

    def remember_command_result(self, command_id: str) -> bool:
        if command_id in self.seen_command_result_set:
            return True
        if len(self.seen_command_results) == self.seen_command_results.maxlen:
            old = self.seen_command_results.popleft()
            self.seen_command_result_set.discard(old)
        self.seen_command_results.append(command_id)
        self.seen_command_result_set.add(command_id)
        return False


@dataclass(slots=True)
class ProcessedEvent:
    response: AcceptedEventResponse
    actions: list[AudioAction]


class DogidoService:
    def __init__(
        self,
        settings: Settings,
        *,
        main_language_web_factory: Callable[[], object | None] | None = None,
    ) -> None:
        self.settings = settings
        self._main_language_web_factory = main_language_web_factory
        self.sessions: dict[str, SessionInfo] = {}
        self.audio = AudioDispatcher(
            settings,
            on_playback_event=self._on_audio_playback_event,
        )
        self.display = DisplayHistory(max_entries=settings.display_history_max_entries)
        self.workshop_hud = WorkshopHudSnapshots()
        self.runtime_status = RuntimeStatus(
            heartbeat_interval_ms=settings.heartbeat_interval_ms
        )
        self.diagnostics = DiagnosticHistory(
            max_entries=settings.diagnostic_history_max_entries
        )
        self.llm = DogidoLLMRouter(settings)
        self.platform_ai = PlatformStructuredAIRouter(settings)
        self.assist = build_assist_registry()
        self.memory = MemoryStore(settings.memory_dir) if settings.memory_enabled else None
        # 評価用の決定記録。会話・川柳の正本 MemoryStore とは別writer／別path。
        self.episodes = EpisodeRecorder(settings.memory_dir) if settings.memory_enabled else None
        if self.memory is not None:
            from dogido_server.catalog_readings import configure_corrections_path

            configure_corrections_path(self.memory.catalog_corrections_path)

    def warmup(self) -> None:
        runtime = self.runtime_status.snapshot()["runtime"]
        LOGGER.warning(
            "runtime_identity source=%s python_environment=%s virtual_environment=%s "
            "pid=%s instance_id=%s",
            runtime["source_label_ja"],
            runtime["python_environment"],
            runtime["virtual_environment"],
            runtime["process_id"],
            runtime["instance_id"],
        )
        LOGGER.warning(
            "assist_ready action=select_sword rule_version=%s",
            SELECT_SWORD_RULE_VERSION,
        )
        self.llm.preload()
        # 可用性確認だけ。Apple の初回推論や Foundry のモデル download はしない。
        self.platform_ai.preload()
        self.audio.prewarm_speech_texts(self._fallback_speech_catalog(self.settings.default_call_name))

    def shutdown(self) -> None:
        """端末内モデルの worker / loaded model を解放する。"""

        for session in tuple(self.sessions.values()):
            if session.language_runtime is not None:
                session.language_runtime.close()
        self.audio.close()
        self.platform_ai.close()

    @staticmethod
    def _queue_player_input(
        session: SessionInfo,
        text: str,
        *,
        source: str,
        display_text: str | None = None,
        turn_id: str = "",
        force_main_chat: bool = False,
        foreground_route: str = "",
    ) -> bool:
        """保留入力を先着順で保持する。同じ入力は重ねず、満杯ならfail-closed。"""

        value = (text or "").strip()
        normalized_source = "voice" if source == "voice" else "text"
        visible_text = (display_text or value).strip()
        if not value:
            return False
        candidate = (
            value,
            normalized_source,
            visible_text,
            str(turn_id or "")[:180],
            bool(force_main_chat),
            str(foreground_route or "")[:40],
        )
        if session.pending_player_text is None:
            session.pending_player_text = value
            session.pending_player_source = normalized_source
            session.pending_player_display_text = visible_text
            session.pending_player_turn_id = str(turn_id or "")[:180] or None
            session.pending_player_force_main_chat = bool(force_main_chat)
            session.pending_player_foreground_route = (
                str(foreground_route or "")[:40] or None
            )
            return True
        # 同じ本文は入力経路がvoice/textで異なっても一発話として扱い、
        # 最初に受けたsourceを保持する。別tickで二回答しないための境界。
        if session.pending_player_text == value:
            if force_main_chat:
                session.pending_player_turn_id = str(turn_id or "")[:180] or None
                session.pending_player_force_main_chat = True
                session.pending_player_foreground_route = (
                    str(foreground_route or "")[:40] or "casual"
                )
            return True
        for index, queued in enumerate(session.deferred_player_inputs):
            queued_text, queued_source, queued_display, *_rest = queued
            if queued_text != value:
                continue
            if force_main_chat:
                session.deferred_player_inputs[index] = (
                    queued_text,
                    queued_source,
                    queued_display,
                    str(turn_id or "")[:180],
                    True,
                    str(foreground_route or "")[:40] or "casual",
                )
            return True
        maxlen = session.deferred_player_inputs.maxlen
        if maxlen is not None and len(session.deferred_player_inputs) >= maxlen:
            LOGGER.warning(
                "player_input_queue_full session_id=%s source=%s text=%s",
                session.session_id,
                normalized_source,
                value[:80],
            )
            return False
        session.deferred_player_inputs.append(candidate)
        return True

    @staticmethod
    def _promote_deferred_player_input(session: SessionInfo) -> None:
        if session.pending_player_text is not None or not session.deferred_player_inputs:
            return
        (
            text,
            source,
            display_text,
            turn_id,
            force_main_chat,
            foreground_route,
        ) = session.deferred_player_inputs.popleft()
        session.pending_player_text = text
        session.pending_player_source = source
        session.pending_player_display_text = display_text
        session.pending_player_turn_id = turn_id or None
        session.pending_player_force_main_chat = force_main_chat
        session.pending_player_foreground_route = foreground_route or None
        LOGGER.warning(
            "player_input_promoted session_id=%s source=%s text=%s",
            session.session_id,
            source,
            text[:80],
        )

    @staticmethod
    def _session_has_queued_input(session: SessionInfo) -> bool:
        return bool((session.pending_player_text or "").strip()) or bool(
            session.deferred_player_inputs
        )

    def create_session(self, request: AdapterSessionCreateRequest) -> AdapterSessionCreateResponse:
        now = datetime.now().astimezone()
        session_id = _new_id("ses")
        machine = DogidoStateMachine(self.settings, llm=self.llm)
        session = SessionInfo(
            session_id=session_id,
            schema_version=request.schema_version,
            adapter_name=request.adapter_name,
            adapter_version=request.adapter_version,
            game=request.game,
            player_name=request.player_name,
            profile_name=request.profile_name,
            call_name=request.call_name or self.settings.default_call_name,
            capabilities=request.capabilities,
            execution_capabilities=request.execution_capabilities,
            created_at=now,
            machine=machine,
        )
        self._bind_dialogue_provider(session)
        self.sessions[session_id] = session
        self.runtime_status.adapter_seen(
            session_id,
            adapter_name=request.adapter_name,
            adapter_version=request.adapter_version,
            seen_at=now,
        )
        self.audio.prewarm_speech_texts(self._fallback_speech_catalog(request.call_name or self.settings.default_call_name))
        LOGGER.info(
            "adapter_session_created session_id=%s adapter=%s version=%s schema=%s "
            "capabilities=%s execution_capabilities=%s",
            session_id,
            request.adapter_name,
            request.adapter_version,
            request.schema_version,
            ",".join(request.capabilities) or "none",
            ",".join(request.execution_capabilities) or "none",
        )
        return AdapterSessionCreateResponse(
            session_id=session_id,
            accepted_schema_version=self.settings.accepted_schema_version,
            server_time=now,
            event_endpoint="/api/v1/game-events",
            batch_endpoint="/api/v1/game-events/batch",
            heartbeat_interval_ms=self.settings.heartbeat_interval_ms,
            max_batch_size=self.settings.max_batch_size,
        )

    def process_event(
        self,
        event: GameEvent,
        session_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> ProcessedEvent:
        session = self._ensure_session(event, session_id)
        self._drain_playback_events(session, observed_at=event.observed_at)
        if not getattr(event.meta, "call_name", None) and session.call_name:
            event = event.model_copy(
                update={
                    "meta": event.meta.model_copy(update={"call_name": session.call_name}),
                }
            )
        if not session.first_event_logged:
            LOGGER.info(
                "adapter_event_bound session_id=%s adapter=%s session_version=%s event_build=%s schema=%s",
                session.session_id,
                session.adapter_name,
                session.adapter_version,
                event.meta.adapter_build or "unset",
                event.schema_version,
            )
            session.first_event_logged = True
        session.last_seen_at = event.observed_at
        self.runtime_status.adapter_seen(
            session.session_id,
            adapter_name=session.adapter_name,
            adapter_version=session.adapter_version,
        )
        command_result_acks, observed_command_results = self._consume_command_results(
            session,
            event.command_results,
            now=datetime.now().astimezone(),
        )

        deduplicated = False
        if idempotency_key:
            deduplicated = session.remember_idempotency(idempotency_key)

        if not deduplicated and event.sequence is not None:
            if session.is_stale_sequence(event.sequence):
                LOGGER.warning(
                    "stale_sequence_skipped session_id=%s sequence=%s last_sequence=%s dimension=%s",
                    session.session_id,
                    event.sequence,
                    session.last_sequence,
                    event.player.dimension,
                )
                deduplicated = True
            else:
                deduplicated = session.remember_sequence(event.sequence)

        if deduplicated:
            response_time = datetime.now().astimezone()
            response = AcceptedEventResponse(
                accepted=True,
                event_id=_new_id("evt"),
                session_id=session.session_id,
                sequence=event.sequence,
                deduplicated=True,
                commands=self._pending_commands_for_response(session, response_time),
                acknowledged_command_ids=command_result_acks,
                server_time=response_time,
            )
            return ProcessedEvent(response=response, actions=[])

        # 保留中と同じ本文をアダプタが直接再送した場合は、直接分を一度だけ
        # 処理し、古い複製を後続tickで再回答しない。安全抑止された場合は
        # この直接分が改めて一件だけキューへ戻る。
        direct_player_text = (event.meta.user_text or "").strip()
        if direct_player_text:
            if session.pending_player_text == direct_player_text:
                session.pending_player_text = None
                session.pending_player_source = None
                session.pending_player_display_text = None
                session.pending_player_turn_id = None
                session.pending_player_force_main_chat = False
                session.pending_player_foreground_route = None
                session.combat_input_analysis_text = None
                session.combat_input_analysis = None
                session.combat_input_analysis_path = "none"
            if session.deferred_player_inputs:
                session.deferred_player_inputs = deque(
                    (
                        (text, source, display_text, turn_id, force_main, route)
                        for (
                            text,
                            source,
                            display_text,
                            turn_id,
                            force_main,
                            route,
                        ) in session.deferred_player_inputs
                        if text != direct_player_text
                    ),
                    maxlen=session.deferred_player_inputs.maxlen,
                )

        # 先行入力が前tickで処理済みなら、衝突時に退避した次の入力を昇格する。
        # アダプタが同じtickに直接本文を載せた場合は、その本文を先に扱う。
        if not (event.meta.user_text or "").strip():
            self._promote_deferred_player_input(session)

        # このフレームのservice処理が状態を変える前を、決定記録の基準点にする。
        state_before = self._episode_state_before(session)

        # 音声入力（/api/v1/player-input）はチャットと同じ user_text 経路に合流させる。
        # アダプタからのチャットが同じイベントに載っていた場合はそちらを優先し、保留分は次イベントへ
        # 川柳の自分の世界中（preface〜本句）は入力を保持し、機械には載せない
        # 本句が何らかの理由で出せず pending が張り付いたら hold を強制解除
        if session.machine.state.pending_haiku_after_preface:
            session.machine._force_clear_stuck_pending_haiku(event.observed_at)

        attached_player_text: str | None = None
        attached_player_display_text: str | None = direct_player_text or None
        attached_player_source = "text"
        attached_player_turn_id = ""
        attached_player_force_main_chat = False
        attached_player_foreground_route = ""
        combat_input_analysis: CombatWorkshopInputAnalysis | None = None
        combat_input_path = "none"
        haiku_pending_before = bool(session.machine.state.pending_haiku_after_preface)
        if haiku_pending_before:
            incoming = (event.meta.user_text or "").strip()
            if incoming:
                self._queue_player_input(
                    session,
                    incoming,
                    source="text",
                    display_text=incoming,
                )
                event.meta.user_text = None
                LOGGER.warning(
                    "player_input_held_for_haiku session_id=%s text=%s",
                    session.session_id,
                    incoming[:80],
                )
            # pending_player_text のみの場合もこのフレームでは載せない（句完了後に）
        elif session.pending_player_text and not (event.meta.user_text or "").strip():
            # panic 中は player_chat 枝が意図的に無効（絶叫優先）。
            # ここで毎 tick attach すると speech 無し → requeue → また attach のログ嵐になる。
            # 落ち着くまで pending に置いたまま次イベントを待つ。
            pending_preview = route_player_input(session.pending_player_text)
            pending_workshop_owned = bool(
                session.haiku_workshop is not None
                and is_active(session.haiku_workshop)
                and not pending_preview.requests_sword
                and should_handle_as_workshop(
                    pending_preview.raw_text,
                    verse=session.haiku_workshop.editing_line(),
                    player_input=pending_preview,
                )
            )
            hold_input_for_safety = bool(
                (
                    pending_preview.knowledge_query is not None
                    or pending_workshop_owned
                )
                and (
                    session.machine.state.mode
                    in {"alert", "panic", "suppressed_panic"}
                    or event.event.name
                    in {EventName.PLAYER_DIED, EventName.COMBAT_ENDED}
                    or bool(event.combat.combat_active_hint)
                    or bool(event.visual_threats)
                    or bool(event.auditory_threats)
                    or session.machine._detect_warden_sonic_boom(event)
                )
            )
            if hold_input_for_safety:
                pending_text = session.pending_player_text or ""
                if session.panic_hold_logged_text != pending_text:
                    session.panic_hold_logged_text = pending_text
                    LOGGER.warning(
                        "player_input_held_for_safety session_id=%s mode=%s text=%s",
                        session.session_id,
                        session.machine.state.mode,
                        pending_text[:80],
                    )
            else:
                paused_workshop = (
                    session.haiku_workshop
                    if session.haiku_workshop is not None
                    and session.haiku_workshop.combat_paused
                    else None
                )
                if paused_workshop is not None:
                    pending_combat_text = session.pending_player_text or ""
                    if (
                        session.combat_input_analysis_text == pending_combat_text
                        and session.combat_input_analysis is not None
                    ):
                        combat_input_analysis = session.combat_input_analysis
                        combat_input_path = session.combat_input_analysis_path
                    else:
                        combat_input_analysis, combat_input_path = (
                            self._analyze_combat_workshop_input(
                                paused_workshop,
                                pending_combat_text,
                            )
                        )
                        session.combat_input_analysis_text = pending_combat_text
                        session.combat_input_analysis = combat_input_analysis
                        session.combat_input_analysis_path = combat_input_path
                paused_workshop_input = bool(
                    combat_input_analysis is not None
                    and combat_input_analysis.action
                    in {"resume_workshop", "workshop_input"}
                )
                paused_workshop_close = bool(
                    paused_workshop is not None
                    and classify_workshop_intent(
                        session.pending_player_text or "",
                        verse=paused_workshop.editing_line(),
                    )
                    == "close"
                )
                pending_assist_text = session.pending_player_text or ""
                if (session.pending_player_source or "text") == "voice":
                    pending_assist_text = (
                        interpret_voice_select_sword_request(pending_assist_text)
                        or pending_assist_text
                    )
                explicit_assist_request = (
                    is_explicit_voice_select_sword_request(pending_assist_text)
                    if (session.pending_player_source or "text") == "voice"
                    else is_explicit_select_sword_request(pending_assist_text)
                )
                if (
                    session.machine.state.mode in {"panic", "suppressed_panic"}
                    and not paused_workshop_input
                    and not paused_workshop_close
                    and not explicit_assist_request
                ):
                    # 同じ文の hold は1回だけログ（毎 tick は出さない）
                    pending_text = session.pending_player_text or ""
                    if session.panic_hold_logged_text != pending_text:
                        session.panic_hold_logged_text = pending_text
                        LOGGER.warning(
                            "player_input_held_for_panic session_id=%s mode=%s text=%s",
                            session.session_id,
                            session.machine.state.mode,
                            pending_text[:80],
                        )
                else:
                    attached_player_text = session.pending_player_text
                    attached_player_display_text = (
                        session.pending_player_display_text or attached_player_text
                    )
                    attached_player_source = session.pending_player_source or "text"
                    attached_player_turn_id = session.pending_player_turn_id or ""
                    attached_player_force_main_chat = session.pending_player_force_main_chat
                    attached_player_foreground_route = (
                        session.pending_player_foreground_route or ""
                    )
                    event.meta.user_text = attached_player_text
                    session.pending_player_text = None
                    session.pending_player_source = None
                    session.pending_player_display_text = None
                    session.pending_player_turn_id = None
                    session.pending_player_force_main_chat = False
                    session.pending_player_foreground_route = None
                    session.combat_input_analysis_text = None
                    session.combat_input_analysis = None
                    session.combat_input_analysis_path = "none"
                    LOGGER.warning(
                        "player_input_attached_after_hold session_id=%s text=%s",
                        session.session_id,
                        attached_player_text[:80],
                    )

        # 固定表は本文へ、現在語彙による音近傍補正は解釈面だけへ載せる。
        # adapter の typed chat は source=text のため、音近傍補正しない。
        # 剣assistは voice-only の閉じた補正後も同じ明示命令ガードを再適用する。
        event, interpreted_player_text = self._apply_contextual_asr_to_event(
            event,
            session=session,
            input_source=attached_player_source,
        )
        routed_player_input = self._route_assist_player_input(
            session,
            event.meta.user_text,
            interpreted_player_text=interpreted_player_text,
            input_source=attached_player_source,
        )
        if direct_player_text and self.settings.audio_enabled:
            # adapter chatも、すでに受理済みの本人発話なら再生中の返答へ
            # barge-inできる。requeueされた保留入力では二度止めない。
            self.audio.interrupt_for_player_input()

        # player主体の会話所有権を、状態機械の安全判断とは別に同期する。
        # 国語・一般知識の限定対話だけは非同期workerへ渡し、game-event直列処理を塞がない。
        conversation_now = event.observed_at
        foreground = session.foreground_dialogue
        foreground_ttl_ms = self.settings.conversation_active_ttl_ms
        if foreground.route == "web" and session.language_runtime is not None:
            # 調査開始後は独立対話で既に使っている30分の読書期限を正にする。
            # 同意待ち／起動待ちにはresearchがないため、通常の5分期限のまま。
            research_ttl_ms = session.language_runtime.active_research_ttl_ms()
            if research_ttl_ms is not None:
                foreground_ttl_ms = research_ttl_ms
        foreground.expire_if_idle(
            conversation_now,
            ttl_ms=foreground_ttl_ms,
        )
        if (
            foreground.route == "haiku_workshop"
            and not is_active(session.haiku_workshop)
            and not session.machine.state.pending_haiku_after_preface
        ):
            foreground.clear()
        conversation_danger = self._conversation_danger_active(session, event)
        if session.pending_voice_vocalization_at is not None:
            vocalized_at = session.pending_voice_vocalization_at
            session.pending_voice_vocalization_at = None
            close_in_time = abs((conversation_now - vocalized_at).total_seconds()) <= 5.0
            session.dialogue.add_digest(
                "situation",
                self._observed_situation_note(event) if close_in_time else "状況：驚いた声を検出。原因は不明。",
                at=conversation_now,
            )
            if not conversation_danger:
                session.dialogue.end_danger_retention(
                    player_turns=self.settings.conversation_post_danger_player_turns,
                )
        if conversation_danger and not foreground.combat_active:
            session.dialogue.begin_danger_retention()
            foreground.suspend_for_combat(
                hold_player_turns=self.settings.conversation_suspended_player_turns,
            )
            if session.language_runtime is not None:
                session.language_runtime.interrupt_for_combat()
            # worker内だけでなく、すでにservice待ち列へ到着した旧返答も捨てる。
            if session.language_runtime is not None:
                for pending_result in session.pending_language_results:
                    session.language_runtime.discard_undelivered_result(
                        pending_result,
                        resolution="combat",
                    )
            session.pending_language_results.clear()

        combat_chat_ack = ""
        delegated_language_turn = False
        runtime_attention_result: dict[str, object] | None = None
        if session.language_runtime is not None:
            session.language_runtime.expire_pending_address(conversation_now)
        if (
            session.language_runtime is not None
            and not attached_player_force_main_chat
            and self._is_general_conversation_input(session, routed_player_input)
        ):
            runtime_attention_result = (
                session.language_runtime.handle_pending_address_input(
                    routed_player_input.semantic_text,
                    source=attached_player_source,
                    observed_at=conversation_now,
                )
            )
            if runtime_attention_result is not None and runtime_attention_result.get(
                "consumed"
            ):
                if runtime_attention_result.get("reply"):
                    self._enqueue_main_language_result(
                        session,
                        runtime_attention_result,
                    )
                host_request = runtime_attention_result.get("host_chat_request")
                if isinstance(host_request, dict):
                    if not self._queue_host_chat_request(session, host_request):
                        session.language_runtime.reject_host_chat_request(
                            str(host_request.get("turn_id") or ""),
                            resolution="host_chat_queue_full",
                        )
                delegated_language_turn = True
                event = event.model_copy(
                    update={"meta": event.meta.model_copy(update={"user_text": None})}
                )
                routed_player_input = route_player_input(None)
        current_general_input = self._is_general_conversation_input(
            session,
            routed_player_input,
        )
        if conversation_danger and current_general_input:
            combat_chat_ack = foreground.note_combat_chat_attempt(
                conversation_now,
                cooldown_ms=self.settings.combat_chat_ack_cooldown_ms,
            )
            event = event.model_copy(
                update={"meta": event.meta.model_copy(update={"user_text": None})}
            )
            routed_player_input = route_player_input(None)
        elif (
            not delegated_language_turn
            and not attached_player_force_main_chat
            and self._main_language_should_handle(session, routed_player_input)
        ):
            runtime = session.language_runtime
            if runtime is not None:
                accepted, _turn_id = runtime.submit_turn(
                    routed_player_input.semantic_text,
                    source=attached_player_source,
                    observed_at=conversation_now,
                    raw_text=attached_player_display_text or routed_player_input.raw_text,
                )
                if accepted:
                    if foreground.suspended is not None:
                        foreground.consume_suspended_player_turn(
                            resumes=(
                                foreground.suspended.route == "learning"
                                and self._explicit_topic_resume(
                                    routed_player_input.semantic_text
                                )
                            ),
                            now=conversation_now,
                        )
                    delegated_language_turn = True
                    event = event.model_copy(
                        update={"meta": event.meta.model_copy(update={"user_text": None})}
                    )
                    routed_player_input = route_player_input(None)
                else:
                    # busy中の知識質問を雑談へ誤配送しない。現在入力は一度だけ
                    # 待ち列へ戻し、workerが空いた次tickで再判定する。
                    self._queue_player_input(
                        session,
                        routed_player_input.raw_text,
                        source=attached_player_source,
                        display_text=attached_player_display_text,
                    )
                    event = event.model_copy(
                        update={"meta": event.meta.model_copy(update={"user_text": None})}
                    )
                    routed_player_input = route_player_input(None)
        # casualの所有権・保留turn消費は、状態機械が実際の返答を選べた後に
        # 確定する。雷・夕方で同じ入力を再queueした時に二重計上しない。

        # ambient 抑止: まだ相乗りしていない話しかけがキューにある
        session.machine.player_input_queued = self._session_has_queued_input(session)

        # 脅威が来たフレームは、古いpinをtimeoutで先に消さない。状態機械が
        # 戦況を確定した直後に、句を保持したまま戦闘中断へ移す。
        workshop_danger = self._event_interrupts_workshop(event)
        if not (
            session.haiku_workshop is not None
            and (session.haiku_workshop.combat_paused or workshop_danger)
        ):
            session.haiku_workshop = maybe_close_for_time(
                session.haiku_workshop,
                now=event.observed_at,
            )
        if session.haiku_workshop is not None and not session.haiku_workshop.open:
            LOGGER.warning(
                "haiku_workshop_closed session_id=%s reason=%s",
                session.session_id,
                session.haiku_workshop.close_reason,
            )
            session.haiku_workshop = None

        suspended_overlay = session.foreground_dialogue.suspended_prompt(
            routed_player_input.semantic_text
        )
        session.dialogue.set_prompt_overlay(suspended_overlay)
        try:
            machine_result = session.machine.process(
                event,
                interpreted_user_text=interpreted_player_text,
                player_input_context=routed_player_input,
            )
        finally:
            session.dialogue.clear_prompt_overlay()
        if machine_result.haiku_emission is not None:
            session.last_haiku_emission = machine_result.haiku_emission
            # memory の有無に関わらず pin を立てる（entry_id は memory 側で埋める）
            if session.haiku_workshop is None or (
                session.haiku_workshop.display_line()
                != (
                    machine_result.haiku_emission.reading_text
                    or machine_result.haiku_emission.text
                    or ""
                ).strip()
            ):
                self._open_haiku_workshop(
                    session,
                    machine_result.haiku_emission,
                    entry_id=None,
                    now=event.observed_at,
                )
        # 本句完了フレームでは hold 中の入力を次フレームで確実に載せる（ログで追えるようにする）
        if (
            haiku_pending_before
            and not session.machine.state.pending_haiku_after_preface
            and session.pending_player_text
        ):
            LOGGER.warning(
                "player_input_ready_after_haiku session_id=%s text=%s",
                session.session_id,
                session.pending_player_text[:80],
            )
        actions = list(machine_result.actions)
        if combat_chat_ack:
            actions.append(
                AudioAction(
                    layer="speech",
                    interrupt=False,
                    text=combat_chat_ack,
                    speech_profile="battle",
                    queue_priority="foreground",
                    route_owner="combat_chat_ack",
                )
            )
        # このtickで戦闘pauseへ遷移すると、更新後のworkshop判定は入力を
        # 所有しなくなる。遷移前の所有権を保存し、同時に届いた句編集・
        # 現在句質問を安全発話の後へ必ず戻す。
        workshop_owns_input = bool(
            session.haiku_workshop is not None
            and not session.machine.player_input.requests_sword
            and session.machine._haiku_workshop_should_handle_player_input()
        )
        combat_actions, workshop_input_consumed, replace_noncombat_speech = (
            self._update_workshop_combat_state(
                session,
                event,
                actions,
                state_mode=machine_result.state.mode,
                input_analysis=combat_input_analysis,
                input_analysis_path=combat_input_path,
            )
        )
        if replace_noncombat_speech:
            # 安定した単独敵をプレイヤー判断で無視する場合だけ、同じ入力への
            # 通常chatを置き換える。panic cue / callout は安全のため残す。
            actions = [action for action in actions if action.layer != "speech"]
        actions.extend(combat_actions)
        issued_commands, assist_actions = self._assist_actions(
            session,
            event,
            actions,
            now=datetime.now().astimezone(),
        )
        actions.extend(assist_actions)
        actions.extend(self._command_result_feedback_actions(observed_command_results, actions))
        # workshopの後段返答で、安全発話・dimension flush・adapter実行結果を
        # 一括置換しない。既存actionが優先されるtickでは入力を待ち列へ戻す。
        workshop_input_preempted = bool(
            workshop_owns_input
            and (
                event.event.name in {EventName.PLAYER_DIED, EventName.COMBAT_ENDED}
                or machine_result.state.mode in {"alert", "panic", "suppressed_panic"}
                or bool(event.combat.combat_active_hint)
                or bool(event.visual_threats)
                or bool(event.auditory_threats)
                or session.machine._detect_warden_sonic_boom(event)
                or any(action.interrupt for action in actions)
                or any(
                    action.layer == "speech" and bool(action.text)
                    for action in actions
                )
            )
        )
        if workshop_input_preempted:
            deferred_workshop_text = (routed_player_input.raw_text or "").strip()
            if deferred_workshop_text:
                self._queue_player_input(
                    session,
                    deferred_workshop_text,
                    source=attached_player_source,
                    display_text=attached_player_display_text,
                )
                LOGGER.warning(
                    "workshop_input_deferred_after_preemption "
                    "session_id=%s mode=%s text=%s",
                    session.session_id,
                    machine_result.state.mode,
                    deferred_workshop_text[:80],
                )
        workshop_input_enabled = (
            not workshop_input_consumed
            and not workshop_input_preempted
            and not bool(
                session.haiku_workshop is not None
                and session.haiku_workshop.combat_paused
            )
        )
        memory_actions = self._memory_actions(
            session,
            event,
            actions,
            machine_result.haiku_emission,
            allow_player_input=(
                workshop_input_enabled
                and not session.machine.player_input.requests_sword
                and not (
                    routed_player_input.knowledge_query is not None
                    and event.event.name
                    in {EventName.PLAYER_DIED, EventName.COMBAT_ENDED}
                )
            ),
        )
        # workshop 返事があるときは player_chat と二重にしない（講評を優先）
        if memory_actions and any(a.layer == "speech" and a.text for a in memory_actions):
            if session.machine._haiku_workshop_should_handle_player_input():
                actions = [
                    a
                    for a in actions
                    if not (a.layer == "speech" and a.text)
                ]
        actions.extend(memory_actions)

        # 完了済みの国語対話結果は安全発話を追い越さない。危険中・割り込みtickは
        # 小さなsession内待ち列へ戻し、epoch不一致の結果はruntime側で破棄する。
        actions.extend(
            self._collect_main_language_actions(
                session,
                event,
                existing_actions=actions,
                conversation_danger=conversation_danger,
            )
        )

        casual_reply_registered = self._register_main_player_reply(
            session,
            routed_player_input,
            actions,
            now=conversation_now,
            source=attached_player_source,
            turn_id=attached_player_turn_id,
            route_override=attached_player_foreground_route,
        )
        if (
            casual_reply_registered
            and not is_active(session.haiku_workshop)
            and session.machine._casual_haiku_due_at_player_boundary(
                event,
                conversation_now,
            )
        ):
            # 絶え間ない雑談でも、現在の返答を選び終えた境界で発句を続ける。
            # 同じbatchの後ろへ置くため、マイク途中や現在再生中の文は切らない。
            preface = session.machine._begin_prefaced_haiku(event, conversation_now)
            preface_actions = session.machine._speech_actions(
                preface,
                protect_ms=6000,
                speech_profile="haiku",
            )
            for action in preface_actions:
                action.route_owner = "auto_haiku_preface"
            actions.extend(preface_actions)

        if (
            session.machine.state.pending_haiku_after_preface
            and session.foreground_dialogue.route == "casual"
        ):
            session.foreground_dialogue.suspend(
                "auto_haiku",
                hold_player_turns=self.settings.conversation_suspended_player_turns,
            )
            session.foreground_dialogue.activate(
                "haiku_workshop",
                now=conversation_now,
            )
        elif is_active(session.haiku_workshop):
            if session.foreground_dialogue.route in {"casual", "learning"}:
                session.foreground_dialogue.suspend(
                    "haiku_workshop",
                    hold_player_turns=self.settings.conversation_suspended_player_turns,
                )
            session.foreground_dialogue.activate("haiku_workshop", now=conversation_now)

        if (
            session.foreground_dialogue.combat_active
            and not machine_result.combat_active
            and event.event.name in {EventName.COMBAT_ENDED, EventName.PLAYER_DIED}
        ):
            post_combat = session.foreground_dialogue.finish_combat()
            session.dialogue.end_danger_retention(
                player_turns=self.settings.conversation_post_danger_player_turns,
            )
            if session.language_runtime is not None:
                session.language_runtime.release_after_combat()
            if post_combat and event.event.name == EventName.COMBAT_ENDED:
                relief = next(
                    (
                        action
                        for action in actions
                        if action.layer == "speech"
                        and action.cue_id == "aftermath_relief"
                    ),
                    None,
                )
                if relief is not None:
                    relief.text = " ".join(
                        value for value in (relief.text or "", post_combat) if value
                    )
                    relief.route_owner = "combat_chat_aftermath"
                else:
                    actions.append(
                        AudioAction(
                            layer="speech",
                            interrupt=False,
                            text=post_combat,
                            speech_profile="peace",
                            queue_priority="foreground",
                            route_owner="combat_chat_aftermath",
                        )
                    )

        if any(action.defer_player_input for action in actions):
            session.foreground_dialogue.note_interrupt(
                "thunder"
                if (
                    session.machine._has_recent_nearby_lightning(event)
                    or session.machine._has_recent_thunder_sound(event)
                )
                else "dusk"
            )

        # 状態機械process後に確定した正本DBのlearning開始や、worker結果による
        # route変更も、このevent時刻から発句時計へ反映する。
        session.machine._sync_haiku_interval_pause(conversation_now)
        self._assign_playback_identity(session, actions)

        # 警戒・戦闘・死亡・時限警告など、高優先発話に先送りされた質問を
        # 失わない。DB回答済み／現在句として処理済みなら再キューしない。
        if (
            routed_player_input.knowledge_query is not None
            and not session.machine.knowledge_query_handled
        ):
            deferred_text = (routed_player_input.raw_text or "").strip()
            if deferred_text and self._queue_player_input(
                session,
                deferred_text,
                source=attached_player_source,
                display_text=attached_player_display_text,
            ):
                LOGGER.warning(
                    "knowledge_question_deferred_after_preemption "
                    "session_id=%s mode=%s text=%s",
                    session.session_id,
                    machine_result.state.mode,
                    deferred_text[:80],
                )
        self._update_dialogue_context(session, event, actions)
        # 句と無関係な speech が出た（通常 chat）→ drift
        self._note_workshop_after_actions(session, event, actions)

        # 話しかけをイベントに載せたが speech が出なかった場合は捨てずに再キュー
        # （ambient_mob 枝や panic 枝に食われたケースの取りこぼし防止）
        if (
            (attached_player_text or direct_player_text)
            and not delegated_language_turn
            and not session.machine.player_input.requests_sword
            and self._should_requeue_player_input(session, actions)
        ):
            requeue_text = (
                attached_player_text
                or direct_player_text
                or routed_player_input.raw_text
            )
            if self._queue_player_input(
                session,
                requeue_text,
                source=attached_player_source,
                display_text=attached_player_display_text,
                turn_id=attached_player_turn_id,
                force_main_chat=attached_player_force_main_chat,
                foreground_route=attached_player_foreground_route,
            ):
                LOGGER.warning(
                    "player_input_requeued session_id=%s mode=%s text=%s",
                    session.session_id,
                    machine_result.state.mode,
                    requeue_text[:80],
                )
        self._promote_deferred_player_input(session)

        output_flags = self._output_flags(actions)
        response_time = datetime.now().astimezone()
        response = AcceptedEventResponse(
            accepted=True,
            event_id=_new_id("evt"),
            session_id=session.session_id,
            sequence=event.sequence,
            deduplicated=False,
            state=StateResponse(mode=machine_result.state.mode, combat_active=machine_result.combat_active),
            outputs=output_flags,
            commands=self._pending_commands_for_response(session, response_time),
            acknowledged_command_ids=command_result_acks,
            server_time=response_time,
        )
        self._record_episode(
            session=session,
            event=event,
            response=response,
            state_before=state_before,
            machine_result=machine_result,
            actions=actions,
            output_flags=output_flags,
            interpreted_user_text=interpreted_player_text,
            adapter_commands=issued_commands,
            command_results=observed_command_results,
        )
        player_text_for_display = (
            attached_player_display_text or routed_player_input.raw_text or ""
        ).strip()
        if player_text_for_display:
            for action in actions:
                if (
                    action.layer == "speech"
                    and bool(action.text)
                    and action.cue_id != "aftermath_relief"
                    and not action.defer_player_input
                ):
                    action.display_player_input_text = player_text_for_display
                    break
        return ProcessedEvent(response=response, actions=actions)

    def _route_assist_player_input(
        self,
        session: SessionInfo,
        raw_text: str | None,
        *,
        interpreted_player_text: str | None,
        input_source: str = "text",
    ) -> PlayerInputContext:
        routed = route_player_input(
            raw_text,
            interpreted_text=interpreted_player_text,
        )
        if routed.normalized_text.startswith("/"):
            return routed
        command_text = routed.normalized_text
        if (
            input_source == "voice"
            and not routed.requests_sword
            and interpreted_player_text
            and is_explicit_voice_select_sword_request(routed.interpreted_text)
        ):
            command_text = routed.interpreted_text
            routed = replace(
                routed,
                requests_sword=True,
                assist_intent_source="code_voice_asr",
                assist_intent_evidence=command_text,
                assist_intent_confidence=1.0,
            )
        # 明示知識質問は会話専用で、世界操作の意図分類へ渡さない。
        # 「ダイヤモンドの剣の耐久値は？」のような質問を、曖昧剣分類が
        # 持ち替え要求へ昇格させる余地を閉じる。
        if routed.knowledge_query is not None:
            return replace(
                routed,
                requests_sword=False,
                assist_intent_source="none",
                assist_intent_evidence="",
                assist_intent_confidence=0.0,
            )
        workshop_owns_ambiguous_sword = bool(
            session.haiku_workshop is not None
            and session.haiku_workshop.open
            and not session.haiku_workshop.combat_paused
            and not (
                routed.assist_intent_source == "code_voice_asr"
                and is_unambiguous_voice_select_sword_request(command_text)
            )
            and not is_unambiguous_select_sword_request(command_text)
        )
        if workshop_owns_ambiguous_sword:
            if routed.requests_sword:
                return replace(
                    routed,
                    requests_sword=False,
                    assist_intent_source="none",
                    assist_intent_evidence="",
                    assist_intent_confidence=0.0,
                )
            return routed
        if routed.requests_sword:
            return routed

        # 通常会話を毎回分類しない。「剣」に触れた曖昧形だけQwen chat routeへ。
        player_text = (
            routed.semantic_text
            if input_source == "voice"
            else (routed.normalized_text or routed.raw_text)
        ).strip()
        if (
            not player_text
            or not mentions_sword_target(player_text)
            or is_bare_sword_target(player_text)
        ):
            return routed
        if not self.llm.route_enabled("chat"):
            return routed
        fallback = {
            "intent": "other",
            "weapon_kind": "unknown",
            "is_request": False,
            "evidence": "",
            "confidence": 0.0,
        }
        try:
            payload = self.llm.generate_structured_json(
                StructuredGenerationRequest(
                    kind="assist_select_sword_intent",
                    fallback_value=fallback,
                    details={"player_text": player_text},
                    temperature=0.0,
                    route="chat",
                    max_tokens=96,
                )
            )
        except Exception as exc:  # fail closed。会話自体は通常経路へ戻す。
            LOGGER.warning("assist_select_sword_intent_failed detail=%s", exc)
            return routed
        intent = finalize_select_sword_intent_payload(payload, player_text=player_text)
        if not intent.requested:
            return routed
        LOGGER.warning(
            "assist_intent action=select_sword source=%s confidence=%.2f evidence=%s",
            intent.source,
            intent.confidence,
            intent.evidence[:80],
        )
        return replace(
            routed,
            requests_sword=True,
            assist_intent_source=intent.source,
            assist_intent_evidence=intent.evidence,
            assist_intent_confidence=intent.confidence,
        )

    def _assist_actions(
        self,
        session: SessionInfo,
        event: GameEvent,
        existing_actions: list[AudioAction],
        *,
        now: datetime,
    ) -> tuple[list[SelectHotbarCommand], list[AudioAction]]:
        if not session.machine.player_input.requests_sword:
            return [], []

        already_pending = next(
            (
                command
                for command in session.pending_commands.values()
                if command.type == "select_hotbar" and command.expires_at > now
            ),
            None,
        )
        if already_pending is not None:
            if any(action.layer == "speech" and action.text for action in existing_actions):
                return [], []
            return [], [AudioAction(layer="speech", interrupt=False, text="いま持ち替え中やで！")]

        if (
            session.last_select_sword_command_at is not None
            and (now - session.last_select_sword_command_at).total_seconds() < 3.0
        ):
            if any(action.layer == "speech" and action.text for action in existing_actions):
                return [], []
            return [], [AudioAction(layer="speech", interrupt=False, text="もう持ち替えたで！")]

        context = ActionContext(
            event=event,
            execution_capabilities=frozenset(session.execution_capabilities),
            now=now,
        )
        dispatch = self.assist.propose(ActionName.SELECT_SWORD, context)
        response_actions: list[AudioAction] = []
        has_speech = any(action.layer == "speech" and action.text for action in existing_actions)
        if dispatch.command is None:
            if not has_speech:
                if dispatch.detail_code == "capability_missing":
                    text = "今のアダプターでは、まだ持ち替え操作が使えへんで。"
                else:
                    text = "剣も代わりの武器も、ホットバーにないで！"
                response_actions.append(AudioAction(layer="speech", interrupt=False, text=text))
            return [], response_actions

        command = dispatch.command
        session.pending_commands[command.command_id] = command
        session.last_select_sword_command_at = now
        selection = select_weapon_slot(event.player.hotbar.slots if event.player.hotbar else [])
        if not has_speech:
            text = (
                "剣がない！ これでどうや！？"
                if selection is not None and selection.used_fallback
                else "剣やな、持ち替えるで！"
            )
            response_actions.append(AudioAction(layer="speech", interrupt=False, text=text))
        LOGGER.warning(
            "assist_command_issued session_id=%s command_id=%s type=%s slot=%s expected_item=%s "
            "intent_source=%s confidence=%.2f",
            session.session_id,
            command.command_id,
            command.type,
            command.slot,
            command.expected_item_id,
            session.machine.player_input.assist_intent_source,
            session.machine.player_input.assist_intent_confidence,
        )
        return [command], response_actions

    def _consume_command_results(
        self,
        session: SessionInfo,
        results: list[AdapterCommandResult],
        *,
        now: datetime,
    ) -> tuple[list[str], list[AdapterCommandResult]]:
        acknowledged: list[str] = []
        observed: list[AdapterCommandResult] = []
        for incoming in results:
            acknowledged.append(incoming.command_id)
            if session.remember_command_result(incoming.command_id):
                continue
            command = session.pending_commands.pop(incoming.command_id, None)
            result = incoming
            if (
                command is not None
                and incoming.status == "succeeded"
            ):
                if not (command.issued_at <= incoming.executed_at < command.expires_at):
                    result = incoming.model_copy(
                        update={
                            "status": "failed",
                            "detail_code": "result_outside_validity_window",
                        }
                    )
                elif (
                    incoming.selected_slot != command.slot
                    or incoming.selected_item_id != command.expected_item_id
                ):
                    result = incoming.model_copy(
                        update={"status": "failed", "detail_code": "result_mismatch"}
                    )
            if command is None:
                LOGGER.warning(
                    "assist_command_result_unknown session_id=%s command_id=%s status=%s",
                    session.session_id,
                    incoming.command_id,
                    incoming.status,
                )
            else:
                LOGGER.warning(
                    "assist_command_result session_id=%s command_id=%s status=%s detail=%s",
                    session.session_id,
                    incoming.command_id,
                    result.status,
                    result.detail_code or "-",
                )
                if result.status != "succeeded":
                    session.last_select_sword_command_at = None
            observed.append(result)

        for command_id, command in list(session.pending_commands.items()):
            if command.expires_at > now:
                continue
            session.pending_commands.pop(command_id, None)
            if session.remember_command_result(command_id):
                continue
            observed.append(
                AdapterCommandResult(
                    command_id=command_id,
                    command_type=command.type,
                    status="expired",
                    executed_at=now,
                    detail_code="server_result_timeout",
                )
            )
            session.last_select_sword_command_at = None
        return list(dict.fromkeys(acknowledged)), observed

    def _pending_commands_for_response(
        self,
        session: SessionInfo,
        now: datetime,
    ) -> list[SelectHotbarCommand]:
        return sorted(
            (
                command
                for command in session.pending_commands.values()
                if command.expires_at > now
            ),
            key=lambda command: (command.issued_at, command.command_id),
        )

    def _command_result_feedback_actions(
        self,
        results: list[AdapterCommandResult],
        existing_actions: list[AudioAction],
    ) -> list[AudioAction]:
        if any(action.layer == "speech" and action.text for action in existing_actions):
            return []
        failure = next((result for result in results if result.status != "succeeded"), None)
        if failure is None:
            return []
        if failure.status == "expired":
            text = "持ち替えが間に合わへんかったわ。もう一回言うてな。"
        elif failure.detail_code in {"expected_item_mismatch", "result_mismatch"}:
            text = "手元が変わったから、勝手に別の枠へは替えんかったで。"
        else:
            text = "うまく持ち替えられへんかったわ。手元を確認してな。"
        return [AudioAction(layer="speech", interrupt=False, text=text)]

    def _episode_state_before(self, session: SessionInfo) -> dict[str, object]:
        workshop = session.haiku_workshop
        return {
            "mode": session.machine.state.mode,
            "pending_haiku_after_preface": session.machine.state.pending_haiku_after_preface,
            "player_input_queued": self._session_has_queued_input(session),
            "workshop": (
                "combat_paused"
                if workshop is not None and workshop.combat_paused
                else "open"
                if workshop is not None and workshop.open
                else "none"
            ),
        }

    def _record_episode(
        self,
        *,
        session: SessionInfo,
        event: GameEvent,
        response: AcceptedEventResponse,
        state_before: dict[str, object],
        machine_result: StateMachineResult,
        actions: list[AudioAction],
        output_flags: OutputFlags,
        interpreted_user_text: str | None,
        adapter_commands: list[SelectHotbarCommand],
        command_results: list[AdapterCommandResult],
    ) -> None:
        if self.episodes is None:
            return
        try:
            self.episodes.record(
                event=event,
                event_id=response.event_id,
                session_id=session.session_id,
                state_before=state_before,
                mode_after=machine_result.state.mode,
                combat_active=machine_result.combat_active,
                actions=actions,
                output_flags=output_flags,
                haiku_emitted=machine_result.haiku_emission is not None,
                interpreted_user_text=interpreted_user_text,
                adapter_commands=adapter_commands,
                command_results=command_results,
                recorded_at=response.server_time,
            )
        except Exception as exc:  # recorder自体の不具合もリアルタイム経路へ伝播させない
            LOGGER.warning(
                "episode_record_failed session_id=%s sequence=%s detail=%s",
                session.session_id,
                event.sequence,
                exc,
            )

    def process_batch(
        self,
        events: Iterable[GameEvent],
        session_id: str | None = None,
    ) -> tuple[BatchAcceptedResponse, list[AudioAction]]:
        processed = 0
        deduplicated = 0
        actions: list[AudioAction] = []
        commands_by_id: dict[str, SelectHotbarCommand] = {}
        acknowledged_command_ids: list[str] = []

        for event in events:
            result = self.process_event(event, session_id=session_id)
            if result.response.deduplicated:
                deduplicated += 1
            else:
                processed += 1
                actions.extend(result.actions)
            for command in result.response.commands:
                commands_by_id[command.command_id] = command
            acknowledged_command_ids.extend(result.response.acknowledged_command_ids)

        response = BatchAcceptedResponse(
            accepted=True,
            received=processed + deduplicated,
            processed=processed,
            deduplicated=deduplicated,
            commands=list(commands_by_id.values()),
            acknowledged_command_ids=list(dict.fromkeys(acknowledged_command_ids)),
            server_time=datetime.now().astimezone(),
        )
        return response, actions

    def heartbeat(self, session_id: str, last_sequence: int | None) -> HeartbeatResponse:
        session = self.sessions[session_id]
        session.last_seen_at = datetime.now().astimezone()
        self.runtime_status.adapter_seen(
            session_id,
            adapter_name=session.adapter_name,
            adapter_version=session.adapter_version,
            seen_at=session.last_seen_at,
        )
        if last_sequence is not None:
            session.last_sequence = last_sequence
        return HeartbeatResponse(
            ok=True,
            session_id=session_id,
            server_time=datetime.now().astimezone(),
        )

    def close_session(self, session_id: str) -> CloseSessionResponse:
        session = self.sessions.pop(session_id, None)
        if session is not None and session.language_runtime is not None:
            session.language_runtime.close()
        self.runtime_status.adapter_closed(session_id)
        return CloseSessionResponse(ok=True, session_id=session_id)

    def dispatch_actions(
        self,
        actions: list[AudioAction],
        *,
        session_id: str | None = None,
    ) -> None:
        """確定済み本文を表示履歴へ残し、音声が有効なら再生を依頼する。"""

        if not actions:
            return
        resolved_session_id = session_id
        if not resolved_session_id:
            action_sessions = {
                action.playback_session_id
                for action in actions
                if action.playback_session_id
            }
            if len(action_sessions) == 1:
                resolved_session_id = next(iter(action_sessions))
        if resolved_session_id:
            for action in actions:
                if action.layer == "speech" and action.text and not action.utterance_id:
                    action.utterance_id = "service-speech:" + uuid4().hex
                if action.utterance_id and not action.playback_session_id:
                    action.playback_session_id = resolved_session_id
        self.display.record_actions(
            actions,
            session_id=resolved_session_id,
            audio_requested=self.settings.audio_enabled,
        )
        if self.settings.audio_enabled:
            if resolved_session_id and resolved_session_id in self.sessions:
                session = self.sessions[resolved_session_id]
                runtime = session.language_runtime
                for action in actions:
                    if not action.utterance_id or not action.conversation_turn_id:
                        continue
                    if runtime is not None:
                        runtime.mark_dispatched(action.utterance_id)
                    else:
                        session.dialogue_turns.dispatched(action.utterance_id)
            self.audio.play_actions(actions)

    def publish_workshop_hud(self) -> None:
        """Called on the service worker only; a display failure must not fail an action."""
        try:
            self.workshop_hud.publish(self.sessions)
        except Exception:
            self.workshop_hud.clear()
            LOGGER.exception("workshop_hud_projection_failed")

    def _mark_workshop_hud_edit(
        self, workshop: RecentHaikuWorkshop, selected_line: int | None,
    ) -> None:
        workshop.hud_editing = True
        workshop.hud_selected_line = selected_line
        self.publish_workshop_hud()

    def display_snapshot(self, *, session_id: str | None = None) -> dict[str, object]:
        """ゲーム外画面へ、発言・参考資料・診断ログを一つのsnapshotで返す。"""

        snapshot = self.display.snapshot(session_id=session_id)
        diagnostic = self.diagnostics.snapshot()
        snapshot["diagnostic_schema_version"] = diagnostic["schema_version"]
        snapshot["diagnostic_revision"] = diagnostic["revision"]
        snapshot["diagnostics"] = diagnostic["entries"]
        snapshot["diagnostic_retention"] = diagnostic["retention"]
        runtime_status = self.runtime_status.snapshot()
        snapshot["runtime_revision"] = runtime_status["revision"]
        snapshot["runtime"] = runtime_status["runtime"]
        snapshot["minecraft"] = runtime_status["minecraft"]
        return snapshot

    def record_voice_input_diagnostic(
        self,
        payload: VoiceInputDiagnosticRequest,
    ) -> dict[str, object]:
        """別プロセスのSTT診断を、起動中だけの診断履歴と端末へ写す。"""
        from dogido_server.llm.sanitize import summarize_for_log

        parts = ["voice_input", f"event={payload.event}"]
        if payload.prompt_mode is not None:
            parts.append(f"prompt_mode={payload.prompt_mode}")
        if payload.duration_ms is not None:
            parts.append(f"duration_ms={payload.duration_ms}")
        if payload.reason:
            parts.append(f"reason={summarize_for_log(payload.reason)}")
        if payload.recognized_text is not None:
            parts.append(f"text={summarize_for_log(payload.recognized_text)}")
        if payload.detail:
            parts.append(f"detail={summarize_for_log(payload.detail)}")
        message = " ".join(parts)
        entry_id = self.diagnostics.record(
            level=payload.level,
            logger="dogido.voice_input",
            source="voice_input",
            event=payload.event,
            message=message,
        )
        level = {
            "info": logging.INFO,
            "warning": logging.WARNING,
            "error": logging.ERROR,
        }[payload.level]
        if not (payload.event == "capture" and payload.reason == "aec_levels"
                and payload.level == "info"):
            LOGGER.log(
                level,
                message,
                extra={"dogido_diagnostic_skip": True},
            )
        return {"accepted": entry_id is not None, "entry_id": entry_id}

    def voice_input_context(self) -> VoiceInputContextResponse:
        """直近セッションが川柳workshop中かだけを音声認識へ公開する。"""

        if not self.sessions:
            return VoiceInputContextResponse()
        session = max(
            self.sessions.values(),
            key=lambda candidate: candidate.last_seen_at or datetime.min.replace(tzinfo=timezone.utc),
        )
        prompt_mode = "haiku_workshop" if is_open(session.haiku_workshop) else "normal"
        return VoiceInputContextResponse(
            prompt_mode=prompt_mode,
            session_id=session.session_id,
        )

    def push_player_input(self, text: str, *, source: str = "text") -> dict[str, object]:
        """音声入力などゲーム外からのプレイヤー発話を、直近のアクティブセッションへ届ける。"""
        from dogido_server.player_input.normalize import (
            is_known_voice_noise_text,
            normalize_player_text,
        )
        from dogido_server.player_input.voice_vocalization import (
            is_pure_voice_vocalization,
        )

        original = (text or "").strip()
        if not original:
            return {"accepted": False, "reason": "empty_text"}
        # STT 既知誤変換を入口で直し、ログには補正後を載せる（#29）
        # 視線先文脈は次の game-event 相乗り時に _apply_contextual_asr_to_event で補強
        normalized = normalize_player_text(original)
        if not normalized:
            return {"accepted": False, "reason": "empty_text"}
        input_source = "voice" if str(source).strip().lower() == "voice" else "text"
        if input_source == "voice" and is_known_voice_noise_text(normalized):
            LOGGER.warning(
                "player_input_rejected reason=noise_text text=%s",
                normalized[:80],
            )
            return {"accepted": False, "reason": "noise_text"}
        if not self.sessions:
            return {"accepted": False, "reason": "no_active_session"}
        session = max(
            self.sessions.values(),
            key=lambda candidate: candidate.last_seen_at or datetime.min.replace(tzinfo=timezone.utc),
        )
        if input_source == "voice" and is_pure_voice_vocalization(normalized):
            observed_at = datetime.now().astimezone()
            session.pending_voice_vocalization_at = observed_at
            session.dialogue.begin_danger_retention()
            self.diagnostics.record(
                level="INFO",
                logger="dogido.voice_input",
                source="voice_input",
                event="player_vocalization",
                message=f"voice_vocalization text={original}",
                created_at=observed_at,
            )
            if self.settings.audio_enabled:
                self.audio.interrupt_for_player_input()
            return {
                "accepted": True,
                "session_id": session.session_id,
                "reason": "situation_vocalization",
            }
        existing = (session.pending_player_text or "").strip()
        existing_preview = route_player_input(existing) if existing else None
        preserve_existing = bool(
            existing_preview is not None
            and (
                existing_preview.knowledge_query is not None
                or (
                    is_active(session.haiku_workshop)
                    and session.haiku_workshop is not None
                    and should_handle_as_workshop(
                        existing_preview.raw_text,
                        verse=session.haiku_workshop.editing_line(),
                        player_input=existing_preview,
                    )
                )
            )
        )
        if preserve_existing:
            if not self._queue_player_input(
                session,
                normalized,
                source=input_source,
                display_text=original,
            ):
                return {"accepted": False, "reason": "queue_full"}
        else:
            # 従来のvoice endpointは未処理の一般発話を最新値で置換する。
            # 知識質問・workshop入力だけは上の分岐で先着順に保全する。
            session.pending_player_text = normalized
            session.pending_player_source = input_source
            session.pending_player_display_text = original
            session.pending_player_turn_id = None
            session.pending_player_force_main_chat = False
            session.pending_player_foreground_route = None
            session.combat_input_analysis_text = None
            session.combat_input_analysis = None
            session.combat_input_analysis_path = "none"
        if original != normalized:
            LOGGER.warning(
                "player_input_pushed session_id=%s source=%s text=%s (stt_raw=%s)",
                session.session_id,
                input_source,
                normalized[:80],
                original[:80],
            )
        else:
            LOGGER.warning(
                "player_input_pushed session_id=%s source=%s text=%s",
                session.session_id,
                input_source,
                normalized[:80],
            )
        if self.settings.audio_enabled:
            self.audio.interrupt_for_player_input()
        return {"accepted": True, "session_id": session.session_id}

    def _conversation_danger_active(
        self,
        session: SessionInfo,
        event: GameEvent,
    ) -> bool:
        if event.event.name == EventName.COMBAT_ENDED:
            return False
        return bool(
            session.foreground_dialogue.combat_active
            or session.machine.state.mode in {"alert", "panic", "suppressed_panic"}
            or self._event_interrupts_workshop(event)
        )

    @staticmethod
    def _observed_situation_note(event: GameEvent) -> str:
        """叫び声の字面を使わず、同時点のコード観測だけを短く投影する。"""

        outcomes = tuple(event.combat.hostile_outcomes or ())
        if event.event.name == EventName.CREEPER_DETONATED or any(
            outcome.outcome == "creeper_detonation"
            and outcome.evidence == "explosion_packet"
            for outcome in outcomes
        ):
            return "状況：クリーパーの爆発をコードで観測。"
        if event.event.name == EventName.PLAYER_DIED:
            return "状況：プレイヤーの死亡をコードで観測。原因の詳細は未確定。"
        if event.visual_threats:
            return "状況：近くの敵対モブを視認。"
        if event.auditory_threats:
            return "状況：敵らしい音を観測。具体名は未確定。"
        return "状況：驚いた声を検出。原因は不明。"

    @staticmethod
    def _explicit_topic_resume(text: str) -> bool:
        normalized = " ".join((text or "").replace("\n", " ").split())
        return any(
            marker in normalized
            for marker in (
                "さっきの話",
                "前の話",
                "話の続き",
                "続き話",
                "続きやけど",
                "続きを",
                "戻るけど",
                "戻ろ",
            )
        )

    def _is_general_conversation_input(
        self,
        session: SessionInfo,
        player_input: PlayerInputContext,
    ) -> bool:
        text = (player_input.semantic_text or "").strip()
        if not text or (player_input.normalized_text or "").startswith("/"):
            return False
        if (
            player_input.wants_quiet
            or player_input.asks_hostile_count
            or player_input.asks_hostile_direction
            or player_input.asks_dragon_direction
            or player_input.asks_save_last_haiku
            or player_input.asks_inventory
            or player_input.requests_sword
            or player_input.knowledge_query is not None
            or player_input.player_haiku_text is not None
            or player_input.revised_haiku_text is not None
            or player_input.reading_correction is not None
            or player_input.asks_haiku_recall
        ):
            return False
        workshop = session.haiku_workshop
        if (
            is_active(workshop)
            and workshop is not None
            and should_handle_as_workshop(
                player_input.raw_text,
                verse=workshop.editing_line(),
                player_input=player_input,
            )
        ):
            return False
        return True

    def _main_language_should_handle(
        self,
        session: SessionInfo,
        player_input: PlayerInputContext,
    ) -> bool:
        if session.language_runtime is None:
            return False
        if not self._is_general_conversation_input(session, player_input):
            return False
        foreground = session.foreground_dialogue
        if foreground.route in {"learning", "web"}:
            return True
        if foreground.suspended is not None and foreground.suspended.route == "learning":
            return bool(
                self._explicit_topic_resume(player_input.semantic_text)
                or self._looks_like_main_language_request(player_input.semantic_text)
            )
        return self._looks_like_main_language_request(player_input.semantic_text)

    @staticmethod
    def _looks_like_main_language_request(text: str) -> bool:
        """既存Minecraft雑談を奪わない、国語・語句質問の狭い入口。"""

        normalized = " ".join((text or "").replace("\n", " ").split())
        if not normalized:
            return False
        explicit_patterns = (
            "ってどういう意味",
            "って何て読む",
            "ってなんて読む",
            "の読み方",
            "言葉の意味",
            "ことばの意味",
            "何年生で習",
            "何年生の漢字",
        )
        if any(pattern in normalized for pattern in explicit_patterns):
            return True
        language_topics = (
            "漢字",
            "短歌",
            "俳句",
            "川柳",
            "音数",
            "文法",
            "熟語",
            "ことわざ",
            "枕詞",
            "語句",
        )
        if not any(topic in normalized for topic in language_topics):
            return False
        return any(
            marker in normalized
            for marker in (
                "?",
                "？",
                "教えて",
                "知りたい",
                "調べて",
                "勉強",
                "習う",
                "習った",
                "話をしよう",
                "話しよう",
                "話そう",
            )
        )

    def _advance_or_begin_casual_topic(
        self,
        session: SessionInfo,
        text: str,
        *,
        now: datetime,
    ) -> None:
        foreground = session.foreground_dialogue
        if foreground.suspended is not None:
            foreground.consume_suspended_player_turn(
                resumes=(
                    foreground.suspended.route == "casual"
                    and self._explicit_topic_resume(text)
                ),
                now=now,
            )
        foreground.activate("casual", now=now, player_text=text)

    def _collect_main_language_actions(
        self,
        session: SessionInfo,
        event: GameEvent,
        *,
        existing_actions: list[AudioAction],
        conversation_danger: bool,
    ) -> list[AudioAction]:
        runtime = session.language_runtime
        if runtime is None:
            return []
        for envelope in runtime.poll():
            if envelope.get("work_kind") == "turn":
                result = runtime.accept_turn_result(envelope, observed_at=event.observed_at)
                self._remember_main_web_return_context(
                    session,
                    result,
                    observed_at=event.observed_at,
                )
                host_request = result.get("host_chat_request")
                if isinstance(host_request, dict):
                    if not self._queue_host_chat_request(session, host_request):
                        runtime.reject_host_chat_request(
                            str(host_request.get("turn_id") or ""),
                            resolution="host_chat_queue_full",
                        )
                if result.get("reply"):
                    self._enqueue_main_language_result(session, result)
            elif envelope.get("work_kind") == "playback_control":
                # Web制御は通常のplayer turnではない。失敗案内等があれば
                # 案内専用turnとして実再生完了まで追跡する。
                result = runtime.accept_control_result(
                    envelope,
                    observed_at=event.observed_at,
                )
                if result.get("reply"):
                    self._enqueue_main_language_result(session, result)

        unsafe = bool(
            conversation_danger
            or event.visual_threats
            or event.auditory_threats
            or session.machine.state.mode in {"alert", "panic", "suppressed_panic"}
            or any(action.interrupt for action in existing_actions)
            or any(action.layer == "speech" and action.text for action in existing_actions)
            or session.foreground_dialogue.route == "haiku_workshop"
        )
        if unsafe or not session.pending_language_results:
            return []
        row = session.pending_language_results.popleft()
        turn_id = str(row.get("turn_id") or "")
        semantic_text = str(row.get("semantic_text") or row.get("raw_text") or "")
        if (
            turn_id
            and semantic_text
            and str(row.get("status") or "") != "address_confirmation_requested"
        ):
            session.dialogue.add_player(
                semantic_text,
                at=event.observed_at,
                turn_id=turn_id,
            )
        references: list[SpeechReference] = []
        for fact in row.get("references", []) if isinstance(row.get("references"), list) else []:
            if not isinstance(fact, dict):
                continue
            sources = fact.get("sources") if isinstance(fact.get("sources"), list) else []
            first = next((source for source in sources if isinstance(source, dict)), {})
            references.append(
                SpeechReference(
                    source_id=str(fact.get("id") or "language-dialogue")[:160],
                    title_ja=str(first.get("title_ja") or fact.get("title_ja") or "参考資料")[:160],
                    citation_label_ja=str(fact.get("title_ja") or "参考資料")[:160],
                    locator=str(first.get("locator") or "")[:200],
                    url=str(first.get("url") or "")[:1000],
                    source_kind="language_dialogue",
                )
            )
        return [
            AudioAction(
                layer="speech",
                interrupt=False,
                text=str(row.get("reply") or ""),
                speech_profile="peace",
                queue_priority="foreground",
                queue_replace_key="main_language_reply",
                references=tuple(references),
                display_player_input_text=str(row.get("raw_text") or ""),
                utterance_id=str(row.get("utterance_id") or ""),
                conversation_turn_id=str(row.get("turn_id") or ""),
                route_owner="main_language_dialogue",
            )
        ]

    @staticmethod
    def _remember_main_web_return_context(
        session: SessionInfo,
        result: dict[str, object],
        *,
        observed_at: datetime,
    ) -> None:
        """Web本文を持ち帰らず、調べた問い一件だけを本体の短期文脈へ渡す。"""

        context = result.get("return_context")
        if not isinstance(context, dict):
            return
        topic = " ".join(str(context.get("researched_topic") or "").split())[:160]
        if not topic:
            return
        session.dialogue.add_digest(
            "research",
            f"直前にプレイヤーと『{topic}』をWebで調べた。",
            at=observed_at,
        )

    def _queue_host_chat_request(
        self,
        session: SessionInfo,
        request: dict[str, object],
    ) -> bool:
        """国語workerが返した同じ入力を、IDを保ったまま本体chatへ一度だけ戻す。"""

        text = str(request.get("text") or "").strip()
        turn_id = str(request.get("turn_id") or "").strip()
        if not text or not turn_id:
            return False
        return self._queue_player_input(
            session,
            text,
            source=str(request.get("source") or "text"),
            display_text=str(request.get("raw_text") or text),
            turn_id=turn_id,
            force_main_chat=True,
            foreground_route=str(request.get("foreground_route") or "casual"),
        )

    def _enqueue_main_language_result(
        self,
        session: SessionInfo,
        result: dict[str, object],
    ) -> None:
        pending = session.pending_language_results
        maxlen = pending.maxlen
        if maxlen is not None and len(pending) >= maxlen:
            evicted = pending.popleft()
            if session.language_runtime is not None:
                session.language_runtime.discard_undelivered_result(
                    evicted,
                    resolution="result_queue_replaced",
                )
            LOGGER.warning(
                "main_language_result_replaced session_id=%s turn_id=%s",
                session.session_id,
                str(evicted.get("turn_id") or "")[:160],
            )
        pending.append(result)

    def _register_main_player_reply(
        self,
        session: SessionInfo,
        player_input: PlayerInputContext,
        actions: list[AudioAction],
        *,
        now: datetime,
        source: str,
        turn_id: str = "",
        route_override: str = "",
    ) -> bool:
        route = route_override if route_override in {"casual", "learning"} else ""
        if not route and any(action.route_owner == "player_chat" for action in actions):
            route = "casual"
        elif not route and self._is_general_conversation_input(session, player_input):
            route = "casual"
        elif (
            player_input.knowledge_query is not None
            and session.machine.knowledge_query_handled
        ):
            # 正本DBの即答は従来どおり状態機械が所有するが、会話上は
            # learningとして扱い、follow-up中の自動川柳を止める。
            route = "learning"
        if not route:
            return False
        reply = next(
            (
                action
                for action in actions
                if action.layer == "speech"
                and bool(action.text)
                and not action.defer_player_input
                and action.route_owner != "main_language_dialogue"
                and action.cue_id != "aftermath_relief"
            ),
            None,
        )
        if reply is None:
            return False
        if route == "casual":
            self._advance_or_begin_casual_topic(
                session,
                player_input.semantic_text,
                now=now,
            )
        else:
            foreground = session.foreground_dialogue
            if foreground.suspended is not None:
                foreground.consume_suspended_player_turn(
                    resumes=(
                        foreground.suspended.route == "learning"
                        and self._explicit_topic_resume(player_input.semantic_text)
                    ),
                    now=now,
                )
            foreground.activate("learning", now=now, player_text=player_input.semantic_text)
        turn_id = turn_id or ("main-chat:" + uuid4().hex)
        epoch = session.language_runtime.epoch if session.language_runtime is not None else 0
        session.dialogue_turns.begin(
            turn_id,
            epoch=epoch,
            raw_text=player_input.raw_text,
            semantic_text=player_input.semantic_text,
            source=source,
        )
        session.dialogue_turns.routed(
            turn_id,
            route=route,
            status="player_chat" if route == "casual" else "knowledge_db",
        )
        if route == "learning" and session.language_runtime is not None:
            # controller内部履歴は独立試験との互換用。本体解釈の正は同じ台帳投影。
            session.language_runtime.observe_external_user_turn(
                turn_id,
                player_input.semantic_text,
                source=source,
            )
        session.dialogue.add_player(
            player_input.semantic_text,
            at=now,
            turn_id=turn_id,
        )
        utterance_id = reply.utterance_id or ("main-chat-reply:" + uuid4().hex)
        reply.utterance_id = utterance_id
        reply.conversation_turn_id = turn_id
        reply.route_owner = "player_chat" if route == "casual" else "knowledge_dialogue"
        session.dialogue_turns.select_reply(
            turn_id,
            reply=reply.text or "",
            utterance_id=utterance_id,
        )
        return route == "casual"

    @staticmethod
    def _assign_playback_identity(
        session: SessionInfo,
        actions: list[AudioAction],
    ) -> None:
        """process結果の時点で発話とsessionを結び、暗黙sessionでも失わない。"""

        for action in actions:
            if action.layer == "speech" and action.text and not action.utterance_id:
                action.utterance_id = "service-speech:" + uuid4().hex
            if action.utterance_id and not action.playback_session_id:
                action.playback_session_id = session.session_id

    def _on_audio_playback_event(self, event: dict[str, str]) -> None:
        session_id = event.get("session_id", "")
        session = self.sessions.get(session_id)
        if session is None:
            return
        # audio workerでは状態正本を変更せず、次の直列game-eventで消費する。
        # 非終端のqueued/startedは、満杯時にcompleted等を押し出さない。
        with session.playback_events_lock:
            maxlen = session.playback_events.maxlen
            if maxlen is not None and len(session.playback_events) >= maxlen:
                if event.get("status") not in {"completed", "failed", "cancelled"}:
                    LOGGER.warning(
                        "playback_event_queue_dropped session_id=%s status=%s",
                        session_id,
                        event.get("status", ""),
                    )
                    return
                evict_index = next(
                    (
                        index
                        for index, queued in enumerate(session.playback_events)
                        if queued.get("status") not in {"completed", "failed", "cancelled"}
                    ),
                    0,
                )
                del session.playback_events[evict_index]
                LOGGER.warning(
                    "playback_event_queue_replaced session_id=%s incoming_status=%s",
                    session_id,
                    event.get("status", ""),
                )
            session.playback_events.append(dict(event))

    def _drain_playback_events(self, session: SessionInfo, *, observed_at: datetime) -> None:
        with session.playback_events_lock:
            events = list(session.playback_events)
            session.playback_events.clear()
        for event in events:
            resolved = None
            if session.language_runtime is not None:
                resolved = session.language_runtime.playback_event(
                    event,
                    observed_at=observed_at,
                )
            else:
                status = event.get("status", "")
                utterance_id = event.get("utterance_id", "")
                if status == "queued":
                    session.dialogue_turns.queued(utterance_id)
                elif status == "started":
                    session.dialogue_turns.started(utterance_id)
                elif status in {"completed", "failed", "cancelled"}:
                    resolved = session.dialogue_turns.resolve(
                        utterance_id,
                        status,
                        resolution=event.get("resolution", ""),
                    )
                if status == "completed" and resolved is not None:
                    session.foreground_dialogue.note_completed_turn(
                        str(resolved.get("turn_id") or ""),
                        str(resolved.get("semantic_text") or ""),
                        str(resolved.get("selected_reply") or ""),
                        route=str(resolved.get("route") or "casual"),
                        at=observed_at,
                    )
            utterance_id = str(event.get("utterance_id") or "")
            if (
                event.get("status") == "completed"
                and event.get("text")
                and utterance_id
                and utterance_id not in session.completed_playback_ids
                and resolved is not None
                and str(resolved.get("route") or "") in {"casual", "learning"}
            ):
                session.completed_playback_ids.append(utterance_id)
                session.dialogue.add_dogido(
                    str(resolved.get("selected_reply") or event["text"]),
                    at=observed_at,
                    turn_id=str(resolved.get("turn_id") or ""),
                )

    def _apply_contextual_asr_to_event(
        self,
        event: GameEvent,
        *,
        session: SessionInfo,
        input_source: str,
    ) -> tuple[GameEvent, str | None]:
        """固定補正後の本文と、voice限定候補の文脈解釈面を返す。"""
        from dogido_server.player_input.asr_fixes import apply_contextual_asr_fixes
        from dogido_server.player_input.contextual_asr import (
            apply_candidate_asr_fixes,
            workshop_asr_candidates,
        )

        raw = (event.meta.user_text or "").strip()
        if not raw:
            return event, None
        look_name = None
        if event.look_target is not None and event.look_target.name:
            look_name = str(event.look_target.name)
        fixed, applied = apply_contextual_asr_fixes(
            raw,
            look_name=look_name,
            held_item=event.player.held_item,
            inventory=event.inventory,
        )
        fixed_event = event
        if applied and fixed != raw:
            LOGGER.warning(
                "asr_fix_context applied=%s original=%s fixed=%s look=%s held=%s",
                ",".join(f"{w}->{r}" for w, r in applied),
                raw[:80],
                fixed[:80],
                look_name or "-",
                (event.player.held_item or "-")[:40],
            )
            fixed_event = event.model_copy(
                update={"meta": event.meta.model_copy(update={"user_text": fixed})}
            )

        if input_source == "voice":
            assist_interpreted = interpret_voice_select_sword_request(fixed)
            if assist_interpreted is not None and assist_interpreted != fixed:
                LOGGER.warning(
                    "asr_fix_assist session_id=%s original=%s interpreted=%s "
                    "rule=select_sword_homophone rule_version=%s",
                    session.session_id,
                    fixed[:100],
                    assist_interpreted[:100],
                    SELECT_SWORD_RULE_VERSION,
                )
                return fixed_event, assist_interpreted

        workshop = session.haiku_workshop
        if input_source != "voice" or not is_active(workshop) or workshop is None:
            return fixed_event, None
        candidates = workshop_asr_candidates(
            verse=workshop.editing_line(),
            materials=dict(workshop.materials or {}),
        )
        interpreted, contextual = apply_candidate_asr_fixes(fixed, candidates)
        if not contextual or interpreted == fixed:
            return fixed_event, None
        LOGGER.warning(
            "asr_fix_conversation session_id=%s original=%s interpreted=%s applied=%s",
            session.session_id,
            fixed[:100],
            interpreted[:100],
            ",".join(
                f"{row.original}->{row.replacement}@{row.candidate_source}:d{row.distance}"
                for row in contextual
            ),
        )
        return fixed_event, interpreted

    def _should_requeue_player_input(self, session: SessionInfo, actions: list[AudioAction]) -> bool:
        """相乗りした話しかけに対する speech が無ければ再キューする。"""
        player_input = session.machine.player_input
        if not player_input.breaks_silence:
            return False
        if player_input.wants_quiet:
            return False
        # 川柳保存・直し・読み訂正・想起などは memory 側で返事する場合がある
        if (
            player_input.asks_save_last_haiku
            or player_input.player_haiku_text
            or player_input.revised_haiku_text
            or player_input.reading_correction is not None
            or player_input.asks_haiku_recall
        ):
            return False
        if any(action.defer_player_input for action in actions):
            return True
        # 戦闘終了の安堵はプレイヤー発話への返事ではない。同じイベントに
        # 音声入力が相乗りした場合は、次イベントへ戻して会話も取りこぼさない。
        has_player_reply = any(
            bool(action.text)
            and action.layer == "speech"
            and action.cue_id != "aftermath_relief"
            for action in actions
        )
        return not has_player_reply

    def _event_interrupts_workshop(self, event: GameEvent) -> bool:
        return event_interrupts_workshop(
            event,
            recent_damage_window_ms=self.settings.recent_damage_window_ms,
        )

    def _update_workshop_combat_state(
        self,
        session: SessionInfo,
        event: GameEvent,
        actions: list[AudioAction],
        *,
        state_mode: str,
        input_analysis: CombatWorkshopInputAnalysis | None = None,
        input_analysis_path: str = "none",
    ) -> tuple[list[AudioAction], bool, bool]:
        workshop = session.haiku_workshop
        semantic_text = (session.machine.player_input.semantic_text or "").strip()
        if (
            workshop is not None
            and workshop.combat_paused
            and semantic_text
            and input_analysis is None
        ):
            input_analysis, input_analysis_path = self._analyze_combat_workshop_input(
                workshop,
                semantic_text,
            )
        if input_analysis is None:
            input_analysis = CombatWorkshopInputAnalysis()
        if workshop is not None and workshop.combat_paused and semantic_text:
            LOGGER.warning(
                "haiku_workshop_combat_input session_id=%s action=%s confidence=%.2f "
                "path=%s evidence=%s player=%s",
                session.session_id,
                input_analysis.action,
                input_analysis.confidence,
                input_analysis_path,
                input_analysis.evidence[:80] or "-",
                semantic_text[:100],
            )
        update = update_workshop_combat_state(
            workshop,
            event,
            raw_player_text=session.machine.player_input.raw_text,
            input_action=input_analysis.action,
            input_present=bool(
                semantic_text
                or (
                    session.combat_input_analysis_text
                    and session.pending_player_text
                )
            ),
            state_mode=state_mode,
            has_speech=any(action.layer == "speech" and action.text for action in actions),
            stable_since=session.machine.state.stalled_visual_started_at,
            recent_damage_window_ms=self.settings.recent_damage_window_ms,
            combat_clear_time_ms=self.settings.combat_clear_time_ms,
            resume_delay_ms=self.settings.workshop_low_threat_resume_delay_ms,
            session_id=session.session_id,
        )
        if update.closed:
            session.haiku_workshop = None
        added = (
            [AudioAction(layer="speech", interrupt=False, text=update.reply_text)]
            if update.reply_text
            else []
        )
        return added, update.consume_player_input, update.replace_speech

    def _analyze_combat_workshop_input(
        self,
        workshop: RecentHaikuWorkshop,
        player_text: str,
    ) -> tuple[CombatWorkshopInputAnalysis, str]:
        """OS AIで再開意思を抽出する。安全判定・終了・保存は行わない。"""

        details = build_combat_workshop_input_details(workshop, player_text)
        try:
            payload = self.platform_ai.generate_structured_json(
                StructuredGenerationRequest(
                    kind="haiku_workshop_combat_input",
                    fallback_value={
                        "action": "uncertain",
                        "confidence": 0.0,
                        "evidence": "",
                    },
                    details=details,
                    temperature=0.0,
                    route="chat",
                    max_tokens=120,
                ),
                fallback=self.llm,
            )
            analysis = finalize_combat_workshop_input_payload(
                payload,
                player_text=player_text,
            )
            provider = str(payload.get("__dogido_platform_ai_provider") or "llm")
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("haiku_workshop_combat_input_failed detail=%s", exc)
            analysis = CombatWorkshopInputAnalysis()
            provider = "failed"
        if analysis.action != "uncertain":
            return analysis, provider
        fallback = fallback_combat_workshop_input_analysis(workshop, player_text)
        if fallback.action != "uncertain":
            return fallback, "rule_fallback"
        return analysis, provider

    @staticmethod
    def _reading_correction_is_grounded_in_workshop(
        workshop: RecentHaikuWorkshop | None,
        correction: object | None,
    ) -> bool:
        """省略形「AはB」を、現在句の既知ラベルに一致するときだけ優先する。"""

        if not is_active(workshop) or workshop is None or correction is None:
            return False
        surface = str(getattr(correction, "surface", "") or "").strip()
        if not surface:
            return False
        materials = dict(workshop.materials or {})
        candidates = {
            str(materials.get(key) or "").strip()
            for key in ("biome_ja", "structure_ja", "held_item")
            if materials.get(key)
        }
        for row in materials.get("catalog_sources") or []:
            if isinstance(row, dict):
                candidates.add(str(row.get("label") or "").strip())
        for row in materials.get("source_atoms") or []:
            if isinstance(row, dict) and row.get("kind") == "catalog_label":
                candidates.add(str(row.get("text") or "").strip())
        return surface in candidates

    def _update_dialogue_context(
        self,
        session: SessionInfo,
        event: GameEvent,
        actions: list[AudioAction],
    ) -> None:
        """会話5往復と出来事メモを session.dialogue に積む。"""
        now = event.observed_at
        # 状態機械が積んだ粗い出来事
        notes = list(session.machine.state.pending_dialogue_notes)
        if notes:
            session.dialogue.extend_digest(notes, kind="event", at=now)
            session.machine.state.pending_dialogue_notes.clear()

        # 通常会話のplayer側は、実際に返答を所有したturn IDが確定した時だけ
        # 登録する。警告中の叫び・requeue・workshop入力を通常履歴へ混ぜない。
        # assistant側は実playback completed後だけ登録する。

    def list_haiku_memory(self) -> list[dict[str, object]]:
        if self.memory is None:
            return []
        return self.memory.list_haiku_entries()

    def memory_profile(self, player_name: str | None = None) -> dict[str, object]:
        if self.memory is None:
            return {}
        return self.memory.load_profile(player_name)

    def memory_startup_summary(self) -> dict[str, object]:
        if self.memory is None:
            return {}
        return self.memory.load_rolling_summary()

    def _memory_actions(
        self,
        session: SessionInfo,
        event: GameEvent,
        actions: list[AudioAction],
        haiku_emission: HaikuEmission | None,
        *,
        allow_player_input: bool = True,
    ) -> list[AudioAction]:
        player_input = session.machine.player_input
        extra_actions: list[AudioAction] = []
        if self.memory is None:
            reading_is_explicit = bool(
                player_input.reading_correction is not None
                and (
                    getattr(player_input.reading_correction, "explicit", False)
                    or self._reading_correction_is_grounded_in_workshop(
                        session.haiku_workshop,
                        player_input.reading_correction,
                    )
                )
            )
            formal_memory_action = allow_player_input and (
                player_input.asks_save_last_haiku
                or player_input.player_haiku_text
                or player_input.revised_haiku_text
                or reading_is_explicit
                or player_input.asks_haiku_recall
            )
            if formal_memory_action:
                extra_actions.append(AudioAction(layer="speech", interrupt=False, text="記憶機能は今止まっとるで。"))
                return extra_actions
            # 省略形「AはB」は workshop の自然文を横取りしない。pin の返事を
            # 先に試し、扱えなかった場合だけ読み訂正として失敗を伝える。
            if allow_player_input:
                extra_actions.extend(self._haiku_workshop_actions(session, event))
            if (
                allow_player_input
                and not extra_actions
                and player_input.reading_correction is not None
            ):
                extra_actions.append(AudioAction(layer="speech", interrupt=False, text="記憶機能は今止まっとるで。"))
            return extra_actions

        try:
            advancement_ids = self._event_advancement_ids(event)
            if advancement_ids:
                self.memory.record_progress(event.player.name, advancement_ids, event.observed_at)
            if player_input.normalized_text and not player_input.normalized_text.startswith("/"):
                self.memory.append_player_input(event, session.session_id, player_input.raw_text)
            if haiku_emission is not None:
                self.memory.append_haiku_emission(session.session_id, haiku_emission)
                # 発句は珍しいので、基本すべて長期記憶へ（明示保存を待たない）
                entry, _ = self.memory.save_agent_haiku(haiku_emission)
                entry_id = str(entry.get("id") or "") or None
                if session.haiku_workshop is not None and session.haiku_workshop.open:
                    session.haiku_workshop.entry_id = entry_id or session.haiku_workshop.entry_id
                else:
                    self._open_haiku_workshop(
                        session,
                        haiku_emission,
                        entry_id=entry_id,
                        now=event.observed_at,
                    )
            for action in actions:
                if not action.text:
                    continue
                if haiku_emission is not None and self._action_contains_haiku(action, haiku_emission):
                    continue
                self.memory.append_speech_action(event, session.session_id, action)
            if allow_player_input:
                extra_actions.extend(self._memory_input_actions(session, event))
            for action in extra_actions:
                if action.text:
                    self.memory.append_speech_action(event, session.session_id, action)
        except OSError as exc:
            LOGGER.warning("memory_write_failed session_id=%s detail=%s", session.session_id, exc)
        return extra_actions

    def _memory_input_actions(self, session: SessionInfo, event: GameEvent) -> list[AudioAction]:
        assert self.memory is not None
        player_input = session.machine.player_input

        if player_input.reading_correction is not None and (
            bool(getattr(player_input.reading_correction, "explicit", False))
            or self._reading_correction_is_grounded_in_workshop(
                session.haiku_workshop,
                player_input.reading_correction,
            )
        ):
            return self._handle_reading_correction(session, event, player_input.reading_correction)

        if player_input.revised_haiku_text:
            return self._save_haiku_revision_reply(
                session,
                event,
                player_input.revised_haiku_text,
                source="formal",
            )

        if player_input.player_haiku_text:
            _, created = self.memory.save_player_haiku(event, player_input.player_haiku_text)
            text = "プレイヤーの川柳、保存したで。" if created else "その川柳はもう保存してあるで。"
            return [AudioAction(layer="speech", interrupt=False, text=text)]

        if player_input.asks_save_last_haiku:
            if session.last_haiku_emission is None:
                return [AudioAction(layer="speech", interrupt=False, text="まだ保存できる句がないで。")]
            _, created = self.memory.save_agent_haiku(session.last_haiku_emission)
            text = "今の句、保存したで。" if created else "今の句はもう保存してあるで。"
            return [AudioAction(layer="speech", interrupt=False, text=text)]

        if player_input.asks_haiku_recall:
            return self._handle_haiku_recall(session, event, player_input)

        # H5.2: 明示で soft lesson を緩める（workshop open 外でも可）
        raw = (player_input.raw_text or "").strip()
        if wants_clear_haiku_lessons(raw):
            return self._clear_haiku_lessons_reply(session, event)

        # 川柳 workshop（pin が open のとき、自然な突っ込みを優先）
        workshop_actions = self._haiku_workshop_actions(session, event)
        if workshop_actions:
            return workshop_actions

        # 「草地はくさち」の省略形は通常時だけ。workshop 中の自然な提案は
        # 会話モデルによる意味抽出を先に通し、読み訂正へ誤保存しない。
        if player_input.reading_correction is not None:
            return self._handle_reading_correction(session, event, player_input.reading_correction)

        return []

    def _clear_haiku_lessons_reply(
        self,
        session: SessionInfo,
        event: GameEvent,
    ) -> list[AudioAction]:
        if self.memory is None:
            return [AudioAction(layer="speech", interrupt=False, text="記憶機能は今止まっとるで。")]
        loosen = loosen_all_lessons()
        try:
            self.memory.save_haiku_lesson(
                lesson_type=str(loosen.get("lesson_type") or "*"),
                note=str(loosen.get("note") or ""),
                prefer_materials=bool(loosen.get("prefer_materials")),
                observed_at=event.observed_at,
                polarity=str(loosen.get("polarity") or "loosen"),
                strength=float(loosen.get("strength") or 0.0),
            )
        except OSError as exc:
            LOGGER.warning("haiku_lesson_clear_failed detail=%s", exc)
            return [AudioAction(layer="speech", interrupt=False, text="ちょっと保存に失敗したわ。")]
        LOGGER.warning("haiku_lessons_cleared session_id=%s", session.session_id)
        return [AudioAction(layer="speech", interrupt=False, text="おけ、前の注意は気にせんでええわ。")]

    def _open_haiku_workshop(
        self,
        session: SessionInfo,
        emission: HaikuEmission,
        *,
        entry_id: str | None,
        now: datetime,
    ) -> None:
        if is_open(session.haiku_workshop):
            close_workshop(session.haiku_workshop, reason="next_haiku")
        # 発句側で厚い materials（motifs/held/nearby/fragment_links）があればそれを使う。
        # 無い古い emission 向けに薄いフォールバックだけここで組み立てる。
        materials: dict[str, object] = dict(getattr(emission, "materials", None) or {})
        if not materials:
            if emission.interpretation:
                materials["interpretation"] = emission.interpretation
            if emission.biome:
                materials["biome"] = emission.biome
                try:
                    from dogido_server.entry_catalog import biome_labels

                    bid = str(emission.biome).removeprefix("minecraft:")
                    ja = biome_labels().get(bid) or biome_labels().get(str(emission.biome))
                    if ja:
                        materials["biome_ja"] = ja
                except Exception:  # noqa: BLE001
                    pass
            if emission.structure:
                materials["structure"] = emission.structure
                try:
                    from dogido_server.entry_catalog import structure_labels

                    sid = str(emission.structure).removeprefix("minecraft:")
                    ja = structure_labels().get(sid) or structure_labels().get(str(emission.structure))
                    if ja:
                        materials["structure_ja"] = ja
                except Exception:  # noqa: BLE001
                    pass
            if emission.time_phase:
                materials["time_phase"] = emission.time_phase
        else:
            # ラベル補完だけ（上書きしない）
            if (
                emission.biome
                and "biome" not in materials
                and material_context_visible(materials, "biome")
            ):
                materials["biome"] = emission.biome
            if emission.structure and "structure" not in materials:
                materials["structure"] = emission.structure
            if (
                emission.time_phase
                and "time_phase" not in materials
                and material_context_visible(materials, "sky")
            ):
                materials["time_phase"] = emission.time_phase
            if emission.interpretation and "interpretation" not in materials:
                materials["interpretation"] = emission.interpretation
            try:
                from dogido_server.entry_catalog import biome_labels, structure_labels

                if materials.get("biome") and not materials.get("biome_ja"):
                    bid = str(materials["biome"]).removeprefix("minecraft:")
                    ja = biome_labels().get(bid) or biome_labels().get(str(materials["biome"]))
                    if ja:
                        materials["biome_ja"] = ja
                if materials.get("structure") and not materials.get("structure_ja"):
                    sid = str(materials["structure"]).removeprefix("minecraft:")
                    ja = structure_labels().get(sid) or structure_labels().get(str(materials["structure"]))
                    if ja:
                        materials["structure_ja"] = ja
            except Exception:  # noqa: BLE001
                pass
        session.haiku_workshop = open_from_emission(
            emission,
            materials=materials,
            entry_id=entry_id,
            now=now,
        )
        LOGGER.warning(
            "haiku_workshop_opened session_id=%s text=%s entry_id=%s "
            "speech_materials=%s debug_materials=%s materials_keys=%s",
            session.session_id,
            (emission.surface_text or emission.text or "")[:60],
            entry_id or "-",
            (materials_speech_line(session.haiku_workshop) or "")[:120] or "-",
            (materials_debug_line(session.haiku_workshop) or "")[:160] or "-",
            ",".join(sorted(str(k) for k in (session.haiku_workshop.materials or {})))
            if session.haiku_workshop
            else "-",
        )

    def _save_haiku_revision_reply(
        self,
        session: SessionInfo,
        event: GameEvent,
        revised_text: str,
        *,
        source: str,
        revision_line_sources: list[dict[str, object]] | None = None,
        revision_edits: list[dict[str, object]] | None = None,
        revision_edit_contract: str | None = None,
        revision_base_text: str | None = None,
        revision_base_surface_text: str | None = None,
        revision_lines: list[dict[str, object]] | None = None,
        parent_revision_id: str | None = None,
        keep_workshop_open: bool = False,
    ) -> list[AudioAction]:
        if session.last_haiku_emission is None:
            return [AudioAction(layer="speech", interrupt=False, text="直す元の句がまだないで。")]
        if self.memory is None:
            return [AudioAction(layer="speech", interrupt=False, text="記憶機能は今止まっとるで。")]
        if revision_lines is None:
            built_lines = build_haiku_lines(revised_text, provenance=source)
            if len(built_lines) == 3:
                revised_text = verse_reading_text(built_lines)
                revision_lines = [line.to_dict() for line in built_lines]
        try:
            revision = self.memory.save_haiku_feedback(
                session.last_haiku_emission,
                revised_text=revised_text,
                source=source,
                revision_line_sources=revision_line_sources,
                revision_edits=revision_edits,
                revision_edit_contract=revision_edit_contract,
                revision_base_text=revision_base_text,
                revision_base_surface_text=revision_base_surface_text,
                revision_lines=revision_lines,
                parent_revision_id=parent_revision_id,
                observed_at=event.observed_at,
            )
        except (OSError, ValueError) as exc:
            # 保存境界の不変条件違反をHTTP 500へ漏らさない。pendingは保持し、
            # プレイヤーが案を失わず再試行・確認できる状態にする。
            LOGGER.warning(
                "haiku_revision_save_failed session_id=%s source=%s detail=%s",
                session.session_id,
                source,
                exc,
            )
            return [
                AudioAction(
                    layer="speech",
                    interrupt=False,
                    text="案は残しとるけど、保存だけ失敗したわ。すまん。",
                )
            ]
        if keep_workshop_open and is_open(session.haiku_workshop):
            assert session.haiku_workshop is not None
            advance_workshop_revision(
                session.haiku_workshop,
                revision_id=str(revision.get("id") or "") or None,
            )
            record_workshop_activity(session.haiku_workshop, now=event.observed_at)
        elif is_open(session.haiku_workshop):
            close_workshop(session.haiku_workshop, reason="revise")
            session.haiku_workshop = None
        LOGGER.warning(
            "haiku_revision_saved session_id=%s source=%s text=%s",
            session.session_id,
            source,
            revised_text[:60],
        )
        reply = (
            "直した句、覚えたで。まだ直したい行があったら続けよか。"
            if keep_workshop_open
            else "元の句と直し、覚えといたで。"
        )
        return [AudioAction(layer="speech", interrupt=False, text=reply)]

    def _haiku_workshop_actions(
        self,
        session: SessionInfo,
        event: GameEvent,
    ) -> list[AudioAction]:
        workshop = session.haiku_workshop
        if not is_active(workshop) or workshop is None:
            return []
        player_input = session.machine.player_input
        text = (player_input.raw_text or "").strip()
        semantic_text = (player_input.semantic_text or text).strip()
        if not text or player_input.wants_quiet:
            return []
        if (player_input.normalized_text or "").startswith("/"):
            return []

        # 句中の未知語や「この川柳」ではなく、閉じた語彙で確定した一般知識
        # 質問だけを状態変更より先に処理する。戦闘後の再開確認待ちも含め、
        # pin・pending・lesson・close・driftを一切動かさない。
        knowledge_query = player_input.knowledge_query
        knowledge_subject = (
            "".join(knowledge_query.subject.split()) if knowledge_query is not None else ""
        )
        whole_verse_reference = bool(
            knowledge_subject
            and knowledge_subject.startswith(
                ("この", "今の", "いまの", "さっきの", "先ほどの", "今詠んだ", "いま詠んだ")
            )
            and any(term in knowledge_subject for term in ("句", "川柳", "俳句", "三行"))
        )
        knowledge_targets_current_verse = bool(
            knowledge_query is not None
            and (
                whole_verse_reference
                or mentioned_workshop_line_fragment(workshop, text) is not None
                or grounded_material_for_question(workshop, text) is not None
            )
        )
        if knowledge_targets_current_verse:
            session.machine.knowledge_query_handled = True
        if knowledge_query is not None and not knowledge_targets_current_verse:
            reply = session.machine._render_knowledge_reply()
            if not reply:
                return []
            return session.machine._knowledge_speech_actions(reply)

        actions = self._respond_to_workshop_input(session, event)
        replies = [action.text for action in actions if action.layer == "speech" and action.text]
        released_to_player_chat = any(
            action.route_owner == "player_chat" for action in actions
        )
        if replies and not released_to_player_chat:
            # 実際に選んだ返答だけを履歴へ。保留入力・知識質問・戦闘発話は混ぜない。
            recorded_input = text if text == semantic_text else f"{text}（聞き取りの解釈: {semantic_text}）"
            workshop.dialogue.add_player(recorded_input, at=event.observed_at)
            workshop.dialogue.add_dogido("\n".join(replies), at=event.observed_at)
        return actions

    def _respond_to_workshop_input(
        self,
        session: SessionInfo,
        event: GameEvent,
    ) -> list[AudioAction]:
        workshop = session.haiku_workshop
        assert workshop is not None
        workshop.hud_editing = False
        workshop.hud_selected_line = None
        player_input = session.machine.player_input
        text = (player_input.raw_text or "").strip()
        semantic_text = (player_input.semantic_text or text).strip()

        # 戦闘後は句を勝手に再開せず、一度だけプレイヤーへ戻すか確認する。
        # 新しい具体的な講評が来た場合は、その発話自体を再開の意思として
        # 確認状態だけ外し、同じターンを通常のworkshop処理へ流す。
        if workshop.awaiting_combat_resume_confirmation:
            resume_decision = combat_resume_confirmation_decision(text)
            if resume_decision == "resume":
                workshop.awaiting_combat_resume_confirmation = False
                record_workshop_activity(workshop, now=event.observed_at)
                LOGGER.warning(
                    "haiku_workshop_combat_resume_confirmed session_id=%s player=%s",
                    session.session_id,
                    text[:100],
                )
                return [
                    AudioAction(
                        layer="speech",
                        interrupt=False,
                        text="おけ、続けよか。気になるとこ教えてな。",
                    )
                ]
            if resume_decision == "close":
                close_workshop(workshop, reason="combat_resume_declined")
                session.haiku_workshop = None
                LOGGER.warning(
                    "haiku_workshop_combat_resume_declined session_id=%s player=%s",
                    session.session_id,
                    text[:100],
                )
                return [
                    AudioAction(
                        layer="speech",
                        interrupt=False,
                        text="おけ、句はここまでにしよか。",
                    )
                ]
            workshop.awaiting_combat_resume_confirmation = False

        # 未採用の局所案も、次の講評・置換では最新版として扱う。
        verse = workshop.editing_line()
        # 完成した三行の明示revisionは局所的な「〜に変えて」より優先する。
        conversational = extract_conversational_revise(text)
        replacement_parse = (
            parse_player_line_replacement(text)
            if conversational is None
            else parse_player_line_replacement(None)
        )
        player_line_replacement = replacement_parse.replacement
        if player_line_replacement is not None:
            # 「下五」はSTTで崩れやすい。現在句の一行を発話中に含めた
            # 「旧句より新句」の形なら、その旧句をコードで置換対象に固定する。
            target_fragment = mentioned_workshop_line_fragment(workshop, text)
            if target_fragment is not None:
                player_line_replacement = PlayerLineReplacement(
                    text=player_line_replacement.text,
                    explicit_line_index=player_line_replacement.explicit_line_index,
                    target_fragment=target_fragment,
                )
        if replacement_parse.status != "no_match":
            LOGGER.warning(
                "haiku_workshop_player_line_parse session_id=%s result=%s "
                "candidate=%s explicit_line=%s target_fragment=%s marked_line=%s player=%s",
                session.session_id,
                replacement_parse.status,
                (player_line_replacement.text[:40] if player_line_replacement else "-"),
                (
                    player_line_replacement.explicit_line_index
                    if player_line_replacement is not None
                    else None
                ),
                (
                    player_line_replacement.target_fragment
                    if player_line_replacement is not None
                    else None
                ),
                workshop.marked_line_index,
                text[:100],
            )
        speech_materials = materials_speech_line(workshop)
        debug_materials = materials_debug_line(workshop)

        # 明示保存・採否・終了と既存のfollow-up状態はコードの速い経路を維持する。
        # それ以外の自然な句相談は、現在句・pending・対話をまとめて読む
        # 有界な共同編集エージェントへ先に渡す。利用不可／棄権時だけ下の
        # 旧分類器と定型分岐へ戻る。
        agent_rule_kind = workshop_open_intent(
            text,
            verse=verse,
            player_input=player_input,
        )
        agent_fast_path = bool(
            conversational
            or wants_show_workshop_verse(text)
            or (workshop.pending_revision and pending_revision_decision(text) is not None)
            or (
                not workshop.pending_revision
                and agent_rule_kind in {"close", "praise"}
            )
            or (
                workshop.awaiting_meaning_ack
                and is_meaning_acknowledgement(text)
            )
            or (
                workshop.awaiting_close_confirmation
                and close_confirmation_decision(text) is not None
            )
        )
        if (
            self.settings.llm_enabled
            and self.settings.haiku_workshop_agent_enabled
            and agent_rule_kind is not None
            and not agent_fast_path
        ):
            agent_actions = self._try_workshop_agent_turn(
                session,
                event,
                player_text=semantic_text,
                raw_player_text=text,
            )
            if agent_actions is not None:
                return agent_actions

        # AI生成案は自動保存しない。自然文の意味は常駐する chat LLM で
        # 閉じた action に変換し、現在pendingとの整合と保存・破棄はコードで扱う。
        if workshop.pending_revision:
            pending_analysis, pending_path = self._analyze_pending_revision_reply(
                workshop,
                semantic_text,
            )
            semantic_decision = {
                "accept_pending": "accept",
                "reject_pending": "reject",
            }.get(pending_analysis.action)
            mutation_action = {
                "accept": "accept_pending",
                "reject": "reject_pending",
            }.get(semantic_decision or "")
            if mutation_action is not None and not workshop_mutation_evidence_is_valid(
                mutation_action,
                player_text=text,
                evidence=pending_analysis.evidence,
            ):
                LOGGER.warning(
                    "haiku_workshop_pending_decision result=rejected "
                    "reason=unsafe_original_evidence action=%s evidence=%s raw=%s",
                    mutation_action,
                    pending_analysis.evidence[:80],
                    text[:100],
                )
                semantic_decision = None
            semantic_close_requested = bool(
                pending_analysis.close_request is not None
                and workshop_mutation_evidence_is_valid(
                    "close_workshop",
                    player_text=text,
                    evidence=pending_analysis.close_request.evidence,
                )
            )
            # chat LLM が使えないときも、代表的な明示形だけは従来の
            # closed fullmatch で扱えるようにする。
            decision = semantic_decision or pending_revision_decision(text)
            LOGGER.warning(
                "haiku_workshop_pending_decision session_id=%s action=%s "
                "confidence=%.2f close=%s close_scope=%s path=%s fallback=%s player=%s",
                session.session_id,
                pending_analysis.action,
                pending_analysis.confidence,
                semantic_close_requested,
                (
                    pending_analysis.close_request.scope
                    if pending_analysis.close_request is not None
                    else "-"
                ),
                pending_path,
                "used" if semantic_decision is None and decision is not None else "-",
                text[:100],
            )
            if decision == "accept":
                # 提案後に pin や差分が食い違った場合は、別の句へ誤適用しない。
                if not pending_revision_is_current(workshop):
                    clear_pending_revision(workshop)
                    record_workshop_activity(workshop, now=event.observed_at)
                    LOGGER.warning(
                        "haiku_workshop_revision_rejected reason=stale_edit session_id=%s",
                        session.session_id,
                    )
                    return [AudioAction(layer="speech", interrupt=False, text="元の句と合わんくなったから、案はいったん戻すで。")]
                return self._save_haiku_revision_reply(
                    session,
                    event,
                    workshop.pending_revision,
                    source=workshop.pending_revision_source or "generated_confirmed",
                    revision_line_sources=list(workshop.pending_revision_line_sources),
                    revision_edits=list(workshop.pending_revision_edits),
                    revision_edit_contract=workshop.pending_revision_edit_contract,
                    revision_base_text=workshop.pending_revision_base_text,
                    revision_base_surface_text=workshop.display_surface(),
                    revision_lines=[
                        line.to_dict() for line in workshop.pending_revision_lines
                    ] or None,
                    parent_revision_id=workshop.current_revision_id,
                    keep_workshop_open=not semantic_close_requested,
                )
            if decision == "reject":
                clear_pending_revision(workshop)
                workshop.marked_line_index = None
                workshop.awaiting_meaning_ack = False
                workshop.awaiting_close_confirmation = False
                if semantic_close_requested:
                    close_workshop(workshop, reason="pending_rejected_close")
                    session.haiku_workshop = None
                    LOGGER.warning(
                        "haiku_workshop_closed session_id=%s "
                        "reason=pending_rejected_close evidence=%s",
                        session.session_id,
                        pending_analysis.close_request.evidence[:80]
                        if pending_analysis.close_request is not None
                        else "-",
                    )
                    return [
                        AudioAction(
                            layer="speech",
                            interrupt=False,
                            text="おけ、案は使わず、この句の話はここまでや。",
                        )
                    ]
                record_workshop_activity(workshop, now=event.observed_at)
                return [AudioAction(layer="speech", interrupt=False, text="おけ、元の句はそのままにしとくで。")]
            if semantic_close_requested:
                # pendingの採否をAIに補わせない。どちらを残すか明示された
                # ターンだけ、採用／却下とcloseを一つのコード操作として行う。
                record_workshop_activity(workshop, now=event.observed_at)
                return [
                    AudioAction(
                        layer="speech",
                        interrupt=False,
                        text=(
                            "いまの案を採用して終わるか、元の句のまま終わるか、"
                            "そこだけ教えてな。"
                        ),
                    )
                ]
            if pending_analysis.action == "show_pending":
                workshop.awaiting_meaning_ack = False
                workshop.awaiting_close_confirmation = False
                record_workshop_activity(workshop, now=event.observed_at)
                return [
                    AudioAction(
                        layer="speech",
                        interrupt=False,
                        text=f"いまの案はこれやで。\n{workshop.editing_line()}",
                    )
                ]

        if wants_show_workshop_verse(text):
            workshop.awaiting_meaning_ack = False
            workshop.awaiting_close_confirmation = False
            record_workshop_activity(workshop, now=event.observed_at)
            return [
                AudioAction(
                    layer="speech",
                    interrupt=False,
                    text=f"いまはこうやで。\n{workshop.editing_line()}",
                )
            ]

        # H4: 自然文の直し（workshop open 中）
        if conversational and self.memory is not None:
            LOGGER.warning(
                "haiku_workshop_turn session_id=%s path=revise source=conversational "
                "player=%s verse=%s revised=%s",
                session.session_id,
                text[:100],
                (verse or "")[:80],
                conversational[:80],
            )
            return self._save_haiku_revision_reply(
                session, event, conversational, source="conversational"
            )
        if conversational and self.memory is None:
            LOGGER.warning(
                "haiku_workshop_turn session_id=%s path=revise_no_memory player=%s",
                session.session_id,
                text[:100],
            )
            return [AudioAction(layer="speech", interrupt=False, text="記憶機能は今止まっとるで。")]

        # formal 川柳操作は上で処理済み。ここでは自然文の講評のみ。
        # Stage2: open 中は soft 既定（hard off-topic だけ None → chat/drift）
        meaning_ack_fallback = (
            workshop.awaiting_meaning_ack
            and is_meaning_acknowledgement(text)
        )
        close_confirmation_fallback = (
            close_confirmation_decision(text)
            if workshop.awaiting_close_confirmation
            else None
        )
        # 「わかった」は通常なら明示closeにもなり得るが、意味説明の直後は
        # まず「説明を理解した」と解釈し、終了確認を一段はさむ。
        kind = (
            "ack"
            if meaning_ack_fallback
            else workshop_open_intent(
                text,
                verse=verse,
                player_input=player_input,
            )
        )
        if kind is None:
            # 明確な別件へ移ったら、古い「説明への返事待ち」を次の発話へ残さない。
            workshop.awaiting_meaning_ack = False
            workshop.awaiting_close_confirmation = False
            LOGGER.warning(
                "haiku_workshop_miss session_id=%s player=%s verse=%s "
                "speech_materials=%s debug_materials=%s drift_count=%s "
                "(intent=None hard_off_topic → chat/drift 候補)",
                session.session_id,
                text[:100],
                (verse or "")[:80],
                (speech_materials or "")[:80] or "-",
                (debug_materials or "")[:120] or "-",
                workshop.drift_count,
            )
            return []

        intent_path = "rule"
        analysis = WorkshopAnalysis(intent=kind, confidence=1.0)
        effective_kind = kind
        semantic_close_accepted = False
        semantic_evaluation_positive = False
        semantic_evaluation_needs_revision = False
        semantic_evaluation_unresolved = False
        analysis_kinds = {
            "soft_default",
            "other_haiku",
            "ask_meaning",
            "ack",
            "request_repair",
            "critique_forced",
            "critique_gibberish",
            "critique_offscene",
        }
        if kind in analysis_kinds:
            analysis, intent_path = self._analyze_workshop_feedback(workshop, semantic_text)
            # clear_lessons / 固定規則のpraiseのような操作はコードを正にする。
            # 規則外の句評価は会話モデルから根拠つきで受けるが、positiveでも即closeせず
            # コード所有の終了確認へ進める。
            semantic_intents = {
                "ask_meaning",
                "critique_forced",
                "critique_gibberish",
                "critique_offscene",
                "ack",
                "other_haiku",
                "request_repair",
                "show_current",
                "propose_line_edit",
            }
            if (
                not meaning_ack_fallback
                and kind in {"soft_default", "other_haiku", "ask_meaning", "ack"}
                and analysis.intent in semantic_intents
                and analysis.confidence >= 0.75
            ):
                effective_kind = analysis.intent
            if effective_kind == "request_repair" and not analysis.repair_requested:
                effective_kind = "other_haiku"
            if analysis.close_request is not None:
                close_evidence_valid = workshop_mutation_evidence_is_valid(
                    "close_workshop",
                    player_text=text,
                    evidence=analysis.close_request.evidence,
                )
                if not close_evidence_valid:
                    LOGGER.warning(
                        "haiku_workshop_close_request session_id=%s result=rejected "
                        "reason=unsafe_original_evidence path=%s evidence=%s raw=%s",
                        session.session_id,
                        intent_path,
                        analysis.close_request.evidence[:80],
                        text[:100],
                    )
                elif analysis.line_proposal is None and not analysis.repair_requested:
                    semantic_close_accepted = True
                    effective_kind = "close"
                    LOGGER.warning(
                        "haiku_workshop_close_request session_id=%s result=accepted "
                        "scope=%s confidence=%.2f path=%s evidence=%s",
                        session.session_id,
                        analysis.close_request.scope,
                        analysis.close_request.confidence,
                        intent_path,
                        analysis.close_request.evidence[:80],
                    )
                else:
                    LOGGER.warning(
                        "haiku_workshop_close_request session_id=%s result=rejected "
                        "reason=edit_conflict path=%s evidence=%s",
                        session.session_id,
                        intent_path,
                        analysis.close_request.evidence[:80],
                    )
            # 相槌という分類と、根拠つきの否定評価・具体的な修正相談が
            # 同時に返ったら、納得として処理しない。明示の短い相槌fallbackは維持。
            if (
                effective_kind == "ack"
                and not meaning_ack_fallback
                and analysis.confidence >= 0.75
                and (
                    analysis.findings
                    or analysis.line_proposal is not None
                    or analysis.repair_requested
                    or (
                        analysis.evaluation is not None
                        and analysis.evaluation.sentiment in {"negative", "mixed"}
                    )
                )
            ):
                effective_kind = "request_repair" if analysis.repair_requested else "other_haiku"
            followup_control_turn = (
                workshop.awaiting_meaning_ack and effective_kind == "ack"
            ) or (
                workshop.awaiting_close_confirmation
                and (
                    effective_kind == "ack"
                    or close_confirmation_fallback in {"accept", "continue"}
                )
            )
            evaluation = analysis.evaluation
            replacement_targeted = bool(
                player_line_replacement is not None
                and (
                    player_line_replacement.explicit_line_index is not None
                    or player_line_replacement.target_fragment
                    or workshop.marked_line_index is not None
                )
            )
            semantic_proposal_targeted = bool(
                analysis.line_proposal is not None
                and analysis.line_proposal.line_index is not None
            )
            explicit_replacement_command = (
                is_explicit_player_line_replacement_command(text)
            )
            if (
                evaluation is None
                and player_line_replacement is not None
                and not replacement_targeted
                and not semantic_proposal_targeted
                and not explicit_replacement_command
                and not followup_control_turn
                and not semantic_close_accepted
                and not analysis.repair_requested
            ):
                # 一次の複合schemaが意味判定を確定できなければ、同じchat routeの
                # 評価専用schemaへ進める。provider切替ではなく意味上の二段目。
                LOGGER.warning(
                    "haiku_workshop_evaluation session_id=%s stage=primary "
                    "result=rejected reason=no_accepted_evaluation path=%s candidate=%s",
                    session.session_id,
                    intent_path,
                    player_line_replacement.text[:48],
                )
                evaluation, evaluation_reason = self._analyze_workshop_evaluation_with_chat(
                    workshop,
                    semantic_text,
                )
                if evaluation is not None:
                    intent_path = f"{intent_path}->chat_evaluation"
                else:
                    semantic_evaluation_unresolved = True
                    LOGGER.warning(
                        "haiku_workshop_evaluation session_id=%s stage=chat_second_pass "
                        "result=rejected reason=%s candidate=%s",
                        session.session_id,
                        evaluation_reason,
                        player_line_replacement.text[:48],
                    )
            if (
                evaluation is not None
                and not followup_control_turn
                and not semantic_close_accepted
                and not analysis.repair_requested
            ):
                # 固定置換規則は「〜でいい」を一行案と誤読し得る。対象行が
                # コードで確定していない候補より、根拠つきの句評価を優先する。
                if not replacement_targeted and not semantic_proposal_targeted:
                    player_line_replacement = None
                    if (
                        evaluation.sentiment == "positive"
                        and evaluation.scope == "whole_verse"
                    ):
                        effective_kind = "praise"
                        semantic_evaluation_positive = True
                    elif evaluation.sentiment in {"negative", "mixed"}:
                        semantic_evaluation_needs_revision = True
                        if analysis.intent in {
                            "critique_forced",
                            "critique_gibberish",
                            "critique_offscene",
                        }:
                            effective_kind = analysis.intent
                        else:
                            effective_kind = "other_haiku"
                    LOGGER.warning(
                        "haiku_workshop_evaluation session_id=%s result=accepted "
                        "sentiment=%s scope=%s confidence=%.2f path=%s evidence=%s",
                        session.session_id,
                        evaluation.sentiment,
                        evaluation.scope,
                        evaluation.confidence,
                        intent_path,
                        evaluation.evidence[:80],
                    )
            if analysis.line_reference is not None and not followup_control_turn:
                LOGGER.warning(
                    "haiku_workshop_line_reference session_id=%s concept=%s number=%s "
                    "line_index=%s canonical=%s evidence=%s confidence=%.2f path=%s",
                    session.session_id,
                    analysis.line_reference.concept_id,
                    analysis.line_reference.concept_number,
                    analysis.line_reference.line_index,
                    analysis.line_reference.canonical_name,
                    analysis.line_reference.evidence[:48],
                    analysis.line_reference.confidence,
                    intent_path,
                )
            if (
                analysis.line_proposal is not None
                and not followup_control_turn
                and not semantic_evaluation_positive
                and not semantic_evaluation_needs_revision
                and not semantic_evaluation_unresolved
            ):
                if not workshop_player_edit_is_grounded_in_original(analysis, text):
                    LOGGER.warning(
                        "haiku_workshop_line_proposal session_id=%s result=rejected "
                        "reason=not_in_original path=%s evidence=%s raw=%s",
                        session.session_id,
                        intent_path,
                        analysis.line_proposal.evidence[:80],
                        text[:100],
                    )
                    analysis_line_proposal_grounded = False
                else:
                    analysis_line_proposal_grounded = True
                # 自然な置換提案は会話モデルの意味抽出を優先する。上で得た
                # closed regex の候補は、会話モデルが提案を確定できない場合だけ
                # fallback として残る。ただし現在句そのものが発話に含まれて
                # コードで一意に取れた置換元は、AIの空欄で上書きしない。
                code_target_fragment = (
                    player_line_replacement.target_fragment
                    if player_line_replacement is not None
                    else None
                )
                code_explicit_line = (
                    player_line_replacement.explicit_line_index
                    if player_line_replacement is not None
                    else None
                )
                semantic_line = (
                    analysis.line_reference.line_index
                    if analysis.line_reference is not None
                    else None
                )
                target_indices = {
                    index
                    for index in (
                        analysis.line_proposal.line_index,
                        semantic_line,
                        code_explicit_line,
                    )
                    if index is not None
                }
                if analysis_line_proposal_grounded and len(target_indices) <= 1:
                    player_line_replacement = PlayerLineReplacement(
                        text=analysis.line_proposal.replacement_text,
                        explicit_line_index=(
                            next(iter(target_indices)) if target_indices else None
                        ),
                        target_fragment=(
                            code_target_fragment
                            or analysis.line_proposal.target_fragment
                            or None
                        ),
                    )
                    effective_kind = "propose_line_edit"
                    LOGGER.warning(
                        "haiku_workshop_line_proposal session_id=%s result=accepted "
                        "target_line=%s target_fragment=%s replacement=%s confidence=%.2f "
                        "path=%s evidence=%s",
                        session.session_id,
                        player_line_replacement.explicit_line_index,
                        analysis.line_proposal.target_fragment[:40],
                        analysis.line_proposal.replacement_text[:40],
                        analysis.line_proposal.confidence,
                        intent_path,
                        analysis.line_proposal.evidence[:80],
                    )
                elif analysis_line_proposal_grounded:
                    LOGGER.warning(
                        "haiku_workshop_line_proposal session_id=%s result=rejected "
                        "reason=line_reference_conflict targets=%s evidence=%s",
                        session.session_id,
                        sorted(target_indices),
                        analysis.line_proposal.evidence[:80],
                    )
            if analysis.findings and not followup_control_turn:
                workshop.last_findings = [finding.to_dict() for finding in analysis.findings]
            marked_line = workshop.marked_line_index
            if not followup_control_turn:
                marked_line = update_marked_workshop_line(
                    workshop,
                    findings=analysis.findings,
                    player_text=(
                        text
                        if player_line_replacement is not None
                        or effective_kind
                        in {
                            "request_repair",
                            "critique_forced",
                            "critique_gibberish",
                            "critique_offscene",
                        }
                        else None
                    ),
                    line_reference=analysis.line_reference,
                )
            if analysis.findings and not followup_control_turn:
                LOGGER.warning(
                    "haiku_workshop_locate session_id=%s result=%s marked_line=%s findings=%s",
                    session.session_id,
                    "accepted" if marked_line is not None else "ambiguous",
                    marked_line,
                    [finding.to_dict() for finding in analysis.findings],
                )

        # 意味説明への納得は講評ではない。別の句断片を拾わせず、コード固定の
        # 終了確認へ進める。会話モデルが使えない場合も代表的な短文だけfallbackする。
        if workshop.awaiting_meaning_ack:
            if effective_kind == "ack":
                workshop.awaiting_meaning_ack = False
                workshop.awaiting_close_confirmation = True
                workshop.close_confirmation_source = "meaning"
                record_workshop_activity(workshop, now=event.observed_at)
                LOGGER.warning(
                    "haiku_workshop_followup session_id=%s stage=meaning_explained "
                    "result=close_confirmation intent_path=%s player=%s",
                    session.session_id,
                    intent_path,
                    text[:100],
                )
                return [
                    AudioAction(
                        layer="speech",
                        interrupt=False,
                        text="うん、伝わってよかった。この句の話はここまででええ？",
                    )
                ]
            # 新しい質問・講評が来たなら、前の説明待ちは終えて通常処理を続ける。
            workshop.awaiting_meaning_ack = False

        if workshop.awaiting_close_confirmation:
            if close_confirmation_fallback == "continue":
                workshop.awaiting_close_confirmation = False
                workshop.close_confirmation_source = None
                record_workshop_activity(workshop, now=event.observed_at)
                LOGGER.warning(
                    "haiku_workshop_followup session_id=%s stage=close_confirmation "
                    "result=continue player=%s",
                    session.session_id,
                    text[:100],
                )
                return [
                    AudioAction(
                        layer="speech",
                        interrupt=False,
                        text="おけ、まだ続けよか。気になるとこ教えてな。",
                    )
                ]
            if (
                close_confirmation_fallback == "accept"
                or effective_kind == "ack"
                or semantic_evaluation_positive
            ):
                confirmation_source = workshop.close_confirmation_source
                close_workshop(
                    workshop,
                    reason=(
                        "evaluation_confirmed"
                        if confirmation_source == "evaluation"
                        else "meaning_confirmed"
                    ),
                )
                session.haiku_workshop = None
                LOGGER.warning(
                    "haiku_workshop_followup session_id=%s stage=close_confirmation "
                    "result=closed intent_path=%s player=%s",
                    session.session_id,
                    intent_path,
                    text[:100],
                )
                return [
                    AudioAction(
                        layer="speech",
                        interrupt=False,
                        text="おけ、この句の話はここまでや。",
                    )
                ]
            # 終了への返事ではなく新しい句の話なら、確認状態だけ解除して続ける。
            workshop.awaiting_close_confirmation = False
            workshop.close_confirmation_source = None

        if semantic_evaluation_unresolved:
            # 評価か置換かを会話モデルでも確定できなかった。対象のない語を
            # 一行へ勝手に入れず、次の発話で意図を一つにしてもらう。
            record_workshop_activity(workshop, now=event.observed_at)
            return [
                AudioAction(
                    layer="speech",
                    interrupt=False,
                    text=(
                        "これは句全体の感想やろか？ "
                        "それとも、どこか一行をその言葉に変えたいん？"
                    ),
                )
            ]

        # 正規表現で曖昧でも、会話モデルが発話中の一意な置換語と句中断片を取れた
        # 場合は先へ進める。どちらでも確定しなければコード固定で聞き返す。
        if (
            replacement_parse.status == "ambiguous"
            and player_line_replacement is None
            and not semantic_evaluation_positive
            and not semantic_evaluation_needs_revision
        ):
            record_workshop_activity(workshop, now=event.observed_at)
            return [
                AudioAction(
                    layer="speech",
                    interrupt=False,
                    text=(
                        "置き換えるところが一つに決められへんかったわ。"
                        "『くさちのねよりくさちかな』みたいに、元の一行と新しい一行を教えてな。"
                    ),
                )
            ]

        if effective_kind == "show_current":
            record_workshop_activity(workshop, now=event.observed_at)
            return [
                AudioAction(
                    layer="speech",
                    interrupt=False,
                    text=f"いまはこうやで。\n{workshop.editing_line()}",
                )
            ]

        kind = effective_kind
        reply_kind = kind

        now = event.observed_at
        if kind == "close":
            close_workshop(
                workshop,
                reason="semantic_explicit" if semantic_close_accepted else "explicit",
            )
            session.haiku_workshop = None
            reply = render_workshop_reply("close", workshop, player_text=text)
            LOGGER.warning(
                "haiku_workshop_turn session_id=%s path=close intent_path=%s "
                "semantic=%s player=%s reply=%s",
                session.session_id,
                intent_path,
                semantic_close_accepted,
                text[:100],
                (reply or "")[:120],
            )
            return [AudioAction(layer="speech", interrupt=False, text=reply)]

        record_workshop_activity(workshop, now=now)
        critique_kind = {
            "praise": "praise",
            "critique_forced": "forced_compress",
            "critique_gibberish": "unreadable",
            "critique_offscene": "off_context",
            "ask_meaning": "ask_meaning",
            "ack": "other",
            "other_haiku": "other",
            "soft_default": "other",
        }.get(kind, "other")

        critique_id: str | None = None
        if kind not in {"close", "ack"} and self.memory is not None:
            try:
                row = self.memory.save_haiku_critique(
                    entry_id=workshop.entry_id,
                    kind=critique_kind,
                    player_text=text,
                    surface_at_time=workshop.editing_line(),
                    materials_snapshot=dict(workshop.materials or {}),
                    observed_at=now,
                    session_id=session.session_id,
                )
                critique_id = str(row.get("id") or "") or None
                # praise は critique 保存のみ（lesson は触らない＝過去の指摘をキープ）。
                # 全軸 loosen は明示「気にせんで」経路だけ（clear_lessons）。
                if critique_kind != "praise" and kind != "request_repair":
                    for lesson in lessons_from_critique_kind(critique_kind, player_text=text):
                        self.memory.save_haiku_lesson(
                            lesson_type=str(lesson.get("lesson_type") or "other"),
                            note=str(lesson.get("note") or ""),
                            prefer_materials=bool(lesson.get("prefer_materials")),
                            forbidden_fragments=list(lesson.get("forbidden_fragments") or []),
                            from_entry_id=workshop.entry_id,
                            from_critique_id=critique_id,
                            observed_at=now,
                            polarity=str(lesson.get("polarity") or "tighten"),
                            strength=float(lesson.get("strength") or 0.3),
                        )
            except OSError as exc:
                LOGGER.warning("haiku_critique_save_failed detail=%s", exc)

        if player_line_replacement is not None:
            result = build_player_line_revision(workshop, player_line_replacement)
            target_line = result.target_line_index
            self._mark_workshop_hud_edit(workshop, target_line)
            if result.text is None:
                LOGGER.warning(
                    "haiku_workshop_player_line_edit session_id=%s result=rejected "
                    "target_line=%s candidate=%s reasons=%s base=%s",
                    session.session_id,
                    target_line,
                    player_line_replacement.text[:40],
                    list(result.failure_reasons),
                    result.base_text[:80],
                )
                return [
                    AudioAction(
                        layer="speech",
                        interrupt=False,
                        text=self._player_line_revision_failure_reply(result.failure_reasons),
                    )
                ]
            workshop.pending_revision = result.text
            workshop.pending_revision_surface_text = result.surface_text
            workshop.pending_revision_lines = result.lines
            workshop.pending_revision_line_sources.clear()
            workshop.pending_revision_base_text = result.base_text
            workshop.pending_revision_edits = [dict(edit) for edit in result.edits]
            workshop.pending_revision_edit_contract = PLAYER_LINE_EDIT_CONTRACT_VERSION
            workshop.pending_revision_source = "player_line_confirmed"
            workshop.marked_line_index = None
            workshop.last_findings.clear()
            LOGGER.warning(
                "haiku_workshop_player_line_edit session_id=%s result=staged "
                "target_line=%s candidate=%s base=%s revised=%s edits=%s",
                session.session_id,
                target_line,
                player_line_replacement.text[:40],
                result.base_text[:80],
                result.text[:80],
                len(result.edits),
            )
            return [
                AudioAction(
                    layer="speech",
                    interrupt=False,
                    # 修正句の直後へ案内を連結するとTTSで境界が潰れる。
                    # pending状態は維持し、音声は変更後の三行だけで止める。
                    text=result.text,
                )
            ]

        if kind == "praise":
            if semantic_evaluation_positive:
                workshop.awaiting_close_confirmation = True
                workshop.close_confirmation_source = "evaluation"
                record_workshop_activity(workshop, now=event.observed_at)
                LOGGER.warning(
                    "haiku_workshop_followup session_id=%s stage=evaluation_positive "
                    "result=close_confirmation intent_path=%s player=%s",
                    session.session_id,
                    intent_path,
                    text[:100],
                )
                return [
                    AudioAction(
                        layer="speech",
                        interrupt=False,
                        text="気にいってもらえてよかった。この句の話はここまででええ？",
                    )
                ]
            close_workshop(workshop, reason="praise")
            session.haiku_workshop = None

        if kind == "request_repair":
            repair_analysis = analysis
            if not repair_analysis.findings:
                repair_analysis = WorkshopAnalysis(
                    intent=kind,
                    confidence=analysis.confidence,
                    repair_requested=True,
                    findings=workshop_findings_from_records(workshop.last_findings),
                )
            reply, repair_path = self._workshop_revision_reply(
                workshop,
                repair_analysis,
                semantic_text,
            )
            LOGGER.warning(
                "haiku_workshop_repair session_id=%s path=%s targets=%s accepted=%s",
                session.session_id,
                repair_path,
                repair_target_indices(repair_analysis.findings),
                bool(workshop.pending_revision),
            )
            return [AudioAction(layer="speech", interrupt=False, text=reply)]

        reply_path = "template"
        if reply_kind == "ask_meaning":
            reply, reply_path = self._ask_meaning_workshop_reply(workshop, semantic_text)
            workshop.awaiting_meaning_ack = True
            workshop.awaiting_close_confirmation = False
        elif reply_kind in {
            "soft_default",
            "other_haiku",
            "request_repair",
            "critique_forced",
            "critique_gibberish",
            "critique_offscene",
            "ack",
            "praise",
        }:
            # 会話は共同編集者 leaf。状態変更・保存はすでにコード側で確定済み。
            # 失敗時だけ短い定型へ戻す。
            reply, reply_path = self._collaborator_workshop_reply(
                workshop,
                semantic_text,
                kind=reply_kind,
                analysis=analysis,
                reply_goal=(
                    "ask_revision_direction"
                    if semantic_evaluation_needs_revision
                    else "respond_to_feedback"
                ),
            )
        else:
            reply = render_workshop_reply(reply_kind, workshop, player_text=text)
        # 観察用: intent / 句 / 口頭材料 vs 内部 materials / 返事
        LOGGER.warning(
            "haiku_workshop_turn session_id=%s path=reply kind=%s intent_path=%s critique_kind=%s "
            "reply_path=%s player=%s verse=%s speech_materials=%s debug_materials=%s "
            "materials_keys=%s interpreted=%s reply=%s critique_id=%s",
            session.session_id,
            kind,
            intent_path,
            critique_kind,
            reply_path,
            text[:100],
            (verse or "")[:80],
            (speech_materials or "")[:80] or "-",
            (debug_materials or "")[:120] or "-",
            ",".join(sorted(str(k) for k in (workshop.materials or {}))) or "-",
            semantic_text[:100] if semantic_text != text else "-",
            (reply or "")[:160],
            critique_id or "-",
        )
        return [AudioAction(layer="speech", interrupt=False, text=reply)]

    def _try_workshop_agent_turn(
        self,
        session: SessionInfo,
        event: GameEvent,
        *,
        player_text: str,
        raw_player_text: str,
    ) -> list[AudioAction] | None:
        """自然な相談を、最大三手の検証付き共同編集ループで処理する。"""

        workshop = session.haiku_workshop
        assert workshop is not None
        base_verse = workshop.display_line()
        pending_before = workshop.pending_revision
        turn_steps: list[dict[str, object]] = []
        phase = "decide"
        observation: dict[str, object] | None = None
        feedback_step: WorkshopAgentStep | None = None

        # 最大は decide -> after_inspection -> after_validation。inspect と editor は
        # 各一回だけで、同じターンに修正生成を繰り返さない。
        for _round in range(3):
            step, path = self._plan_workshop_agent_step(
                workshop,
                player_text,
                raw_player_text=raw_player_text,
                phase=phase,
                observation=observation,
                turn_steps=turn_steps,
            )
            if step is None:
                if not turn_steps:
                    LOGGER.warning(
                        "haiku_workshop_agent result=legacy_fallback reason=%s player=%s",
                        path,
                        player_text[:100],
                    )
                    return None
                failure_code = f"agent_step_{path}"[:80]
                previous_codes = turn_steps[-1].setdefault("validation_codes", [])
                if isinstance(previous_codes, list) and failure_code not in previous_codes:
                    previous_codes.append(failure_code)
                reply = self._workshop_agent_observation_fallback(workshop, observation)
                record_workshop_activity(workshop, now=event.observed_at)
                self._finish_workshop_agent_turn(
                    session,
                    event,
                    workshop=workshop,
                    player_text=player_text,
                    raw_player_text=raw_player_text,
                    base_verse=base_verse,
                    pending_before=pending_before,
                    turn_steps=turn_steps,
                    feedback_step=feedback_step,
                )
                LOGGER.warning(
                    "haiku_workshop_agent result=code_fallback reason=%s phase=%s player=%s",
                    path,
                    phase,
                    player_text[:100],
                )
                return [AudioAction(layer="speech", interrupt=False, text=reply)]

            if feedback_step is None and step.action not in {
                "inspect",
                "show_current",
                "accept_pending",
                "reject_pending",
                "close_workshop",
                "unrelated",
            }:
                feedback_step = step

            if step.action == "inspect":
                observation = inspect_workshop(workshop, step.checks)
                row = record_workshop_agent_step(
                    workshop,
                    step,
                    phase=phase,
                    outcome="inspection_completed",
                    validation_codes=observation.get("validation_codes", []),
                )
                turn_steps.append(row)
                phase = "after_inspection"
                continue

            if step.action == "propose_revision":
                repair_analysis = step.analysis
                if not repair_analysis.findings:
                    repair_analysis = WorkshopAnalysis(
                        intent="request_repair",
                        confidence=step.confidence,
                        repair_requested=True,
                        findings=workshop_findings_from_records(workshop.last_findings),
                    )
                observation = self._stage_workshop_revision(
                    workshop,
                    repair_analysis,
                )
                row = record_workshop_agent_step(
                    workshop,
                    step,
                    phase=phase,
                    outcome=str(observation.get("status") or "rejected"),
                    validation_codes=observation.get("validation_codes", []),
                )
                turn_steps.append(row)
                feedback_step = step
                phase = "after_validation"
                continue

            if step.action == "stage_player_edit":
                proposal = step.analysis.line_proposal
                assert proposal is not None
                replacement = PlayerLineReplacement(
                    text=proposal.replacement_text,
                    explicit_line_index=proposal.line_index,
                    target_fragment=proposal.target_fragment or None,
                )
                result = build_player_line_revision(workshop, replacement)
                self._mark_workshop_hud_edit(workshop, result.target_line_index)
                if result.text is None:
                    row = record_workshop_agent_step(
                        workshop,
                        step,
                        phase=phase,
                        outcome="player_edit_rejected",
                        validation_codes=result.failure_reasons,
                    )
                    turn_steps.append(row)
                    reply = self._player_line_revision_failure_reply(result.failure_reasons)
                else:
                    workshop.pending_revision = result.text
                    workshop.pending_revision_surface_text = result.surface_text
                    workshop.pending_revision_lines = result.lines
                    workshop.pending_revision_line_sources.clear()
                    workshop.pending_revision_base_text = result.base_text
                    workshop.pending_revision_edits = [dict(edit) for edit in result.edits]
                    workshop.pending_revision_edit_contract = PLAYER_LINE_EDIT_CONTRACT_VERSION
                    workshop.pending_revision_source = "player_line_confirmed"
                    workshop.marked_line_index = None
                    workshop.last_findings.clear()
                    row = record_workshop_agent_step(
                        workshop,
                        step,
                        phase=phase,
                        outcome="player_edit_staged",
                    )
                    turn_steps.append(row)
                    # AudioAction.text は表示とTTSを兼ねるため、表記候補ではなく
                    # コードで確定したひらがな読みを返す（旧局所編集経路と同じ）。
                    reply = result.text
                record_workshop_activity(workshop, now=event.observed_at)
                self._finish_workshop_agent_turn(
                    session,
                    event,
                    workshop=workshop,
                    player_text=player_text,
                    raw_player_text=raw_player_text,
                    base_verse=base_verse,
                    pending_before=pending_before,
                    turn_steps=turn_steps,
                    feedback_step=step,
                )
                return [AudioAction(layer="speech", interrupt=False, text=reply)]

            if step.action == "accept_pending":
                if not pending_revision_is_current(workshop):
                    clear_pending_revision(workshop)
                    reply = "元の句と合わんくなったから、案はいったん戻すで。"
                    outcome = "stale_pending_rejected"
                    codes = ("stale_edit",)
                else:
                    close_after_action = step.close_after_action
                    reply_actions = self._save_haiku_revision_reply(
                        session,
                        event,
                        workshop.pending_revision or "",
                        source=workshop.pending_revision_source or "generated_confirmed",
                        revision_line_sources=list(workshop.pending_revision_line_sources),
                        revision_edits=list(workshop.pending_revision_edits),
                        revision_edit_contract=workshop.pending_revision_edit_contract,
                        revision_base_text=workshop.pending_revision_base_text,
                        revision_base_surface_text=workshop.display_surface(),
                        revision_lines=[line.to_dict() for line in workshop.pending_revision_lines]
                        or None,
                        parent_revision_id=workshop.current_revision_id,
                        keep_workshop_open=not close_after_action,
                    )
                    reply = reply_actions[0].text or ""
                    saved = (
                        session.haiku_workshop is None
                        if close_after_action
                        else workshop.pending_revision is None
                    )
                    if saved and close_after_action:
                        clear_pending_revision(workshop)
                        reply = "元の句と直し、覚えといたで。この句の話はここまでや。"
                    outcome = (
                        "pending_saved_and_closed"
                        if saved and close_after_action
                        else "pending_saved"
                        if saved
                        else "pending_save_failed"
                    )
                    codes = () if saved else ("save_failed",)
                row = record_workshop_agent_step(
                    workshop,
                    step,
                    phase=phase,
                    outcome=outcome,
                    validation_codes=codes,
                )
                turn_steps.append(row)
                record_workshop_activity(workshop, now=event.observed_at)
                self._finish_workshop_agent_turn(
                    session,
                    event,
                    workshop=workshop,
                    player_text=player_text,
                    raw_player_text=raw_player_text,
                    base_verse=base_verse,
                    pending_before=pending_before,
                    turn_steps=turn_steps,
                )
                return [AudioAction(layer="speech", interrupt=False, text=reply)]

            if step.action == "reject_pending":
                clear_pending_revision(workshop)
                workshop.marked_line_index = None
                workshop.awaiting_meaning_ack = False
                workshop.awaiting_close_confirmation = False
                if step.close_after_action:
                    close_workshop(workshop, reason="agent_pending_rejected_close")
                    session.haiku_workshop = None
                row = record_workshop_agent_step(
                    workshop,
                    step,
                    phase=phase,
                    outcome=(
                        "pending_rejected_and_closed"
                        if step.close_after_action
                        else "pending_rejected"
                    ),
                )
                turn_steps.append(row)
                record_workshop_activity(workshop, now=event.observed_at)
                self._finish_workshop_agent_turn(
                    session,
                    event,
                    workshop=workshop,
                    player_text=player_text,
                    raw_player_text=raw_player_text,
                    base_verse=base_verse,
                    pending_before=pending_before,
                    turn_steps=turn_steps,
                )
                reply = (
                    "おけ、案は使わず、この句の話はここまでや。"
                    if step.close_after_action
                    else "おけ、元の句はそのままにしとくで。"
                )
                return [AudioAction(layer="speech", interrupt=False, text=reply)]

            if step.action == "close_workshop":
                row = record_workshop_agent_step(
                    workshop,
                    step,
                    phase=phase,
                    outcome="workshop_closed",
                )
                turn_steps.append(row)
                self._finish_workshop_agent_turn(
                    session,
                    event,
                    workshop=workshop,
                    player_text=player_text,
                    raw_player_text=raw_player_text,
                    base_verse=base_verse,
                    pending_before=pending_before,
                    turn_steps=turn_steps,
                )
                close_workshop(workshop, reason="agent_explicit")
                session.haiku_workshop = None
                return [AudioAction(layer="speech", interrupt=False, text="おけ、この句の話はここまでや。")]

            if step.action == "show_current":
                row = record_workshop_agent_step(
                    workshop,
                    step,
                    phase=phase,
                    outcome="current_shown",
                )
                turn_steps.append(row)
                workshop.awaiting_meaning_ack = False
                workshop.awaiting_close_confirmation = False
                record_workshop_activity(workshop, now=event.observed_at)
                self._finish_workshop_agent_turn(
                    session,
                    event,
                    workshop=workshop,
                    player_text=player_text,
                    raw_player_text=raw_player_text,
                    base_verse=base_verse,
                    pending_before=pending_before,
                    turn_steps=turn_steps,
                    feedback_step=feedback_step,
                )
                if (
                    isinstance(observation, dict)
                    and observation.get("kind") == "revision_validation"
                    and observation.get("status") == "proposed"
                    and workshop.pending_revision
                ):
                    reply = (
                        "検査に通った未採用の案はこれや。\n"
                        f"{workshop.editing_line()}\n"
                        "よければ『その案で』って言ってな。"
                    )
                elif (
                    isinstance(observation, dict)
                    and observation.get("kind") == "revision_validation"
                    and observation.get("status") == "rejected"
                ):
                    reply = (
                        "案は検査に通らんかったから、元の句はそのままや。\n"
                        f"{workshop.display_line()}"
                    )
                else:
                    reply = f"いまはこうやで。\n{workshop.editing_line()}"
                return [
                    AudioAction(
                        layer="speech",
                        interrupt=False,
                        text=reply,
                    )
                ]

            if step.action == "unrelated":
                reply = session.machine._render_player_chat_reply(event)
                if not reply:
                    row = record_workshop_agent_step(
                        workshop,
                        step,
                        phase=phase,
                        outcome="main_chat_unavailable",
                        validation_codes=("no_player_chat_reply",),
                    )
                    turn_steps.append(row)
                    self._finish_workshop_agent_turn(
                        session,
                        event,
                        workshop=workshop,
                        player_text=player_text,
                        raw_player_text=raw_player_text,
                        base_verse=base_verse,
                        pending_before=pending_before,
                        turn_steps=turn_steps,
                    )
                    return []
                updated = record_drift(workshop, now=event.observed_at)
                closed = updated is not None and not updated.open
                row = record_workshop_agent_step(
                    workshop,
                    step,
                    phase=phase,
                    outcome=(
                        "released_to_main_chat_closed"
                        if closed
                        else "released_to_main_chat"
                    ),
                )
                turn_steps.append(row)
                workshop.awaiting_meaning_ack = False
                workshop.awaiting_close_confirmation = False
                self._finish_workshop_agent_turn(
                    session,
                    event,
                    workshop=workshop,
                    player_text=player_text,
                    raw_player_text=raw_player_text,
                    base_verse=base_verse,
                    pending_before=pending_before,
                    turn_steps=turn_steps,
                )
                if closed:
                    session.haiku_workshop = None
                return [
                    AudioAction(
                        layer="speech",
                        interrupt=False,
                        text=reply,
                        route_owner="player_chat",
                    )
                ]

            if step.action in {"respond", "explain", "ask", "compare"}:
                if step.purpose == "improve_wording":
                    # UI-only consultation cue; this does not stage or adopt a revision.
                    self._mark_workshop_hud_edit(
                        workshop, workshop.hud_selected_line
                        if workshop.hud_editing else workshop.marked_line_index,
                    )
                row = record_workshop_agent_step(
                    workshop,
                    step,
                    phase=phase,
                    outcome="replied_from_verified_context",
                )
                turn_steps.append(row)
                workshop.awaiting_meaning_ack = (
                    step.action == "explain" and step.purpose == "understand_meaning"
                )
                workshop.awaiting_close_confirmation = False
                workshop.close_confirmation_source = None
                record_workshop_activity(workshop, now=event.observed_at)
                reply = step.speech
                if (
                    isinstance(observation, dict)
                    and observation.get("kind") == "revision_validation"
                    and observation.get("status") == "proposed"
                    and workshop.pending_revision
                ):
                    reply = (
                        f"{reply}\n{workshop.editing_line()}\n"
                        "よければ『その案で』って言ってな。"
                    )
                self._finish_workshop_agent_turn(
                    session,
                    event,
                    workshop=workshop,
                    player_text=player_text,
                    raw_player_text=raw_player_text,
                    base_verse=base_verse,
                    pending_before=pending_before,
                    turn_steps=turn_steps,
                    feedback_step=feedback_step or step,
                )
                LOGGER.warning(
                    "haiku_workshop_agent result=replied phase=%s action=%s purpose=%s "
                    "steps=%s player=%s",
                    phase,
                    step.action,
                    step.purpose,
                    len(turn_steps),
                    player_text[:100],
                )
                return [AudioAction(layer="speech", interrupt=False, text=reply)]

        # 三手を使い切った場合も、最後の実観測だけをコード固定で返す。
        reply = self._workshop_agent_observation_fallback(workshop, observation)
        record_workshop_activity(workshop, now=event.observed_at)
        self._finish_workshop_agent_turn(
            session,
            event,
            workshop=workshop,
            player_text=player_text,
            raw_player_text=raw_player_text,
            base_verse=base_verse,
            pending_before=pending_before,
            turn_steps=turn_steps,
            feedback_step=feedback_step,
        )
        return [AudioAction(layer="speech", interrupt=False, text=reply)]

    def _plan_workshop_agent_step(
        self,
        workshop: RecentHaikuWorkshop,
        player_text: str,
        *,
        raw_player_text: str,
        phase: str,
        observation: dict[str, object] | None,
        turn_steps: list[dict[str, object]],
    ) -> tuple[WorkshopAgentStep | None, str]:
        details = build_workshop_agent_details(
            workshop,
            player_text,
            original_player_text=raw_player_text,
            phase=phase,
            observation=observation,
            turn_steps=turn_steps,
        )
        fallback = {
            "action": "defer_to_legacy",
            "purpose": "other",
            "confidence": 0.0,
            "evidence": "",
            "speech": "",
            "checks": [],
            "close_after_action": False,
            "close_evidence": "",
            "findings": [],
            "line_reference": {
                "found": False,
                "concept_id": "unknown",
                "evidence": "",
                "confidence": 0.0,
            },
            "line_proposal": {
                "found": False,
                "target_fragment": "",
                "replacement_text": "",
                "evidence": "",
                "confidence": 0.0,
            },
        }
        try:
            payload = self.llm.generate_structured_json(
                StructuredGenerationRequest(
                    kind="haiku_workshop_agent_step",
                    fallback_value=fallback,
                    details=details,
                    temperature=0.25,
                    route="chat",
                    max_tokens=420,
                )
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "haiku_workshop_agent_step result=error phase=%s detail=%s",
                phase,
                exc,
            )
            return None, "generation_error"
        step, reason = finalize_workshop_agent_step(payload, details=details)
        raw_action = (
            str(payload.get("action") or "-") if isinstance(payload, dict) else "-"
        )
        raw_purpose = (
            str(payload.get("purpose") or "-") if isinstance(payload, dict) else "-"
        )
        LOGGER.warning(
            "haiku_workshop_agent_step result=%s phase=%s action=%s purpose=%s "
            "confidence=%.2f reason=%s",
            "accepted" if step is not None else "rejected",
            phase,
            step.action if step is not None else raw_action,
            step.purpose if step is not None else raw_purpose,
            step.confidence if step is not None else 0.0,
            reason,
        )
        return step, reason

    @staticmethod
    def _workshop_agent_observation_fallback(
        workshop: RecentHaikuWorkshop,
        observation: dict[str, object] | None,
    ) -> str:
        if not isinstance(observation, dict):
            return "どこを一緒に見たらええか、もう少し教えてな。"
        if observation.get("kind") == "revision_validation":
            if observation.get("status") == "proposed" and workshop.pending_revision:
                return (
                    "こんなんどうや。\n"
                    f"{workshop.editing_line()}\n"
                    "よければ『その案で』って言ってな。"
                )
            return "検査に通る案までは作れんかったわ。元の句はそのままや。"
        rows = observation.get("lines")
        if isinstance(rows, list) and "meter" in observation.get("checks", []):
            counts = [
                str(row.get("mora_count"))
                for row in rows
                if isinstance(row, dict) and row.get("mora_count") is not None
            ]
            if len(counts) == 3:
                return f"音数は上から{'・'.join(counts)}やで。どの行を一緒に見よか？"
        if "source" in observation.get("checks", []):
            recorded = [
                row
                for row in rows
                if isinstance(row, dict) and row.get("source_status") == "recorded"
            ] if isinstance(rows, list) else []
            if len(recorded) == 3:
                return "三行とも出典の記録があるで。どの行を一緒に見よか？"
            if not recorded:
                return "三行には出典の記録が見つからんかったわ。元の句はそのままや。"
            return (
                f"出典の記録があるのは三行中{len(recorded)}行やで。"
                "どの行を一緒に見よか？"
            )
        if "reading" in observation.get("checks", []):
            unavailable = [
                row
                for row in rows
                if isinstance(row, dict) and row.get("reading_status") != "known"
            ] if isinstance(rows, list) else []
            if unavailable:
                return "読みを確定できん行があったわ。元の句はそのままや。"
            return "今の三行の読みは確認できたで。どの言葉を一緒に見よか？"
        return "検査結果は確認できたで。どこを一緒に見よか？"

    def _finish_workshop_agent_turn(
        self,
        session: SessionInfo,
        event: GameEvent,
        *,
        workshop: RecentHaikuWorkshop,
        player_text: str,
        raw_player_text: str,
        base_verse: str,
        pending_before: str | None,
        turn_steps: list[dict[str, object]],
        feedback_step: WorkshopAgentStep | None = None,
    ) -> None:
        """改善記録を保存する。保存失敗はリアルタイム応答へ伝播させない。"""

        if self.memory is None:
            return
        try:
            if feedback_step is not None:
                problems = {finding.problem for finding in feedback_step.analysis.findings}
                if "forced_compression" in problems or "meter" in problems:
                    critique_kind = "forced_compress"
                elif problems.intersection({"unreadable", "unnatural_japanese", "reading"}):
                    critique_kind = "unreadable"
                elif "off_scene" in problems:
                    critique_kind = "off_context"
                elif (
                    feedback_step.action == "explain"
                    and feedback_step.purpose == "understand_meaning"
                ):
                    critique_kind = "ask_meaning"
                else:
                    critique_kind = "other"
                critique = self.memory.save_haiku_critique(
                    entry_id=workshop.entry_id,
                    kind=critique_kind,
                    player_text=raw_player_text,
                    # ターン開始時に未採用案があれば、感想・比較の対象は
                    # 正本ではなく実際に見ていた案として残す。
                    surface_at_time=pending_before or base_verse,
                    materials_snapshot=dict(workshop.materials or {}),
                    observed_at=event.observed_at,
                    session_id=session.session_id,
                )
                # 修正依頼そのものは次回発句へのhard/soft規則に自動昇格しない。
                # それ以外の明示指摘だけ、既存のsoft lessonへつなぐ。
                if feedback_step.action != "propose_revision":
                    for lesson in lessons_from_critique_kind(
                        critique_kind,
                        player_text=raw_player_text,
                    ):
                        self.memory.save_haiku_lesson(
                            lesson_type=str(lesson.get("lesson_type") or "other"),
                            note=str(lesson.get("note") or ""),
                            prefer_materials=bool(lesson.get("prefer_materials")),
                            forbidden_fragments=list(lesson.get("forbidden_fragments") or []),
                            from_entry_id=workshop.entry_id,
                            from_critique_id=str(critique.get("id") or "") or None,
                            observed_at=event.observed_at,
                            polarity=str(lesson.get("polarity") or "tighten"),
                            strength=float(lesson.get("strength") or 0.3),
                        )
            self.memory.save_haiku_workshop_turn(
                entry_id=workshop.entry_id,
                player_text=raw_player_text,
                semantic_player_text=player_text,
                base_verse=base_verse,
                pending_before=pending_before,
                pending_after=workshop.pending_revision,
                steps=turn_steps,
                observed_at=event.observed_at,
                session_id=session.session_id,
            )
        except OSError as exc:
            LOGGER.warning(
                "haiku_workshop_agent_record_failed session_id=%s detail=%s",
                session.session_id,
                exc,
            )

    @staticmethod
    def _player_line_revision_failure_reply(reasons: tuple[str, ...]) -> str:
        """局所置換の失敗理由を、本文を創作せず短く返す。"""

        reason_set = set(reasons)
        if "missing_target" in reason_set:
            return "『くさちのねよりくさちかな』みたいに、元の一行と新しい一行を教えてな。"
        if reason_set.intersection(
            {
                "target_fragment_not_readable",
                "target_fragment_not_found",
                "ambiguous_target_fragment",
                "target_conflict",
            }
        ):
            return "元の一行が今の句と一つに決まらへんかったわ。元の句をそのまま言ってから、新しい一行を教えてな。"
        if "pending_source_conflict" in reason_set:
            return "先に出した案を『その案で』か『元のまま』で決めてから直そか。"
        if reason_set.intersection({"not_hiragana", "verse_not_hiragana"}):
            return "読みを勝手に決めたくないから、置き換える言葉をひらがなで教えてな。"
        if reason_set.intersection({"meter_too_short", "meter_too_long", "meter_not_exact"}):
            return "その言葉やと音数が合わへんわ。ひらがなで五・七・五の音に合わせてみてな。"
        if "hard_forbidden_term" in reason_set:
            return "その言葉は今の句で使える材料と合わへんから、まだ置き換えんとくで。"
        if "duplicate_line" in reason_set:
            return "別の行と同じになってまうから、もう一つ違う言い方を試そか。"
        if "no_change" in reason_set:
            return "そこは今と同じ言葉やで。別の言い方があれば教えてな。"
        return "その置き換えはまだ安全に入れられへんかったわ。元の三行は変えてへんで。"

    def _analyze_workshop_feedback(
        self,
        workshop: RecentHaikuWorkshop,
        player_text: str,
    ) -> tuple[WorkshopAnalysis, str]:
        """句の状態を変えず、intent と修正対象だけを structured 抽出する。"""

        details = build_workshop_intent_llm_details(workshop, player_text)
        try:
            payload = self.llm.generate_structured_json(
                StructuredGenerationRequest(
                    kind="haiku_workshop_intent",
                    fallback_value={
                        "intent": "soft_default",
                        "confidence": 0.0,
                        "repair_requested": False,
                        "findings": [],
                        "evaluation": {
                            "found": False,
                            "sentiment": "unknown",
                            "scope": "unknown",
                            "evidence": "",
                            "confidence": 0.0,
                        },
                        "close_request": {
                            "found": False,
                            "scope": "unknown",
                            "evidence": "",
                            "confidence": 0.0,
                        },
                        "line_reference": {
                            "found": False,
                            "concept_id": "unknown",
                            "evidence": "",
                            "confidence": 0.0,
                        },
                        "line_proposal": {
                            "found": False,
                            "target_fragment": "",
                            "replacement_text": "",
                            "evidence": "",
                            "confidence": 0.0,
                        },
                    },
                    details=details,
                    temperature=0.0,
                    route="chat",
                    max_tokens=320,
                )
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("haiku_workshop_intent_failed detail=%s", exc)
            return WorkshopAnalysis(), "soft_default"
        analysis = finalize_workshop_analysis_payload(
            payload,
            verse_lines=workshop_verse_lines(workshop.editing_line()),
            player_text=player_text,
        )
        if (
            analysis.intent == "soft_default"
            and not analysis.findings
            and analysis.line_proposal is None
            and analysis.line_reference is None
            and analysis.close_request is None
            and analysis.evaluation is None
        ):
            return analysis, "soft_default"
        return analysis, "chat"

    def _analyze_workshop_evaluation_with_chat(
        self,
        workshop: RecentHaikuWorkshop,
        player_text: str,
    ) -> tuple[WorkshopEvaluation | None, str]:
        """一次分類で未確定の評価だけを、会話モデルへ直接問い合わせる。"""

        fallback = {
            "found": False,
            "sentiment": "unknown",
            "scope": "unknown",
            "evidence": "",
            "confidence": 0.0,
        }
        try:
            payload = self.llm.generate_structured_json(
                StructuredGenerationRequest(
                    kind="haiku_workshop_evaluation",
                    fallback_value=fallback,
                    details={
                        "verse": workshop.editing_line(),
                        "player_text": player_text,
                        "workshop_context": workshop_context_details(workshop),
                    },
                    temperature=0.0,
                    route="chat",
                    max_tokens=96,
                )
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "haiku_workshop_evaluation stage=chat_second_pass "
                "result=error detail=%s",
                exc,
            )
            return None, "generation_error"
        evaluation, reason = validate_workshop_evaluation_payload(
            payload,
            player_text=player_text,
        )
        if evaluation is not None:
            LOGGER.warning(
                "haiku_workshop_evaluation stage=chat_second_pass result=accepted "
                "sentiment=%s scope=%s confidence=%.2f evidence=%s",
                evaluation.sentiment,
                evaluation.scope,
                evaluation.confidence,
                evaluation.evidence[:80],
            )
        return evaluation, reason

    def _analyze_pending_revision_reply(
        self,
        workshop: RecentHaikuWorkshop,
        player_text: str,
    ) -> tuple[PendingRevisionAnalysis, str]:
        """未採用案への自然文を常駐 chat LLM で分類し、実行はまだ行わない。"""

        details = build_pending_revision_llm_details(workshop, player_text)
        try:
            payload = self.llm.generate_structured_json(
                StructuredGenerationRequest(
                    kind="haiku_workshop_pending_decision",
                    fallback_value={
                        "action": "uncertain",
                        "confidence": 0.0,
                        "evidence": "",
                        "close_request": {
                            "found": False,
                            "scope": "unknown",
                            "evidence": "",
                            "confidence": 0.0,
                        },
                    },
                    details=details,
                    temperature=0.0,
                    route="chat",
                    max_tokens=96,
                )
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("haiku_workshop_pending_decision_failed detail=%s", exc)
            return PendingRevisionAnalysis(), "fallback"
        analysis = finalize_pending_revision_payload(
            payload,
            player_text=player_text,
        )
        return analysis, "chat"

    def _workshop_revision_reply(
        self,
        workshop: RecentHaikuWorkshop,
        analysis: WorkshopAnalysis,
        player_text: str,
    ) -> tuple[str, str]:
        """大きい haiku route に対象行だけを直させ、未保存の案として保持する。"""

        observation = self._stage_workshop_revision(workshop, analysis)
        status = str(observation.get("status") or "rejected")
        validation_codes = observation.get("validation_codes")
        path = (
            str(validation_codes[0])
            if isinstance(validation_codes, list) and validation_codes
            else status
        )
        if status != "proposed" or not workshop.pending_revision:
            if path == "pending_exists":
                return (
                    "先の案を『その案で』か『元のまま』で決めてから、次を直そか。",
                    path,
                )
            if path == "no_target":
                return "どの行を直すか、気になる言葉をもう少し教えてな。", path
            return "まだうまく直しきれんかったわ。元の句はそのままや。", path
        # 修正句と採用条件はコードが固定し、対話AIには差し出し方だけを任せる。
        introduction, introduction_path = self._collaborator_workshop_reply(
            workshop,
            player_text,
            kind="request_repair",
            analysis=analysis,
            repair_state="proposed",
            proposed_revision=workshop.pending_revision,
        )
        return (
            f"{introduction}\n{workshop.editing_line()}\nよければ『その案で』って言ってな。",
            f"proposed_{introduction_path}",
        )

    def _stage_workshop_revision(
        self,
        workshop: RecentHaikuWorkshop,
        analysis: WorkshopAnalysis,
    ) -> dict[str, object]:
        """既存editorと全検査を実行し、未保存案または機械可読な失敗を返す。"""

        base_text = workshop.display_line()
        if workshop.pending_revision:
            return {
                "kind": "revision_validation",
                "status": "rejected",
                "base_text": base_text,
                "proposed_verse": None,
                "validation_codes": ["pending_exists"],
            }
        targets = repair_target_indices(analysis.findings)
        self._mark_workshop_hud_edit(workshop, targets[0] if len(targets) == 1 else None)
        if not targets:
            return {
                "kind": "revision_validation",
                "status": "rejected",
                "base_text": base_text,
                "proposed_verse": None,
                "validation_codes": ["no_target"],
            }
        verse_lines = workshop_verse_lines(base_text)
        atoms = source_atoms_from_materials(workshop.materials)
        line_sources = line_source_ids_from_materials(
            workshop.materials,
            verse_lines=verse_lines,
            allowed_atom_ids={atom.atom_id for atom in atoms},
        )
        try:
            result = generate_workshop_revision(
                self.llm,
                original_text=base_text,
                target_indices=targets,
                findings=tuple(finding.to_dict() for finding in analysis.findings),
                source_atoms=atoms,
                original_line_sources=line_sources,
                details={
                    **dict(workshop.materials or {}),
                    "workshop_context": workshop_context_details(workshop),
                },
                max_tokens=self.settings.haiku_structured_max_tokens,
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("haiku_workshop_revision_failed detail=%s", exc)
            workshop.last_repair_feedback = {
                "base_text": base_text,
                "validation_passed": False,
                "failure_reason": "generation_error",
                "retry_feedback": None,
                "proposed_text": None,
            }
            return {
                "kind": "revision_validation",
                "status": "rejected",
                "base_text": base_text,
                "proposed_verse": None,
                "validation_codes": ["generation_error"],
            }
        workshop.last_repair_feedback = {
            "base_text": base_text,
            "validation_passed": result.accepted,
            "failure_reason": result.failure_reason,
            "retry_feedback": result.retry_feedback,
            "proposed_text": result.text,
        }
        validation_codes = self._workshop_revision_validation_codes(result)
        if not result.accepted or not result.text:
            return {
                "kind": "revision_validation",
                "status": "rejected",
                "base_text": base_text,
                "proposed_verse": None,
                "target_line_indices": list(targets),
                "validation_codes": validation_codes or ["rejected"],
            }
        pending_lines = build_haiku_lines(
            result.text,
            line_sources=result.line_sources,
            provenance="generated_confirmed",
        )
        if len(pending_lines) != 3:
            workshop.last_repair_feedback["validation_passed"] = False
            workshop.last_repair_feedback["failure_reason"] = "invalid_line_records"
            return {
                "kind": "revision_validation",
                "status": "rejected",
                "base_text": base_text,
                "proposed_verse": None,
                "target_line_indices": list(targets),
                "validation_codes": ["invalid_line_records"],
            }
        workshop.pending_revision = result.text
        workshop.pending_revision_surface_text = result.text
        workshop.pending_revision_lines = pending_lines
        workshop.pending_revision_line_sources = list(result.line_sources)
        workshop.pending_revision_base_text = result.base_text
        workshop.pending_revision_edits = [edit.to_record() for edit in result.edits]
        workshop.pending_revision_edit_contract = result.edit_contract
        workshop.pending_revision_source = "generated_confirmed"
        return {
            "kind": "revision_validation",
            "status": "proposed",
            "base_text": base_text,
            "proposed_verse": workshop.editing_surface(),
            "target_line_indices": list(targets),
            "validation_codes": ["edit_contract_passed", "grounding_passed", "meter_passed"],
        }

    @staticmethod
    def _workshop_revision_validation_codes(result: object) -> list[str]:
        codes: list[str] = []
        failure_reason = str(getattr(result, "failure_reason", "") or "").strip()
        if failure_reason:
            codes.append(failure_reason)
        feedback = getattr(result, "retry_feedback", None)
        if isinstance(feedback, dict):
            global_failures = feedback.get("global_failure_reasons")
            if isinstance(global_failures, list):
                codes.extend(str(value) for value in global_failures if value)
            line_failures = feedback.get("line_failures")
            if isinstance(line_failures, list):
                for row in line_failures:
                    if not isinstance(row, dict):
                        continue
                    reasons = row.get("failure_reasons")
                    if isinstance(reasons, list):
                        codes.extend(str(value) for value in reasons if value)
        return list(dict.fromkeys(codes))[:12]

    def _ask_meaning_workshop_reply(
        self,
        workshop: RecentHaikuWorkshop,
        player_text: str,
    ) -> tuple[str, str]:
        """当時の材料・見どころ・対話を比較して説明する。句と記録は変更しない。"""

        return self._collaborator_workshop_reply(
            workshop,
            player_text,
            kind="ask_meaning",
            reply_goal="explain_meaning",
        )

    def _collaborator_workshop_reply(
        self,
        workshop: RecentHaikuWorkshop,
        player_text: str,
        *,
        kind: str,
        analysis: WorkshopAnalysis | None = None,
        repair_state: str = "not_run",
        proposed_revision: str | None = None,
        reply_goal: str = "respond_to_feedback",
    ) -> tuple[str, str]:
        """共同編集者モード leaf。実行結果だけを受けて自由に一言返す。"""
        template_kind = kind if kind != "soft_default" else "soft_default"
        if repair_state == "proposed":
            fallback = "こんなんどうや。"
        elif reply_goal == "explain_meaning":
            # 照合先自体が誤っていることがあるため、失敗時も材料名を断定しない。
            fallback = "すまんな。その言葉の意味、今うまく説明できへんわ。"
        elif reply_goal == "ask_revision_direction":
            fallback = "どの行や言葉を、どう直したいか教えてな。"
        else:
            fallback = render_workshop_reply(
                template_kind,
                workshop,
                player_text=player_text,
            )
        verse = workshop.editing_line() or ""
        materials = materials_speech_line(workshop)
        details = {
            "verse": verse,
            "materials_speech": materials,
            "workshop_context": workshop_context_details(workshop),
            "player_text": player_text,
            "intent_kind": kind,
            "reply_goal": reply_goal,
            "character_mode": "workshop",
            "workshop_findings": [
                finding.to_dict() for finding in (analysis.findings if analysis else ())
            ],
            "repair_state": repair_state,
            "proposed_revision": proposed_revision,
        }
        try:
            text = self.llm.generate_leaf_text(
                LeafGenerationRequest(
                    kind="haiku_workshop_reply",
                    fallback_text=fallback,
                    details=details,
                    temperature=0.55,
                    route="chat",
                )
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("haiku_workshop_reply_failed detail=%s", exc)
            return fallback, "template"
        cleaned = (text or "").strip()
        if not cleaned or cleaned == fallback:
            return fallback, "template"
        return cleaned, "collaborator_llm"

    def _note_workshop_after_actions(
        self,
        session: SessionInfo,
        event: GameEvent,
        actions: list[AudioAction],
    ) -> None:
        """hard off-topic で chat speech が出たら drift。

        Stage2: soft 既定の句の話は workshop 経路で処理済み → drift しない。
        """
        workshop = session.haiku_workshop
        if not is_active(workshop) or workshop is None:
            return
        player_input = session.machine.player_input
        text = (player_input.raw_text or "").strip()
        if not text or not player_input.breaks_silence:
            return
        # workshop 経路で既に処理済みなら drift しない
        verse = workshop.editing_line() if workshop else None
        open_kind = workshop_open_intent(
            text,
            verse=verse,
            player_input=player_input,
        )
        if open_kind is not None:
            return
        if player_input.revised_haiku_text or player_input.asks_haiku_recall:
            return
        if player_input.reading_correction is not None:
            return
        if player_input.knowledge_query is not None:
            return
        has_speech = any(bool(a.text) and a.layer == "speech" for a in actions)
        if not has_speech:
            LOGGER.warning(
                "haiku_workshop_idle session_id=%s player=%s "
                "(open, hard_off_topic, no speech)",
                session.session_id,
                text[:100],
            )
            return
        speech_preview = next(
            (a.text for a in actions if a.layer == "speech" and a.text),
            "",
        )
        prev_drift = workshop.drift_count
        updated = record_drift(workshop, now=event.observed_at)
        LOGGER.warning(
            "haiku_workshop_drift session_id=%s player=%s speech=%s drift=%s→%s",
            session.session_id,
            text[:100],
            (speech_preview or "")[:120],
            prev_drift,
            workshop.drift_count if workshop else prev_drift,
        )
        if updated is not None and not updated.open:
            LOGGER.warning(
                "haiku_workshop_closed session_id=%s reason=drift",
                session.session_id,
            )
            session.haiku_workshop = None

    def _handle_reading_correction(
        self,
        session: SessionInfo,
        event: GameEvent,
        correction: object,
    ) -> list[AudioAction]:
        assert self.memory is not None
        surface = str(getattr(correction, "surface", "") or "").strip()
        reading = str(getattr(correction, "reading", "") or "").strip()
        wrong = getattr(correction, "wrong_reading", None)
        wrong_reading = str(wrong).strip() if wrong else None
        if not surface or not reading:
            return [AudioAction(layer="speech", interrupt=False, text="読み、もう一回教えてくれへん？")]

        # 「そうち→くさち」のように surface が誤読だけのとき、直近バイオーム名を正本にする
        import re

        if re.fullmatch(r"[ぁ-んー]+", surface):
            biome_label = session.machine._biome_label(event.world.biome)
            if biome_label and biome_label != "そのへん":
                wrong_reading = wrong_reading or surface
                surface = biome_label

        source = None
        biome_id = session.machine._normalized_biome(event.world.biome)
        if biome_id:
            source = f"biome:{biome_id}"

        self.memory.save_reading_correction(
            surface=surface,
            reading=reading,
            wrong_reading=wrong_reading,
            source=source,
            observed_at=event.observed_at,
            session_id=session.session_id,
        )
        text = f"{surface}は「{reading}」やね。覚え直したで。"
        return [AudioAction(layer="speech", interrupt=False, text=text)]

    def _handle_haiku_recall(
        self,
        session: SessionInfo,
        event: GameEvent,
        player_input: object,
    ) -> list[AudioAction]:
        assert self.memory is not None
        query = getattr(player_input, "haiku_recall_query", None)
        biome_hint = getattr(player_input, "haiku_recall_biome_hint", None)
        place_label = None
        biome_ids: tuple[str, ...] = ()
        if query is not None:
            biome = getattr(query, "biome_id", None) or biome_hint
            biome_ids = tuple(getattr(query, "biome_ids", ()) or ())
            place_label = getattr(query, "place_label", None)
            since = getattr(query, "since", None)
            until = getattr(query, "until", None)
            time_label = getattr(query, "time_label", None)
        else:
            biome = biome_hint
            since = until = time_label = None

        # 場所も期間も無い「いつ頃の句」などは全件から新しい順（現在地に縛らない）
        hits = self.memory.search_haiku_memory(
            biome=biome if not biome_ids else None,
            biome_ids=biome_ids or None,
            since=since,
            until=until,
            limit=3,
        )
        place_speech = place_label or biome
        if not hits and (biome or biome_ids or since or until):
            # 条件を緩めて再検索
            hits = self.memory.search_haiku_memory(limit=3)
            if hits and (place_speech or time_label):
                soft = "ぴったりは無いけど、覚えとる句やと…"
            else:
                soft = "覚えとる句やと…"
        else:
            soft = "覚えとる句やと…"
            if time_label and place_speech:
                soft = f"{time_label}の{place_speech}あたりで覚えとる句やと…"
            elif time_label:
                soft = f"{time_label}の句やと…"
            elif place_speech:
                soft = f"{place_speech}で覚えとる句やと…"

        if not hits:
            return [AudioAction(layer="speech", interrupt=False, text="それに合う句、まだ覚えとらへんで。")]

        lines: list[str] = [soft]
        for hit in hits[:2]:
            world = hit.get("world") if isinstance(hit.get("world"), dict) else {}
            place = world.get("biome") or "どこか"
            when = str(hit.get("created_at") or "")[:10]  # YYYY-MM-DD
            original = str(hit.get("original_text") or "").replace("\n", " / ")
            revised = hit.get("revised_text")
            prefix = f"{when} {place}" if when else str(place)
            if revised:
                revised_line = str(revised).replace("\n", " / ")
                lines.append(f"{prefix}: 元「{original}」直し「{revised_line}」")
            else:
                lines.append(f"{prefix}: 「{original}」")
        return [AudioAction(layer="speech", interrupt=False, text=" ".join(lines))]

    def _action_contains_haiku(self, action: AudioAction, haiku: HaikuEmission) -> bool:
        text = self._compact_text(action.text or "")
        haiku_text = self._compact_text(haiku.text)
        return bool(haiku_text and haiku_text in text)

    def _compact_text(self, text: str) -> str:
        return "".join(text.replace("ここで一句。", "").replace("ここで一句", "").split())

    def _event_advancement_ids(self, event: GameEvent) -> list[str]:
        ids = list(event.meta.advancements)
        extra = getattr(event.meta, "__pydantic_extra__", None) or {}
        for key in ("advancement", "advancements", "unlocked_advancement", "unlocked_advancements"):
            value = extra.get(key)
            if isinstance(value, str):
                ids.append(value)
            elif isinstance(value, list):
                ids.extend(str(item) for item in value if item)
        seen: set[str] = set()
        result: list[str] = []
        for advancement_id in ids:
            normalized = str(advancement_id).strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                result.append(normalized)
        return result

    def _ensure_session(self, event: GameEvent, session_id: str | None) -> SessionInfo:
        if session_id and session_id in self.sessions:
            return self.sessions[session_id]

        implicit_id = session_id or self._implicit_session_id(event)
        if implicit_id not in self.sessions:
            machine = DogidoStateMachine(self.settings, llm=self.llm)
            session = SessionInfo(
                session_id=implicit_id,
                schema_version=event.schema_version,
                adapter_name=event.adapter,
                adapter_version=event.meta.adapter_build or "implicit",
                game=event.game,
                player_name=event.player.name or "unknown",
                profile_name=event.meta.profile_name,
                call_name=event.meta.call_name or self.settings.default_call_name,
                capabilities=[],
                execution_capabilities=[],
                created_at=datetime.now().astimezone(),
                machine=machine,
            )
            self._bind_dialogue_provider(session)
            self.sessions[implicit_id] = session
            self.audio.prewarm_speech_texts(
                self._fallback_speech_catalog(event.meta.call_name or self.settings.default_call_name)
            )
        return self.sessions[implicit_id]

    def _bind_dialogue_provider(self, session: SessionInfo) -> None:
        session.machine.dialogue_context_provider = lambda: session.dialogue
        session.machine.foreground_dialogue_provider = (
            lambda: session.foreground_dialogue.snapshot()
        )
        if (
            self.settings.main_language_dialogue_enabled
            and self.settings.llm_enabled
            and session.language_runtime is None
        ):
            web = self._new_main_language_web(session.session_id)
            session.language_runtime = MainLanguageRuntime(
                self.llm,
                ledger=session.dialogue_turns,
                foreground=session.foreground_dialogue,
                web=web,
                history_provider=session.dialogue.prompt_turns,
                situation_provider=session.dialogue.situation_lines,
                topic_fresh_ms=self.settings.conversation_topic_fresh_ms,
                pending_address_ttl_ms=self.settings.conversation_pending_address_ttl_ms,
            )
        # open 中の句 pin を player_chat details へ（履歴に依存しない）
        session.machine.haiku_workshop_provider = lambda: session.haiku_workshop
        # 次回発句用の薄い lessons
        session.machine.haiku_lessons_provider = lambda: (
            self.memory.list_recent_haiku_lessons(limit=3) if self.memory is not None else []
        )

    def _new_main_language_web(self, session_id: str) -> object | None:
        """利用可能な専用経路だけを休眠状態でsessionへ渡す。"""

        if not self.settings.main_language_web_enabled:
            LOGGER.info(
                "main_language_web status=disabled session_id=%s",
                session_id,
            )
            return None
        try:
            if self._main_language_web_factory is not None:
                web = self._main_language_web_factory()
                status = "injected_ready" if web is not None else "injected_unavailable"
            else:
                web, availability = build_main_web_research()
                status = availability.reason
        except Exception as exc:  # noqa: BLE001 - 任意Web依存でsession作成を壊さない
            LOGGER.warning(
                "main_language_web status=factory_failed session_id=%s error=%s",
                session_id,
                type(exc).__name__,
            )
            return None
        if web is not None and not callable(getattr(web, "search", None)):
            LOGGER.warning(
                "main_language_web status=invalid_provider session_id=%s",
                session_id,
            )
            return None
        log = LOGGER.info if web is not None else LOGGER.warning
        log(
            "main_language_web status=%s session_id=%s",
            status,
            session_id,
        )
        return web

    def _implicit_session_id(self, event: GameEvent) -> str:
        player = (event.player.name or "player").replace(" ", "_")
        adapter = event.adapter.replace(" ", "_")
        return f"implicit_{adapter}_{player}"

    def _output_flags(self, actions: list[AudioAction]) -> OutputFlags:
        flags = OutputFlags()
        for action in actions:
            if action.layer == "panic_cue":
                flags.panic_cue_enqueued = True
            elif action.layer == "callout":
                flags.callout_enqueued = True
            elif action.layer == "speech":
                flags.speech_enqueued = True
        return flags

    def _fallback_speech_catalog(self, call_name: str | None) -> list[str]:
        texts = response_prewarm_texts(call_name)
        texts.extend(fallback_prewarm_texts(call_name))
        seen: set[str] = set()
        result: list[str] = []
        for text in texts:
            if text and text not in seen:
                seen.add(text)
                result.append(text)
        return result
