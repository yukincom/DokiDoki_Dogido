from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
import logging
from typing import Iterable
from uuid import uuid4

from dogido_server.audio import AudioDispatcher
from dogido_server.config import Settings
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

    def warmup(self) -> None:
        self.llm.preload()
        self.audio.prewarm_speech_texts(self._fallback_speech_catalog(self.settings.default_call_name))

    def create_session(self, request: AdapterSessionCreateRequest) -> AdapterSessionCreateResponse:
        now = datetime.now().astimezone()
        session_id = _new_id("ses")
        self.sessions[session_id] = SessionInfo(
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
            machine=DogidoStateMachine(self.settings, llm=self.llm),
        )
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

        machine_result = session.machine.process(event)
        if machine_result.haiku_emission is not None:
            session.last_haiku_emission = machine_result.haiku_emission
        actions = list(machine_result.actions)
        actions.extend(self._memory_actions(session, event, actions, machine_result.haiku_emission))
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
            if player_input.asks_save_last_haiku or player_input.player_haiku_text:
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
        return []

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
            self.sessions[implicit_id] = SessionInfo(
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
                machine=DogidoStateMachine(self.settings, llm=self.llm),
            )
            self.audio.prewarm_speech_texts(
                self._fallback_speech_catalog(event.meta.call_name or self.settings.default_call_name)
            )
        return self.sessions[implicit_id]

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
