from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Iterable
from uuid import uuid4

from dogido_server.audio import AudioDispatcher
from dogido_server.config import Settings
from dogido_server.dialogue_context import DialogueContext
from dogido_server.haiku.combat_pause import (
    CombatWorkshopInputAnalysis,
    build_combat_workshop_input_details,
    event_interrupts_workshop,
    fallback_combat_workshop_input_analysis,
    finalize_combat_workshop_input_payload,
    update_workshop_combat_state,
)
from dogido_server.haiku.workshop import (
    PendingRevisionAnalysis,
    PlayerLineReplacement,
    RecentHaikuWorkshop,
    advance_workshop_revision,
    build_player_line_revision,
    build_ask_meaning_llm_details,
    build_pending_revision_llm_details,
    build_workshop_intent_llm_details,
    classify_workshop_intent,
    clear_pending_revision,
    combat_resume_confirmation_decision,
    close_confirmation_decision,
    close_workshop,
    extract_conversational_revise,
    finalize_ask_meaning_reply,
    finalize_pending_revision_payload,
    finalize_workshop_analysis_payload,
    is_active,
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
    update_marked_workshop_line,
    wants_show_workshop_verse,
    WorkshopAnalysis,
    workshop_findings_from_records,
    workshop_open_intent,
    workshop_verse_lines,
)
from dogido_server.haiku.generation import generate_workshop_revision
from dogido_server.haiku.edit_contract import PLAYER_LINE_EDIT_CONTRACT_VERSION
from dogido_server.haiku.source_atoms import (
    line_source_ids_from_materials,
    source_atoms_from_materials,
)
from dogido_server.llm import DogidoLLMRouter, LeafGenerationRequest, StructuredGenerationRequest
from dogido_server.memory import MemoryStore
from dogido_server.models import (
    AcceptedEventResponse,
    AdapterSessionCreateRequest,
    AdapterSessionCreateResponse,
    BatchAcceptedResponse,
    CloseSessionResponse,
    GameEvent,
    HeartbeatResponse,
    OutputFlags,
    StateResponse,
)
from dogido_server.platform_ai import PlatformStructuredAIRouter
from dogido_server.state_machine import (
    AudioAction,
    DogidoStateMachine,
    HaikuEmission,
)
from dogido_server.state_machine.fallback_catalog import fallback_prewarm_texts
from dogido_server.state_machine.response_catalog import response_prewarm_texts

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
    created_at: datetime
    machine: DogidoStateMachine
    last_seen_at: datetime | None = None
    last_sequence: int | None = None
    seen_sequences: deque[int] = field(default_factory=lambda: deque(maxlen=2048))
    seen_sequence_set: set[int] = field(default_factory=set)
    seen_idempotency: deque[str] = field(default_factory=lambda: deque(maxlen=2048))
    seen_idempotency_set: set[str] = field(default_factory=set)
    first_event_logged: bool = False
    last_haiku_emission: HaikuEmission | None = None
    # 発句の pin（会話履歴とは別。open 中は句本文を忘れない）
    haiku_workshop: RecentHaikuWorkshop | None = None
    # 音声入力など外部から届いたプレイヤー発話。次のイベントの user_text に相乗りさせる
    pending_player_text: str | None = None
    pending_player_source: str | None = None
    # panic hold ログの重複抑制（同じ文は1回だけ）
    panic_hold_logged_text: str | None = None
    # 戦闘中断中にpanicで保留している同じ発話を、毎tick OS AIへ再送しない。
    combat_input_analysis_text: str | None = None
    combat_input_analysis: CombatWorkshopInputAnalysis | None = None
    combat_input_analysis_path: str = "none"
    # player_chat 用: 直近5往復 + 粗い出来事メモ
    dialogue: DialogueContext = field(default_factory=DialogueContext)

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


@dataclass(slots=True)
class ProcessedEvent:
    response: AcceptedEventResponse
    actions: list[AudioAction]


