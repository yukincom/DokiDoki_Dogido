from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable
from uuid import uuid4

from dogido_server.audio import AudioDispatcher
from dogido_server.config import Settings
from dogido_server.llm import DogidoLLMRouter
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
)
from dogido_server.state_machine.fallback_catalog import fallback_prewarm_texts
from dogido_server.state_machine.response_catalog import response_prewarm_texts


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

    def remember_sequence(self, sequence: int) -> bool:
        if sequence in self.seen_sequence_set:
            return True
        if len(self.seen_sequences) == self.seen_sequences.maxlen:
            old = self.seen_sequences.popleft()
            self.seen_sequence_set.discard(old)
        self.seen_sequences.append(sequence)
        self.seen_sequence_set.add(sequence)
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
        session.last_seen_at = event.observed_at

        deduplicated = False
        if idempotency_key:
            deduplicated = session.remember_idempotency(idempotency_key)

        if not deduplicated and event.sequence is not None:
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
        response = AcceptedEventResponse(
            accepted=True,
            event_id=_new_id("evt"),
            session_id=session.session_id,
            sequence=event.sequence,
            deduplicated=False,
            state=StateResponse(mode=machine_result.state.mode, combat_active=machine_result.combat_active),
            outputs=self._output_flags(machine_result.actions),
            server_time=datetime.now().astimezone(),
        )
        return ProcessedEvent(response=response, actions=machine_result.actions)

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
