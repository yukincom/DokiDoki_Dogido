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
from dogido_server.haiku.workshop import (
    RecentHaikuWorkshop,
    classify_workshop_intent,
    close_workshop,
    extract_conversational_revise,
    is_open,
    lessons_from_critique_kind,
    loosen_lesson_for_praise,
    maybe_close_for_time,
    open_from_emission,
    record_drift,
    record_workshop_activity,
    render_workshop_reply,
)
from dogido_server.llm import DogidoLLMRouter
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
        self.memory = MemoryStore(settings.memory_dir) if settings.memory_enabled else None
        if self.memory is not None:
            from dogido_server.catalog_readings import configure_corrections_path

            configure_corrections_path(self.memory.catalog_corrections_path)

    def warmup(self) -> None:
        self.llm.preload()
        self.audio.prewarm_speech_texts(self._fallback_speech_catalog(self.settings.default_call_name))

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
        attached_player_text: str | None = None
        if session.pending_player_text and not (event.meta.user_text or "").strip():
            attached_player_text = session.pending_player_text
            event.meta.user_text = attached_player_text
            session.pending_player_text = None

        # pin の時間切れ（発話の有無に関係なく）
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

        machine_result = session.machine.process(event)
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
        actions = list(machine_result.actions)
        actions.extend(self._memory_actions(session, event, actions, machine_result.haiku_emission))
        self._update_dialogue_context(session, event, actions)
        # 句と無関係な speech が出た（通常 chat）→ drift
        self._note_workshop_after_actions(session, event, actions)

        # 話しかけをイベントに載せたが speech が出なかった場合は捨てずに再キュー
        # （ambient_mob 枝や panic 枝に食われたケースの取りこぼし防止）
        if attached_player_text and self._should_requeue_player_input(session, actions):
            if not session.pending_player_text:
                session.pending_player_text = attached_player_text
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

    def push_player_input(self, text: str) -> dict[str, object]:
        """音声入力などゲーム外からのプレイヤー発話を、直近のアクティブセッションへ届ける。"""
        normalized = (text or "").strip()
        if not normalized:
            return {"accepted": False, "reason": "empty_text"}
        if not self.sessions:
            return {"accepted": False, "reason": "no_active_session"}
        session = max(
            self.sessions.values(),
            key=lambda candidate: candidate.last_seen_at or datetime.min.replace(tzinfo=timezone.utc),
        )
        session.pending_player_text = normalized
        LOGGER.warning(
            "player_input_pushed session_id=%s text=%s",
            session.session_id,
            normalized[:80],
        )
        return {"accepted": True, "session_id": session.session_id}

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
            session.dialogue.add_player(player_input.raw_text, at=now)

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
    ) -> list[AudioAction]:
        player_input = session.machine.player_input
        extra_actions: list[AudioAction] = []
        if self.memory is None:
            if (
                player_input.asks_save_last_haiku
                or player_input.player_haiku_text
                or player_input.revised_haiku_text
                or player_input.reading_correction is not None
                or player_input.asks_haiku_recall
            ):
                extra_actions.append(AudioAction(layer="speech", interrupt=False, text="記憶機能は今止まっとるで。"))
            # pin の講評返事は memory 無しでも動く
            extra_actions.extend(self._haiku_workshop_actions(session, event))
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

        if player_input.reading_correction is not None:
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

        # 川柳 workshop（pin が open のとき、自然な突っ込みを優先）
        workshop_actions = self._haiku_workshop_actions(session, event)
        if workshop_actions:
            return workshop_actions

        return []

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
        materials: dict[str, object] = {}
        if emission.interpretation:
            materials["interpretation"] = emission.interpretation
        if emission.biome:
            materials["biome"] = emission.biome
        if emission.structure:
            materials["structure"] = emission.structure
        if emission.time_phase:
            materials["time_phase"] = emission.time_phase
        session.haiku_workshop = open_from_emission(
            emission,
            materials=materials,
            entry_id=entry_id,
            now=now,
        )
        LOGGER.warning(
            "haiku_workshop_opened session_id=%s text=%s entry_id=%s",
            session.session_id,
            (emission.text or "")[:60],
            entry_id or "-",
        )

    def _save_haiku_revision_reply(
        self,
        session: SessionInfo,
        event: GameEvent,
        revised_text: str,
        *,
        source: str,
    ) -> list[AudioAction]:
        if session.last_haiku_emission is None:
            return [AudioAction(layer="speech", interrupt=False, text="直す元の句がまだないで。")]
        assert self.memory is not None
        self.memory.save_haiku_feedback(
            session.last_haiku_emission,
            revised_text=revised_text,
            observed_at=event.observed_at,
        )
        if is_open(session.haiku_workshop):
            close_workshop(session.haiku_workshop, reason="revise")
            session.haiku_workshop = None
        LOGGER.warning(
            "haiku_revision_saved session_id=%s source=%s text=%s",
            session.session_id,
            source,
            revised_text[:60],
        )
        return [AudioAction(layer="speech", interrupt=False, text="元の句と直し、覚えといたで。")]

    def _haiku_workshop_actions(
        self,
        session: SessionInfo,
        event: GameEvent,
    ) -> list[AudioAction]:
        workshop = session.haiku_workshop
        if not is_open(workshop) or workshop is None:
            return []
        player_input = session.machine.player_input
        text = (player_input.raw_text or "").strip()
        if not text or player_input.wants_quiet:
            return []
        if (player_input.normalized_text or "").startswith("/"):
            return []

        # H4: 自然文の直し（workshop open 中）
        conversational = extract_conversational_revise(text)
        if conversational and self.memory is not None:
            return self._save_haiku_revision_reply(
                session, event, conversational, source="conversational"
            )
        if conversational and self.memory is None:
            return [AudioAction(layer="speech", interrupt=False, text="記憶機能は今止まっとるで。")]

        # formal 川柳操作は上で処理済み。ここでは自然文の講評のみ。
        kind = classify_workshop_intent(text)
        if kind is None:
            return []

        now = event.observed_at
        if kind == "close":
            close_workshop(workshop, reason="explicit")
            session.haiku_workshop = None
            reply = render_workshop_reply("close", workshop, player_text=text)
            return [AudioAction(layer="speech", interrupt=False, text=reply)]

        record_workshop_activity(workshop, now=now)
        critique_kind = {
            "praise": "praise",
            "critique_forced": "forced_compress",
            "critique_gibberish": "unreadable",
            "critique_offscene": "off_context",
            "ask_meaning": "ask_meaning",
            "other_haiku": "other",
        }.get(kind, "other")

        critique_id: str | None = None
        if kind != "close" and self.memory is not None:
            try:
                row = self.memory.save_haiku_critique(
                    entry_id=workshop.entry_id,
                    kind=critique_kind,
                    player_text=text,
                    surface_at_time=workshop.surface_text,
                    materials_snapshot=dict(workshop.materials or {}),
                    observed_at=now,
                    session_id=session.session_id,
                )
                critique_id = str(row.get("id") or "") or None
                # H5.1: soft lesson / praise は loosen（新規常駐しない）
                if critique_kind == "praise":
                    loosen = loosen_lesson_for_praise()
                    self.memory.save_haiku_lesson(
                        lesson_type=str(loosen.get("lesson_type") or "*"),
                        note=str(loosen.get("note") or ""),
                        prefer_materials=bool(loosen.get("prefer_materials")),
                        from_entry_id=workshop.entry_id,
                        from_critique_id=critique_id,
                        observed_at=now,
                        polarity=str(loosen.get("polarity") or "loosen"),
                        strength=float(loosen.get("strength") or 0.0),
                    )
                else:
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

        if kind == "praise":
            close_workshop(workshop, reason="praise")
            session.haiku_workshop = None

        reply = render_workshop_reply(kind, workshop, player_text=text)
        return [AudioAction(layer="speech", interrupt=False, text=reply)]

    def _note_workshop_after_actions(
        self,
        session: SessionInfo,
        event: GameEvent,
        actions: list[AudioAction],
    ) -> None:
        """通常 chat 等で句以外の speech が出たら drift。"""
        workshop = session.haiku_workshop
        if not is_open(workshop) or workshop is None:
            return
        player_input = session.machine.player_input
        text = (player_input.raw_text or "").strip()
        if not text or not player_input.breaks_silence:
            return
        # workshop 経路で既に処理済みなら drift しない（reply が actions に含まれる）
        if classify_workshop_intent(text) is not None:
            return
        if player_input.revised_haiku_text or player_input.asks_haiku_recall:
            return
        if player_input.reading_correction is not None:
            return
        has_speech = any(bool(a.text) and a.layer == "speech" for a in actions)
        if not has_speech:
            return
        updated = record_drift(workshop, now=event.observed_at)
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