class DogidoService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.sessions: dict[str, SessionInfo] = {}
        self.audio = AudioDispatcher(settings)
        self.llm = DogidoLLMRouter(settings)
        self.platform_ai = PlatformStructuredAIRouter(settings)
        self.memory = MemoryStore(settings.memory_dir) if settings.memory_enabled else None
        if self.memory is not None:
            from dogido_server.catalog_readings import configure_corrections_path

            configure_corrections_path(self.memory.catalog_corrections_path)

    def warmup(self) -> None:
        self.llm.preload()
        # 可用性確認だけ。Apple の初回推論や Foundry のモデル download はしない。
        self.platform_ai.preload()
        self.audio.prewarm_speech_texts(self._fallback_speech_catalog(self.settings.default_call_name))

    def shutdown(self) -> None:
        """端末内モデルの worker / loaded model を解放する。"""

        self.platform_ai.close()

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
            created_at=now,
            machine=machine,
        )
        self._bind_dialogue_provider(session)
        self.sessions[session_id] = session
        self.audio.prewarm_speech_texts(self._fallback_speech_catalog(request.call_name or self.settings.default_call_name))
        LOGGER.info(
            "adapter_session_created session_id=%s adapter=%s version=%s schema=%s capabilities=%s",
            session_id,
            request.adapter_name,
            request.adapter_version,
            request.schema_version,
            ",".join(request.capabilities) or "none",
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
            response = AcceptedEventResponse(
                accepted=True,
                event_id=_new_id("evt"),
                session_id=session.session_id,
                sequence=event.sequence,
                deduplicated=True,
                server_time=datetime.now().astimezone(),
            )
            return ProcessedEvent(response=response, actions=[])

        # 音声入力（/api/v1/player-input）はチャットと同じ user_text 経路に合流させる。
        # アダプタからのチャットが同じイベントに載っていた場合はそちらを優先し、保留分は次イベントへ
        # 川柳の自分の世界中（preface〜本句）は入力を保持し、機械には載せない
        # 本句が何らかの理由で出せず pending が張り付いたら hold を強制解除
        if session.machine.state.pending_haiku_after_preface:
            session.machine._force_clear_stuck_pending_haiku(event.observed_at)

        attached_player_text: str | None = None
        attached_player_source = "text"
        combat_input_analysis: CombatWorkshopInputAnalysis | None = None
        combat_input_path = "none"
        haiku_pending_before = bool(session.machine.state.pending_haiku_after_preface)
        if haiku_pending_before:
            incoming = (event.meta.user_text or "").strip()
            if incoming:
                session.pending_player_text = incoming
                session.pending_player_source = "text"
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
            if (
                session.machine.state.mode in {"panic", "suppressed_panic"}
                and not paused_workshop_input
                and not paused_workshop_close
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
                attached_player_source = session.pending_player_source or "text"
                event.meta.user_text = attached_player_text
                session.pending_player_text = None
                session.pending_player_source = None
                session.combat_input_analysis_text = None
                session.combat_input_analysis = None
                session.combat_input_analysis_path = "none"
                LOGGER.warning(
                    "player_input_attached_after_hold session_id=%s text=%s",
                    session.session_id,
                    attached_player_text[:80],
                )

        # 固定表は本文へ、現在語彙による音近傍補正は会話解釈面だけへ載せる。
        # adapter の typed chat は source=text のため、音近傍補正しない。
        event, interpreted_player_text = self._apply_contextual_asr_to_event(
            event,
            session=session,
            input_source=attached_player_source,
        )

        # ambient 抑止: まだ相乗りしていない話しかけがキューにある
        session.machine.player_input_queued = bool((session.pending_player_text or "").strip())

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

        machine_result = session.machine.process(
            event,
            interpreted_user_text=interpreted_player_text,
        )
        if machine_result.haiku_emission is not None:
            session.last_haiku_emission = machine_result.haiku_emission
            # memory の有無に関わらず pin を立てる（entry_id は memory 側で埋める）
            if session.haiku_workshop is None or (
                session.haiku_workshop.surface_text != (machine_result.haiku_emission.text or "").strip()
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
        workshop_input_enabled = not workshop_input_consumed and not bool(
            session.haiku_workshop is not None
            and session.haiku_workshop.combat_paused
        )
        memory_actions = self._memory_actions(
            session,
            event,
            actions,
            machine_result.haiku_emission,
            allow_player_input=workshop_input_enabled,
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
        self._update_dialogue_context(session, event, actions)
        # 句と無関係な speech が出た（通常 chat）→ drift
        self._note_workshop_after_actions(session, event, actions)

        # 話しかけをイベントに載せたが speech が出なかった場合は捨てずに再キュー
        # （ambient_mob 枝や panic 枝に食われたケースの取りこぼし防止）
        if attached_player_text and self._should_requeue_player_input(session, actions):
            if not session.pending_player_text:
                session.pending_player_text = attached_player_text
                session.pending_player_source = attached_player_source
                LOGGER.warning(
                    "player_input_requeued session_id=%s mode=%s text=%s",
                    session.session_id,
                    machine_result.state.mode,
                    attached_player_text[:80],
                )

        response = AcceptedEventResponse(
            accepted=True,
            event_id=_new_id("evt"),
            session_id=session.session_id,
            sequence=event.sequence,
            deduplicated=False,
            state=StateResponse(mode=machine_result.state.mode, combat_active=machine_result.combat_active),
            outputs=self._output_flags(actions),
            server_time=datetime.now().astimezone(),
        )
        return ProcessedEvent(response=response, actions=actions)

    def process_batch(
        self,
        events: Iterable[GameEvent],
        session_id: str | None = None,
    ) -> tuple[BatchAcceptedResponse, list[AudioAction]]:
        processed = 0
        deduplicated = 0
        actions: list[AudioAction] = []

        for event in events:
            result = self.process_event(event, session_id=session_id)
            if result.response.deduplicated:
                deduplicated += 1
            else:
                processed += 1
                actions.extend(result.actions)

        response = BatchAcceptedResponse(
            accepted=True,
            received=processed + deduplicated,
            processed=processed,
            deduplicated=deduplicated,
            server_time=datetime.now().astimezone(),
        )
        return response, actions

    def heartbeat(self, session_id: str, last_sequence: int | None) -> HeartbeatResponse:
        session = self.sessions[session_id]
        session.last_seen_at = datetime.now().astimezone()
        if last_sequence is not None:
            session.last_sequence = last_sequence
        return HeartbeatResponse(
            ok=True,
            session_id=session_id,
            server_time=datetime.now().astimezone(),
        )

    def close_session(self, session_id: str) -> CloseSessionResponse:
        self.sessions.pop(session_id, None)
        return CloseSessionResponse(ok=True, session_id=session_id)

    def dispatch_actions(self, actions: list[AudioAction]) -> None:
        if not self.settings.audio_enabled or not actions:
            return
        self.audio.play_actions(actions)

    def push_player_input(self, text: str, *, source: str = "text") -> dict[str, object]:
        """音声入力などゲーム外からのプレイヤー発話を、直近のアクティブセッションへ届ける。"""
        from dogido_server.player_input.normalize import (
            is_known_voice_noise_text,
            is_too_short_voice_text,
            normalize_player_text,
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
        if input_source == "voice" and is_too_short_voice_text(normalized):
            LOGGER.warning(
                "player_input_rejected reason=too_short text=%s",
                normalized[:80],
            )
            return {"accepted": False, "reason": "too_short"}
        if not self.sessions:
            return {"accepted": False, "reason": "no_active_session"}
        session = max(
            self.sessions.values(),
            key=lambda candidate: candidate.last_seen_at or datetime.min.replace(tzinfo=timezone.utc),
        )
        session.pending_player_text = normalized
        session.pending_player_source = input_source
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
        return {"accepted": True, "session_id": session.session_id}

    def _apply_contextual_asr_to_event(
        self,
        event: GameEvent,
        *,
        session: SessionInfo,
        input_source: str,
    ) -> tuple[GameEvent, str | None]:
        """固定補正後の本文と、状態変更に使わない文脈解釈面を返す。"""
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
        has_speech = any(bool(action.text) and action.layer == "speech" for action in actions)
        return not has_speech

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

        player_input = session.machine.player_input
        if (
            player_input.breaks_silence
            and player_input.raw_text
            and not player_input.wants_quiet
            and not (player_input.normalized_text or "").startswith("/")
        ):
            session.dialogue.add_player(player_input.semantic_text, at=now)

        for action in actions:
            if action.layer != "speech" or not action.text:
                continue
            session.dialogue.add_dogido(action.text, at=now)

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
        # OS AIによる意味抽出を先に通し、読み訂正へ誤保存しない。
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
            if emission.biome and "biome" not in materials:
                materials["biome"] = emission.biome
            if emission.structure and "structure" not in materials:
                materials["structure"] = emission.structure
            if emission.time_phase and "time_phase" not in materials:
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
            (emission.text or "")[:60],
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
        parent_revision_id: str | None = None,
        keep_workshop_open: bool = False,
    ) -> list[AudioAction]:
        if session.last_haiku_emission is None:
            return [AudioAction(layer="speech", interrupt=False, text="直す元の句がまだないで。")]
        if self.memory is None:
            return [AudioAction(layer="speech", interrupt=False, text="記憶機能は今止まっとるで。")]
        revision = self.memory.save_haiku_feedback(
            session.last_haiku_emission,
            revised_text=revised_text,
            source=source,
            revision_line_sources=revision_line_sources,
            revision_edits=revision_edits,
            revision_edit_contract=revision_edit_contract,
            revision_base_text=revision_base_text,
            parent_revision_id=parent_revision_id,
            observed_at=event.observed_at,
        )
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

        # AI生成案は自動保存しない。自然文の意味はOS AI優先で閉じた action に
        # 変換し、現在pendingとの整合と保存・破棄はコードで扱う。
        if workshop.pending_revision:
            pending_analysis, pending_path = self._analyze_pending_revision_reply(
                workshop,
                semantic_text,
            )
            semantic_decision = {
                "accept_pending": "accept",
                "reject_pending": "reject",
            }.get(pending_analysis.action)
            semantic_close_requested = pending_analysis.close_request is not None
            # OS AI / chat が使えないときも、代表的な明示形だけは従来の
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
            # close / clear_lessons / praise のようなライフサイクル操作はコードの
            # 明示規則を保つ。それ以外の自然文は、OS AIの高信頼な意味分類を正に
            # してからコード側の保存・実行検証へ渡す。
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
                if analysis.line_proposal is None and not analysis.repair_requested:
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
            followup_control_turn = (
                workshop.awaiting_meaning_ack and effective_kind == "ack"
            ) or (
                workshop.awaiting_close_confirmation
                and (
                    effective_kind == "ack"
                    or close_confirmation_fallback in {"accept", "continue"}
                )
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
            if analysis.line_proposal is not None and not followup_control_turn:
                # 自然な置換提案は OS AI の意味抽出を優先する。上で得た
                # closed regex の候補は、OS AI が提案を確定できない場合だけ
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
                if len(target_indices) <= 1:
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
                else:
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
        # 終了確認へ進める。OS AIが使えない場合も代表的な短文だけfallbackする。
        if workshop.awaiting_meaning_ack:
            if effective_kind == "ack":
                workshop.awaiting_meaning_ack = False
                workshop.awaiting_close_confirmation = True
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
            if close_confirmation_fallback == "accept" or effective_kind == "ack":
                close_workshop(workshop, reason="meaning_confirmed")
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

        # 正規表現で曖昧でも、OS AIが発話中の一意な置換語と句中断片を取れた
        # 場合は先へ進める。どちらでも確定しなければコード固定で聞き返す。
        if replacement_parse.status == "ambiguous" and player_line_replacement is None:
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
                    text=(
                        f"こうなるで。\n{result.text}\n"
                        "このまま別の行も直せるで。よければ最後に『その案で』って言ってな。"
                    ),
                )
            ]

        if kind == "praise":
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
            payload = self.platform_ai.generate_structured_json(
                StructuredGenerationRequest(
                    kind="haiku_workshop_intent",
                    fallback_value={
                        "intent": "soft_default",
                        "confidence": 0.0,
                        "repair_requested": False,
                        "findings": [],
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
                    },
                    details=details,
                    temperature=0.0,
                    route="chat",
                    max_tokens=320,
                ),
                fallback=self.llm,
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
        ):
            return analysis, "soft_default"
        provider = str(payload.get("__dogido_platform_ai_provider") or "llm")
        return analysis, provider

    def _analyze_pending_revision_reply(
        self,
        workshop: RecentHaikuWorkshop,
        player_text: str,
    ) -> tuple[PendingRevisionAnalysis, str]:
        """未採用案への自然文をOS AI優先で分類し、実行はまだ行わない。"""

        details = build_pending_revision_llm_details(workshop, player_text)
        try:
            payload = self.platform_ai.generate_structured_json(
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
                ),
                fallback=self.llm,
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("haiku_workshop_pending_decision_failed detail=%s", exc)
            return PendingRevisionAnalysis(), "fallback"
        analysis = finalize_pending_revision_payload(
            payload,
            player_text=player_text,
        )
        provider = str(payload.get("__dogido_platform_ai_provider") or "llm")
        return analysis, provider

    def _workshop_revision_reply(
        self,
        workshop: RecentHaikuWorkshop,
        analysis: WorkshopAnalysis,
        player_text: str,
    ) -> tuple[str, str]:
        """大きい haiku route に対象行だけを直させ、未保存の案として保持する。"""

        if workshop.pending_revision:
            return "先の案を『その案で』か『元のまま』で決めてから、次を直そか。", "pending_exists"

        targets = repair_target_indices(analysis.findings)
        if not targets:
            return "どの行を直すか、気になる言葉をもう少し教えてな。", "no_target"
        verse_lines = workshop_verse_lines(workshop.display_line())
        atoms = source_atoms_from_materials(workshop.materials)
        line_sources = line_source_ids_from_materials(
            workshop.materials,
            verse_lines=verse_lines,
            allowed_atom_ids={atom.atom_id for atom in atoms},
        )
        try:
            result = generate_workshop_revision(
                self.llm,
                original_text=workshop.display_line(),
                target_indices=targets,
                findings=tuple(finding.to_dict() for finding in analysis.findings),
                source_atoms=atoms,
                original_line_sources=line_sources,
                details=dict(workshop.materials or {}),
                max_tokens=self.settings.haiku_structured_max_tokens,
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("haiku_workshop_revision_failed detail=%s", exc)
            return "まだうまく直しきれんかったわ。元の句はそのままや。", "failed"
        if not result.accepted or not result.text:
            return "まだうまく直しきれんかったわ。元の句はそのままや。", result.failure_reason or "rejected"
        workshop.pending_revision = result.text
        workshop.pending_revision_line_sources = list(result.line_sources)
        workshop.pending_revision_base_text = result.base_text
        workshop.pending_revision_edits = [edit.to_record() for edit in result.edits]
        workshop.pending_revision_edit_contract = result.edit_contract
        workshop.pending_revision_source = "generated_confirmed"
        # 修正句と採用条件はコードが固定し、対話AIには差し出し方だけを任せる。
        introduction, introduction_path = self._collaborator_workshop_reply(
            workshop,
            player_text,
            kind="request_repair",
            analysis=analysis,
            repair_state="proposed",
            proposed_revision=result.text,
        )
        return (
            f"{introduction}\n{result.text}\nよければ『その案で』って言ってな。",
            f"proposed_{introduction_path}",
        )

    def _ask_meaning_workshop_reply(
        self,
        workshop: RecentHaikuWorkshop,
        player_text: str,
    ) -> tuple[str, str]:
        """材料候補をコードが閉じ、LLM が選び＋短返事（失敗時はテンプレ）。"""
        details = build_ask_meaning_llm_details(workshop, player_text)
        candidates = list(details.get("candidates") or [])
        payload: dict[str, object] | None = None
        if candidates:
            try:
                payload = self.llm.generate_structured_json(
                    StructuredGenerationRequest(
                        kind="haiku_workshop_material_pick",
                        fallback_value={"pick_index": None, "reply": ""},
                        details=details,
                        temperature=0.35,
                        route="chat",
                        max_tokens=96,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning(
                    "haiku_workshop_material_pick_failed detail=%s",
                    exc,
                )
                payload = None
        return finalize_ask_meaning_reply(workshop, player_text, payload)

    def _collaborator_workshop_reply(
        self,
        workshop: RecentHaikuWorkshop,
        player_text: str,
        *,
        kind: str,
        analysis: WorkshopAnalysis | None = None,
        repair_state: str = "not_run",
        proposed_revision: str | None = None,
    ) -> tuple[str, str]:
        """共同編集者モード leaf。実行結果だけを受けて自由に一言返す。"""
        template_kind = kind if kind != "soft_default" else "soft_default"
        fallback = (
            "こんなんどうや。"
            if repair_state == "proposed"
            else render_workshop_reply(template_kind, workshop, player_text=player_text)
        )
        verse = workshop.editing_line() or ""
        materials = materials_speech_line(workshop)
        details = {
            "verse": verse,
            "materials_speech": materials,
            "player_text": player_text,
            "intent_kind": kind,
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
        # open 中の句 pin を player_chat details へ（履歴に依存しない）
        session.machine.haiku_workshop_provider = lambda: session.haiku_workshop
        # 次回発句用の薄い lessons
        session.machine.haiku_lessons_provider = lambda: (
            self.memory.list_recent_haiku_lessons(limit=3) if self.memory is not None else []
        )

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
