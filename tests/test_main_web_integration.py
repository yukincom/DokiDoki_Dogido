"""本体の同意済みWeb接続。実ブラウザー・ネット・音声デバイスは使わない。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time

import pytest

from dogido_server.config import Settings
from dogido_server.dialogue.foreground import ForegroundDialogue
from dogido_server.dialogue.main_runtime import MainLanguageRuntime
from dogido_server.language_dialogue.conversation_turns import TurnLedger
from dogido_server.language_dialogue.main_web import inspect_main_web_availability
from dogido_server.language_dialogue.web_handoff import (
    WEB_DECLINED,
    WEB_DEPARTURE,
    WEB_PERMISSION_PROMPT,
)
from dogido_server.language_dialogue.web_research import WebResult
from dogido_server.models import (
    AdapterSessionCreateRequest,
    Certainty,
    CombatState,
    Direction,
    EventDescriptor,
    EventName,
    GameEvent,
    HorizontalDirection,
    MetaState,
    PassiveMob,
    PlayerState,
    Position,
    PriorityHint,
    SourceKind,
    Weather,
    WorldState,
)
from dogido_server.service import DogidoService


BASE = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
QUESTION = "朧語ってどういう意味？ウェブで調べて"


class MainWebLLM:
    def __init__(self) -> None:
        self.requests = []

    def generate_structured_json(self, request):  # type: ignore[no-untyped-def]
        self.requests.append(deepcopy(request))
        current = request.details.get("current", {})
        text = str(current.get("text") or "")
        if request.kind == "language_dialogue_interpretation":
            return {
                "dialogue_act": "information_request",
                "topic": "language",
                "relation": "new",
                "question": QUESTION,
                "target": "朧語",
                "facet": "meaning",
                "target_status": "explicit",
                "alternatives": [],
                "evidence": [{"turn_id": current["turn_id"], "quote": text}],
                "search_terms": ["朧語"],
                "clarification": "",
                "lookup_requested": True,
                "web_query": "朧語の意味",
            }
        if request.kind == "language_research_intent":
            return {
                "intent": "return" if "戻" in text else "report",
                "evidence": text,
            }
        return request.fallback_value

    def generate_leaf_text(self, request):  # type: ignore[no-untyped-def]
        return request.fallback_text


class CloseRecorder:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FailingCloseRecorder:
    def close(self) -> None:
        raise RuntimeError("close_failed")


class FakeMainWeb:
    def __init__(self, status: str = "page_opened") -> None:
        self.status = status
        self.calls = []
        self.client = CloseRecorder()

    def search(self, target, terms, facet, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(
            {
                "target": target,
                "terms": list(terms),
                "facet": facet,
                "web_query": kwargs.get("web_query"),
            }
        )
        if self.status == "page_opened":
            return WebResult(
                query="朧語の意味",
                status="page_opened",
                child_status="opened",
                search_url="https://www.google.com/search?q=test",
            )
        return WebResult(
            query="朧語の意味",
            status=self.status,
            search_status=self.status,
        )


def game_event(second: int, *, text: str | None = None, passive: bool = False) -> GameEvent:
    return GameEvent(
        schema_version="2026-05-24",
        adapter="test",
        observed_at=BASE + timedelta(seconds=second),
        sequence=second,
        event=EventDescriptor(
            name=EventName.STATUS_SNAPSHOT,
            source_kind=SourceKind.SYSTEM,
            priority_hint=PriorityHint.BACKGROUND,
            certainty=Certainty.HIGH,
        ),
        player=PlayerState(
            name="player",
            position=Position(x=0, y=64, z=0),
            dimension="minecraft:overworld",
            health=20,
            hunger=20,
        ),
        world=WorldState(
            weather=Weather.CLEAR,
            biome="plains",
            local_light=15,
            sky_visible=True,
        ),
        passive_mobs=(
            [
                PassiveMob(
                    type="cow",
                    distance=5,
                    direction=Direction(horizontal=HorizontalDirection.FRONT),
                )
            ]
            if passive
            else []
        ),
        combat=CombatState(hostiles_within_7=0, hostiles_within_10=0),
        meta=MetaState(user_text=text),
    )


def wait_for_envelope(runtime: MainLanguageRuntime, *, kind: str = "turn") -> dict:
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        for row in runtime.poll():
            if row.get("work_kind") == kind:
                return row
        time.sleep(0.005)
    raise AssertionError(f"{kind} worker did not finish")


def wait_for_worker_idle(runtime: MainLanguageRuntime) -> None:
    deadline = time.monotonic() + 1.0
    while runtime.worker.items.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.005)
    assert not runtime.worker.items.unfinished_tasks


def prepare_departure(
    web: FakeMainWeb,
) -> tuple[MainLanguageRuntime, ForegroundDialogue, dict]:
    foreground = ForegroundDialogue()
    runtime = MainLanguageRuntime(
        MainWebLLM(),
        ledger=TurnLedger(),
        foreground=foreground,
        web=web,
    )
    accepted, _ = runtime.submit_turn(QUESTION, source="voice", observed_at=BASE)
    assert accepted
    permission = runtime.accept_turn_result(wait_for_envelope(runtime), observed_at=BASE)
    assert permission["reply"] == WEB_PERMISSION_PROMPT
    runtime.playback_event(
        {"utterance_id": permission["utterance_id"], "status": "completed"},
        observed_at=BASE,
    )
    accepted, _ = runtime.submit_turn(
        "ええで",
        source="voice",
        observed_at=BASE + timedelta(seconds=1),
    )
    assert accepted
    departure = runtime.accept_turn_result(
        wait_for_envelope(runtime),
        observed_at=BASE + timedelta(seconds=1),
    )
    assert departure["reply"] == WEB_DEPARTURE
    assert departure["utterance_id"].startswith("web-departure:")
    assert not web.calls
    return runtime, foreground, departure


def test_main_web_availability_check_is_side_effect_free_and_requires_visible_profile(
    tmp_path: Path,
) -> None:
    command = tmp_path / "chrome-web-mcp"
    command.write_text("#!/bin/sh\n", encoding="utf-8")
    command.chmod(0o700)
    chrome = tmp_path / "Google Chrome"
    chrome.write_text("binary", encoding="utf-8")
    chrome.chmod(0o700)
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"show_browser": True}), encoding="utf-8")

    ready = inspect_main_web_availability(
        command=command,
        config=config,
        platform_name="darwin",
        mcp_available=True,
        chrome_paths=(chrome,),
    )

    assert ready.available and ready.reason == "ready"
    config.write_text(json.dumps({"show_browser": False}), encoding="utf-8")
    hidden = inspect_main_web_availability(
        command=command,
        config=config,
        platform_name="darwin",
        mcp_available=True,
        chrome_paths=(chrome,),
    )
    assert not hidden.available and hidden.reason == "visible_browser_disabled"


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_departure_failure_revokes_launch_and_returns_to_learning(status: str) -> None:
    web = FakeMainWeb()
    runtime, foreground, departure = prepare_departure(web)
    try:
        resolved = runtime.playback_event(
            {"utterance_id": departure["utterance_id"], "status": status},
            observed_at=BASE + timedelta(seconds=2),
        )

        assert resolved is not None and resolved["playback_status"] == status
        assert foreground.route == "learning"
        assert runtime.dialogue._pending_web is None  # noqa: SLF001
        assert runtime.playback_event(
            {"utterance_id": departure["utterance_id"], "status": "completed"},
            observed_at=BASE + timedelta(seconds=3),
        ) is None
        assert not web.calls
    finally:
        runtime.close()


def test_completed_departure_opens_once_and_control_result_owns_web() -> None:
    web = FakeMainWeb()
    runtime, foreground, departure = prepare_departure(web)
    try:
        runtime.playback_event(
            {"utterance_id": departure["utterance_id"], "status": "completed"},
            observed_at=BASE + timedelta(seconds=2),
        )
        control = runtime.accept_control_result(
            wait_for_envelope(runtime, kind="playback_control"),
            observed_at=BASE + timedelta(seconds=3),
        )

        assert control["status"] == "awaiting_report"
        assert control["foreground_route"] == "web"
        assert foreground.route == "web"
        assert len(web.calls) == 1
        assert runtime.playback_event(
            {"utterance_id": departure["utterance_id"], "status": "completed"},
            observed_at=BASE + timedelta(seconds=4),
        ) is None
        assert len(web.calls) == 1
    finally:
        runtime.close()


def test_web_failure_returns_to_learning_instead_of_leaving_web_owner() -> None:
    web = FakeMainWeb("captcha")
    runtime, foreground, departure = prepare_departure(web)
    try:
        runtime.playback_event(
            {"utterance_id": departure["utterance_id"], "status": "completed"},
            observed_at=BASE + timedelta(seconds=2),
        )
        control = runtime.accept_control_result(
            wait_for_envelope(runtime, kind="playback_control"),
            observed_at=BASE + timedelta(seconds=3),
        )

        assert control["status"] == "web_unavailable"
        assert control["reply"]
        assert control["turn_id"].startswith("main-web-control:")
        assert control["utterance_id"].startswith("main-web-control-reply:")
        assert control["foreground_route"] == "learning"
        assert foreground.route == "learning"
        assert len(web.calls) == 1
    finally:
        runtime.close()


@pytest.mark.parametrize(
    ("playback_status", "remembered"),
    [("completed", True), ("failed", False)],
)
def test_web_control_reply_enters_history_only_after_completed_playback(
    playback_status: str,
    remembered: bool,
) -> None:
    web = FakeMainWeb("captcha")
    runtime, _, departure = prepare_departure(web)
    try:
        runtime.playback_event(
            {"utterance_id": departure["utterance_id"], "status": "completed"},
            observed_at=BASE + timedelta(seconds=2),
        )
        control = runtime.accept_control_result(
            wait_for_envelope(runtime, kind="playback_control"),
            observed_at=BASE + timedelta(seconds=3),
        )
        reply = control["reply"]
        assert not any(
            item.get("role") == "assistant" and item.get("text") == reply
            for item in runtime.dialogue.history
        )

        runtime.playback_event(
            {"utterance_id": control["utterance_id"], "status": playback_status},
            observed_at=BASE + timedelta(seconds=4),
        )

        assert any(
            item.get("role") == "assistant" and item.get("text") == reply
            for item in runtime.dialogue.history
        ) is remembered
    finally:
        runtime.close()


def test_full_control_queue_abandons_permission_without_opening(monkeypatch) -> None:
    web = FakeMainWeb()
    runtime, foreground, departure = prepare_departure(web)
    try:
        monkeypatch.setattr(runtime.worker, "submit", lambda **kwargs: False)

        runtime.playback_event(
            {"utterance_id": departure["utterance_id"], "status": "completed"},
            observed_at=BASE + timedelta(seconds=2),
        )

        assert foreground.route == "learning"
        assert runtime.dialogue._pending_web is None  # noqa: SLF001
        assert not web.calls
    finally:
        runtime.close()


def test_control_worker_exception_abandons_permission(monkeypatch) -> None:
    web = FakeMainWeb()
    runtime, foreground, departure = prepare_departure(web)
    try:
        def fail_before_consuming(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("control_failed")

        monkeypatch.setattr(runtime.dialogue, "on_speech_playback_result", fail_before_consuming)
        runtime.playback_event(
            {"utterance_id": departure["utterance_id"], "status": "completed"},
            observed_at=BASE + timedelta(seconds=2),
        )
        result = runtime.accept_control_result(
            wait_for_envelope(runtime, kind="playback_control"),
            observed_at=BASE + timedelta(seconds=3),
        )

        assert result["status"] == "failed"
        assert result["control_status"] == "RuntimeError"
        assert foreground.route == "learning"
        assert runtime.dialogue._pending_web is None  # noqa: SLF001
        assert not web.calls
    finally:
        runtime.close()


def test_combat_epoch_prevents_late_completed_departure_from_opening() -> None:
    web = FakeMainWeb()
    runtime, foreground, departure = prepare_departure(web)
    try:
        runtime.mark_dispatched(departure["utterance_id"])
        foreground.suspend_for_combat()
        runtime.interrupt_for_combat()

        resolved = runtime.playback_event(
            {"utterance_id": departure["utterance_id"], "status": "completed"},
            observed_at=BASE + timedelta(seconds=2),
        )

        assert resolved is not None and resolved["playback_status"] == "completed"
        assert runtime.dialogue._pending_web is None  # noqa: SLF001
        assert foreground.route == "none"
        assert not web.calls
        assert runtime.poll() == []
    finally:
        runtime.close()


def test_combat_preserves_an_already_open_research_context() -> None:
    web = FakeMainWeb()
    runtime, foreground, departure = prepare_departure(web)
    try:
        runtime.playback_event(
            {"utterance_id": departure["utterance_id"], "status": "completed"},
            observed_at=BASE + timedelta(seconds=2),
        )
        runtime.accept_control_result(
            wait_for_envelope(runtime, kind="playback_control"),
            observed_at=BASE + timedelta(seconds=3),
        )
        assert foreground.route == "web"

        foreground.suspend_for_combat()
        runtime.interrupt_for_combat()

        assert foreground.route == "web"
        assert runtime.dialogue.research is not None
        runtime.release_after_combat()
        assert not runtime.dialogue.paused
    finally:
        runtime.close()


def test_decline_clears_web_foreground_in_main_runtime() -> None:
    web = FakeMainWeb()
    runtime = MainLanguageRuntime(
        MainWebLLM(),
        ledger=TurnLedger(),
        foreground=ForegroundDialogue(),
        web=web,
    )
    try:
        accepted, _ = runtime.submit_turn(QUESTION, source="voice", observed_at=BASE)
        assert accepted
        permission = runtime.accept_turn_result(wait_for_envelope(runtime), observed_at=BASE)
        assert permission["foreground_route"] == "web"

        accepted, _ = runtime.submit_turn(
            "いや",
            source="voice",
            observed_at=BASE + timedelta(seconds=1),
        )
        assert accepted
        declined = runtime.accept_turn_result(
            wait_for_envelope(runtime),
            observed_at=BASE + timedelta(seconds=1),
        )

        assert declined["reply"] == WEB_DECLINED
        assert declined["foreground_route"] == "none"
        assert runtime.foreground.route == "none"
        assert not web.calls
    finally:
        runtime.close()


@pytest.mark.parametrize(
    ("enabled", "expected_calls"),
    [(False, 0), (True, 1)],
)
def test_optional_web_factory_never_breaks_main_dialogue(
    enabled: bool,
    expected_calls: int,
) -> None:
    calls = 0

    def unavailable_factory():
        nonlocal calls
        calls += 1
        raise RuntimeError("optional_web_unavailable")

    service = DogidoService(
        Settings(
            audio_enabled=False,
            llm_enabled=True,
            memory_enabled=False,
            main_language_web_enabled=enabled,
        ),
        main_language_web_factory=unavailable_factory,
    )
    service.llm = MainWebLLM()  # type: ignore[assignment]
    try:
        created = service.create_session(
            AdapterSessionCreateRequest(
                schema_version="2026-05-24",
                adapter_name="test",
                adapter_version="1",
                game="minecraft",
                player_name="player",
                capabilities=[],
            )
        )
        runtime = service.sessions[created.session_id].language_runtime
        assert runtime is not None
        assert runtime.dialogue.web is None
        assert calls == expected_calls
    finally:
        service.shutdown()


def test_optional_web_close_failure_does_not_break_runtime_shutdown() -> None:
    web = FakeMainWeb()
    web.client = FailingCloseRecorder()  # type: ignore[assignment]
    runtime = MainLanguageRuntime(
        MainWebLLM(),
        ledger=TurnLedger(),
        foreground=ForegroundDialogue(),
        web=web,
    )

    runtime.close()
    runtime.close()


def test_service_integrates_consent_playback_web_hold_and_return_context() -> None:
    web = FakeMainWeb()
    service = DogidoService(
        Settings(
            audio_enabled=False,
            llm_enabled=True,
            memory_enabled=False,
            main_language_web_enabled=True,
            decision_policy="py_trees",
        ),
        main_language_web_factory=lambda: web,
    )
    service.llm = MainWebLLM()  # type: ignore[assignment]
    created = service.create_session(
        AdapterSessionCreateRequest(
            schema_version="2026-05-24",
            adapter_name="test",
            adapter_version="1",
            game="minecraft",
            player_name="player",
            capabilities=[],
        )
    )
    session_id = created.session_id
    try:
        session = service.sessions[session_id]
        runtime = session.language_runtime
        assert runtime is not None and runtime.dialogue.web is web

        first = service.process_event(game_event(1, text=QUESTION), session_id=session_id)
        assert not first.actions
        wait_for_worker_idle(runtime)
        permission_tick = service.process_event(game_event(2), session_id=session_id)
        permission = next(action for action in permission_tick.actions if action.layer == "speech")
        assert permission.text == WEB_PERMISSION_PROMPT

        service._on_audio_playback_event(  # noqa: SLF001
            {
                "session_id": session_id,
                "utterance_id": permission.utterance_id,
                "status": "completed",
                "text": permission.text or "",
            }
        )
        consent_tick = service.process_event(
            game_event(3, text="ええで"),
            session_id=session_id,
        )
        assert not consent_tick.actions
        wait_for_worker_idle(runtime)
        departure_tick = service.process_event(game_event(4), session_id=session_id)
        departure = next(action for action in departure_tick.actions if action.layer == "speech")
        assert departure.text == WEB_DEPARTURE
        assert not web.calls

        service._on_audio_playback_event(  # noqa: SLF001
            {
                "session_id": session_id,
                "utterance_id": departure.utterance_id,
                "status": "completed",
                "text": departure.text or "",
            }
        )
        service.process_event(game_event(5), session_id=session_id)
        wait_for_worker_idle(runtime)
        opened = service.process_event(game_event(6), session_id=session_id)
        assert not opened.actions
        assert len(web.calls) == 1
        assert session.foreground_dialogue.route == "web"
        assert session.machine.state.haiku_interval_pause_started_at is not None

        # 通常会話の5分期限を越えて読んでも、Web用30分期限までは保持する。
        ambient = service.process_event(
            game_event(307, passive=True),
            session_id=session_id,
        )
        assert not [action for action in ambient.actions if action.layer == "speech"]

        returning = service.process_event(
            game_event(308, text="冒険に戻る"),
            session_id=session_id,
        )
        assert not returning.actions
        wait_for_worker_idle(runtime)
        returned = service.process_event(game_event(309), session_id=session_id)
        reply = next(action for action in returned.actions if action.layer == "speech")
        assert reply.text == "よし、冒険にもどろか！"
        assert session.foreground_dialogue.route == "none"
        assert any("朧語" in line and "Webで調べた" in line for line in session.dialogue.digest_lines())
        assert all("google.com" not in line for line in session.dialogue.digest_lines())
        service._on_audio_playback_event(  # noqa: SLF001
            {
                "session_id": session_id,
                "utterance_id": reply.utterance_id,
                "status": "completed",
                "text": reply.text or "",
            }
        )
        service.process_event(game_event(310), session_id=session_id)
        assert sum("冒険に戻る" in line for line in session.dialogue.conversation_lines()) == 1
        assert sum("冒険にもどろか" in line for line in session.dialogue.conversation_lines()) == 1
    finally:
        service.shutdown()
    assert web.client.closed
