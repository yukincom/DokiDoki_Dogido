"""本体会話の所有権・中断・再生完了境界。

実モデル、ブラウザー、音声デバイスは使わない。意味生成は固定値、
危険・時刻・再生結果は型付き入力で検証する。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import threading
import time

from dogido_server.config import Settings
from dogido_server.dialogue.foreground import (
    CASUAL_HAIKU_PREFACE,
    COMBAT_CHAT_ACK,
    COMBAT_CHAT_AFTERMATH,
    COMBAT_FOCUSED_AFTERMATH,
    ForegroundDialogue,
)
from dogido_server.dialogue.main_runtime import MainLanguageRuntime
from dogido_server.language_dialogue.conversation_turns import TurnLedger
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
    TimePhase,
    VisualThreat,
    Weather,
    WorldState,
)
from dogido_server.service import DogidoService
from dogido_server.state_machine import DogidoStateMachine


BASE = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


def event(
    second: int,
    *,
    user_text: str | None = None,
    event_name: EventName = EventName.STATUS_SNAPSHOT,
    threats: bool = False,
    thunder_ms: int | None = None,
    time_phase: TimePhase = TimePhase.DAY,
    passive_mobs: bool = False,
) -> GameEvent:
    visual = (
        [
            VisualThreat(
                type="zombie",
                entity_id="zombie-1",
                distance=4.0,
                direction=Direction(horizontal=HorizontalDirection.FRONT),
            )
        ]
        if threats
        else []
    )
    return GameEvent(
        schema_version="2026-05-24",
        adapter="test",
        observed_at=BASE + timedelta(seconds=second),
        sequence=second,
        event=EventDescriptor(
            name=event_name,
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
            time_phase=time_phase,
            time_of_day=12000 if time_phase == TimePhase.EVENING else 6000,
            weather=Weather.THUNDER if thunder_ms is not None else Weather.CLEAR,
            biome="plains",
            local_light=15,
            sky_visible=True,
            thunder_sound_recent_ms=thunder_ms,
        ),
        visual_threats=visual,
        passive_mobs=(
            [
                PassiveMob(
                    type="cow",
                    distance=5,
                    direction=Direction(horizontal=HorizontalDirection.FRONT),
                )
            ]
            if passive_mobs
            else []
        ),
        combat=CombatState(
            recent_hostile_visual_ms=0 if threats else None,
            hostiles_within_7=1 if threats else 0,
            hostiles_within_10=1 if threats else 0,
            combat_active_hint=threats,
        ),
        meta=MetaState(user_text=user_text),
    )


class LanguageLLM:
    """現在turn IDへだけ根拠を結ぶ、国語質問用の固定モデル。"""

    def __init__(self) -> None:
        self.requests = []

    def generate_structured_json(self, request):  # type: ignore[no-untyped-def]
        self.requests.append(deepcopy(request))
        if request.kind == "language_dialogue_interpretation":
            current = request.details["current"]
            return {
                "dialogue_act": "information_request",
                "topic": "language",
                "relation": "new",
                "question": "漢字の三は何年生？",
                "target": "三",
                "facet": "grade",
                "target_status": "explicit",
                "alternatives": [],
                "evidence": [
                    {"turn_id": current["turn_id"], "quote": current["text"]}
                ],
                "search_terms": ["三"],
                "clarification": "",
            }
        if request.kind in {"haiku_irony", "haiku_scene"}:
            return {"found": False}
        return request.fallback_value

    def generate_leaf_text(self, request):  # type: ignore[no-untyped-def]
        return request.fallback_text


class BlockingLanguageLLM(LanguageLLM):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def generate_structured_json(self, request):  # type: ignore[no-untyped-def]
        self.started.set()
        self.release.wait(timeout=1.0)
        return super().generate_structured_json(request)


def wait_for_turn(runtime: MainLanguageRuntime) -> dict:
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        rows = runtime.poll()
        if rows:
            return rows[0]
        time.sleep(0.005)
    raise AssertionError("dialogue worker did not finish")


def make_service(*, llm_enabled: bool = False) -> tuple[DogidoService, str]:
    service = DogidoService(
        Settings(
            audio_enabled=False,
            llm_enabled=llm_enabled,
            memory_enabled=False,
            decision_policy="py_trees",
        )
    )
    if llm_enabled:
        service.llm = LanguageLLM()  # type: ignore[assignment]
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
    return service, created.session_id


def test_foreground_material_uses_only_completed_turns_and_is_bounded() -> None:
    foreground = ForegroundDialogue()
    foreground.activate("casual", now=BASE, player_text="今日は石炭をたくさん掘った")
    for index in range(1, 5):
        foreground.note_completed_turn(
            f"turn-{index}",
            f"今日は石炭をたくさん掘った話その{index}",
            "それはよう頑張ったな。",
            route="casual",
        )

    material = foreground.casual_haiku_material()

    assert len(str(material["summary"])) <= 80
    assert 1 <= len(material["motifs"]) <= 3
    assert material["source_turn_ids"] == ["turn-2", "turn-3", "turn-4"]
    assert material["attribution"] == "player_dialogue_soft_material"


def test_casual_haiku_material_does_not_reuse_completed_learning_turns() -> None:
    foreground = ForegroundDialogue()
    foreground.activate("learning", now=BASE, player_text="漢字の話")
    foreground.note_completed_turn(
        "learning-1",
        "漢字の三は何年生？",
        "小学一年生やで。",
        route="learning",
    )
    foreground.activate("casual", now=BASE + timedelta(seconds=1), player_text="洞窟の話")

    assert foreground.casual_haiku_material() == {}


def test_suspended_topic_expires_on_ten_accepted_player_turns_or_resumes_explicitly() -> None:
    foreground = ForegroundDialogue()
    foreground.activate("casual", now=BASE, player_text="洞窟の話")
    foreground.note_completed_turn(
        "t1",
        "洞窟の奥まで行った",
        "怖かったやろな。",
        route="casual",
    )
    assert foreground.suspend_for_combat(hold_player_turns=10)

    for index in range(9):
        assert not foreground.consume_suspended_player_turn(
            resumes=False,
            now=BASE + timedelta(seconds=index + 1),
        )
        assert foreground.suspended is not None
    foreground.consume_suspended_player_turn(
        resumes=False,
        now=BASE + timedelta(seconds=10),
    )
    assert foreground.suspended is None

    foreground.activate("learning", now=BASE, player_text="三の学年")
    assert foreground.suspend_for_combat()
    assert foreground.consume_suspended_player_turn(
        resumes=True,
        now=BASE + timedelta(seconds=20),
    )
    assert foreground.route == "learning"
    assert foreground.suspended is None


def test_combat_ack_is_first_then_cooldown_and_resets_next_combat() -> None:
    foreground = ForegroundDialogue()
    foreground.activate("casual", now=BASE, player_text="さっきの話")
    foreground.suspend_for_combat()

    assert foreground.note_combat_chat_attempt(BASE) == COMBAT_CHAT_ACK
    assert foreground.note_combat_chat_attempt(BASE + timedelta(seconds=10)) == ""
    assert foreground.note_combat_chat_attempt(BASE + timedelta(seconds=31)) == COMBAT_CHAT_ACK
    assert foreground.finish_combat() == COMBAT_CHAT_AFTERMATH

    foreground.activate("casual", now=BASE + timedelta(seconds=40), player_text="次の話")
    foreground.suspend_for_combat()
    assert foreground.note_combat_chat_attempt(BASE + timedelta(seconds=41)) == COMBAT_CHAT_ACK


def test_combat_aftermath_invites_focused_player_back_only_when_topic_was_suspended() -> None:
    foreground = ForegroundDialogue()
    foreground.activate("casual", now=BASE, player_text="洞窟の話")
    foreground.suspend_for_combat()

    assert foreground.finish_combat() == COMBAT_FOCUSED_AFTERMATH

    no_topic = ForegroundDialogue()
    no_topic.suspend_for_combat()
    assert no_topic.finish_combat() == ""

    auto_haiku_topic = ForegroundDialogue()
    auto_haiku_topic.activate("casual", now=BASE, player_text="石炭の話")
    auto_haiku_topic.suspend("auto_haiku")
    assert not auto_haiku_topic.suspend_for_combat()
    assert auto_haiku_topic.finish_combat() == ""
    assert auto_haiku_topic.suspended is not None
    assert auto_haiku_topic.suspended.reason == "auto_haiku"


def test_casual_allows_due_haiku_learning_freezes_interval_and_ambient() -> None:
    settings = Settings(
        llm_enabled=False,
        haiku_interval_ms=600_000,
        haiku_quiet_time_ms=30_000,
    )
    machine = DogidoStateMachine(settings)
    foreground = ForegroundDialogue()
    machine.foreground_dialogue_provider = foreground.snapshot
    due = BASE + timedelta(minutes=10)
    machine.state.last_haiku_emitted_at = BASE
    machine.state.last_non_silent_at = due
    foreground.activate("casual", now=due, player_text="ずっと話してる")

    assert machine._should_emit_haiku(event(600), due)  # noqa: SLF001
    foreground.activate("learning", now=due, player_text="学年を教えて")
    assert not machine._should_emit_haiku(event(600), due)  # noqa: SLF001
    assert foreground.blocks_ambient()

    # 5分経過時点で学習を始め、さらに5分学習しても発句時計は5分のまま。
    machine.state.last_haiku_emitted_at = BASE + timedelta(minutes=5)
    machine._sync_haiku_interval_pause(due)  # noqa: SLF001
    foreground.clear()
    resumed = due + timedelta(minutes=5)
    machine._sync_haiku_interval_pause(resumed)  # noqa: SLF001
    assert machine.state.last_haiku_emitted_at == BASE + timedelta(minutes=10)
    assert not machine._should_emit_haiku(event(900), resumed)  # noqa: SLF001


def test_ongoing_combat_blocks_due_haiku_during_a_threat_free_lull() -> None:
    machine = DogidoStateMachine(
        Settings(llm_enabled=False, haiku_interval_ms=600_000)
    )
    foreground = ForegroundDialogue(combat_active=True)
    machine.foreground_dialogue_provider = foreground.snapshot
    due = BASE + timedelta(minutes=10)
    machine.state.last_haiku_emitted_at = BASE
    machine.state.last_non_silent_at = BASE

    assert not machine._should_emit_haiku(event(600), due)  # noqa: SLF001


def test_casual_haiku_keeps_conversation_preface_when_llm_is_disabled() -> None:
    machine = DogidoStateMachine(
        Settings(llm_enabled=False, haiku_interval_ms=600_000)
    )
    foreground = ForegroundDialogue()
    foreground.activate("casual", now=BASE, player_text="洞窟の話")
    machine.foreground_dialogue_provider = foreground.snapshot
    machine.state.last_haiku_emitted_at = BASE
    machine.state.last_non_silent_at = BASE + timedelta(minutes=10)

    result = machine.process(event(600))

    assert [action.text for action in result.actions if action.text] == [
        CASUAL_HAIKU_PREFACE
    ]
    assert machine.state.pending_haiku_after_preface


def test_due_haiku_follows_current_reply_at_repeated_casual_utterance_boundary() -> None:
    service = DogidoService(
        Settings(
            audio_enabled=False,
            llm_enabled=False,
            memory_enabled=False,
            decision_policy="py_trees",
            haiku_interval_ms=600_000,
        )
    )
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
    try:
        session = service.sessions[created.session_id]
        session.foreground_dialogue.activate("casual", now=BASE, player_text="最初の話")
        session.machine.state.last_haiku_emitted_at = BASE
        session.machine.state.last_non_silent_at = BASE + timedelta(minutes=10)

        interrupted = service.process_event(
            event(600, user_text="まだ話の続きやで"),
            session_id=created.session_id,
        )

        spoken = [action.text for action in interrupted.actions if action.text]
        assert len(spoken) == 2
        assert spoken[-1] == CASUAL_HAIKU_PREFACE
        assert interrupted.actions[-1].route_owner == "auto_haiku_preface"
        assert session.pending_player_text is None
        assert session.foreground_dialogue.route == "haiku_workshop"
    finally:
        service.shutdown()


def test_conversation_haiku_preface_and_sources_are_attributed_soft_material() -> None:
    foreground = ForegroundDialogue()
    foreground.activate("casual", now=BASE, player_text="石炭の話")
    foreground.note_completed_turn(
        "t1",
        "石炭がたくさん取れた",
        "ええ採掘やったな。",
        route="casual",
    )
    machine = DogidoStateMachine(
        Settings(llm_enabled=True, audio_enabled=False),
        llm=LanguageLLM(),
    )
    machine.foreground_dialogue_provider = foreground.snapshot

    spoken = machine._begin_prefaced_haiku(event(1), BASE)  # noqa: SLF001

    assert spoken == CASUAL_HAIKU_PREFACE
    material = machine._pending_haiku_materials["player_dialogue_material"]  # noqa: SLF001
    assert material["constraint"] == "soft"
    assert material["source_turn_ids"] == ["t1"]
    dialogue_atoms = [
        atom
        for atom in machine._pending_haiku_source_atoms  # noqa: SLF001
        if atom.observation_role == "player_dialogue_attributed"
    ]
    assert dialogue_atoms
    assert all(atom.source_ref == "dialogue:t1" for atom in dialogue_atoms)
    assert all(atom.kind == "dialogue_material" for atom in dialogue_atoms)
    assert all(atom.claim_scopes == ("player_reported_context",) for atom in dialogue_atoms)


def test_casual_haiku_uses_conversation_preface_even_before_first_reply_completed() -> None:
    foreground = ForegroundDialogue()
    foreground.activate("casual", now=BASE, player_text="まだ最初の返事待ち")
    machine = DogidoStateMachine(
        Settings(llm_enabled=True, audio_enabled=False),
        llm=LanguageLLM(),
    )
    machine.foreground_dialogue_provider = foreground.snapshot

    spoken = machine._begin_prefaced_haiku(event(1), BASE)  # noqa: SLF001

    assert spoken == CASUAL_HAIKU_PREFACE
    assert machine._pending_conversation_haiku_material is None  # noqa: SLF001


def test_main_runtime_adds_assistant_history_only_after_completed_playback() -> None:
    foreground = ForegroundDialogue()
    ledger = TurnLedger()
    runtime = MainLanguageRuntime(LanguageLLM(), ledger=ledger, foreground=foreground)
    try:
        accepted, turn_id = runtime.submit_turn(
            "漢字の三は何年生？",
            source="voice",
            observed_at=BASE,
        )
        assert accepted
        row = runtime.accept_turn_result(wait_for_turn(runtime), observed_at=BASE)
        utterance_id = row["utterance_id"]
        runtime.playback_event(
            {"utterance_id": utterance_id, "status": "queued"},
            observed_at=BASE,
        )
        runtime.playback_event(
            {"utterance_id": utterance_id, "status": "started"},
            observed_at=BASE,
        )
        assert not foreground.completed_turns
        assert not any(item.get("role") == "assistant" for item in runtime.dialogue.history)

        runtime.playback_event(
            {"utterance_id": utterance_id, "status": "completed"},
            observed_at=BASE,
        )

        assert foreground.completed_turns[-1].turn_id == turn_id
        assert any(item.get("role") == "assistant" for item in runtime.dialogue.history)
        assert ledger.snapshot()[-1]["playback_status"] == "completed"
    finally:
        runtime.close()


def test_main_runtime_failed_playback_never_becomes_dialogue_material() -> None:
    foreground = ForegroundDialogue()
    ledger = TurnLedger()
    runtime = MainLanguageRuntime(LanguageLLM(), ledger=ledger, foreground=foreground)
    try:
        accepted, _ = runtime.submit_turn(
            "漢字の三は何年生？",
            source="voice",
            observed_at=BASE,
        )
        assert accepted
        row = runtime.accept_turn_result(wait_for_turn(runtime), observed_at=BASE)
        runtime.playback_event(
            {
                "utterance_id": row["utterance_id"],
                "status": "failed",
                "resolution": "process_exit",
            },
            observed_at=BASE,
        )

        assert not foreground.completed_turns
        assert not any(item.get("role") == "assistant" for item in runtime.dialogue.history)
        assert ledger.snapshot()[-1]["playback_status"] == "failed"
    finally:
        runtime.close()


def test_main_runtime_external_learning_turn_waits_for_completed_reply() -> None:
    foreground = ForegroundDialogue()
    ledger = TurnLedger()
    runtime = MainLanguageRuntime(LanguageLLM(), ledger=ledger, foreground=foreground)
    try:
        turn_id = "main-chat:local-knowledge"
        utterance_id = "local-knowledge:reply"
        ledger.begin(
            turn_id,
            epoch=runtime.epoch,
            raw_text="枕詞って何？",
            semantic_text="枕詞って何？",
        )
        ledger.routed(turn_id, route="learning", status="knowledge_db")
        ledger.select_reply(
            turn_id,
            reply="特定の言葉にかかる決まった表現やで。",
            utterance_id=utterance_id,
        )

        assert runtime.observe_external_user_turn(
            turn_id,
            "枕詞って何？",
            source="voice",
        )
        assert not runtime.observe_external_user_turn(
            turn_id,
            "枕詞って何？",
            source="voice",
        )
        assert [row["role"] for row in runtime.dialogue.history] == ["user"]

        runtime.playback_event(
            {"utterance_id": utterance_id, "status": "completed"},
            observed_at=BASE,
        )

        assert [row["role"] for row in runtime.dialogue.history] == ["user", "assistant"]
        assert runtime.dialogue.history[-1]["turn_id"] == f"{turn_id}:reply"
        assert foreground.completed_turns[-1].route == "learning"
    finally:
        runtime.close()


def test_replaced_undelivered_result_is_cancelled_not_remembered() -> None:
    foreground = ForegroundDialogue()
    ledger = TurnLedger()
    runtime = MainLanguageRuntime(LanguageLLM(), ledger=ledger, foreground=foreground)
    try:
        accepted, _ = runtime.submit_turn(
            "漢字の三は何年生？",
            source="voice",
            observed_at=BASE,
        )
        assert accepted
        row = runtime.accept_turn_result(wait_for_turn(runtime), observed_at=BASE)

        runtime.discard_undelivered_result(
            row,
            resolution="result_queue_replaced",
        )

        assert ledger.snapshot()[-1]["playback_status"] == "cancelled"
        assert ledger.snapshot()[-1]["resolution"] == "result_queue_replaced"
        assert not foreground.completed_turns
        assert not any(item.get("role") == "assistant" for item in runtime.dialogue.history)
    finally:
        runtime.close()


def test_combat_interrupt_discards_inflight_language_result_and_closes_ledger_turn() -> None:
    llm = BlockingLanguageLLM()
    foreground = ForegroundDialogue()
    ledger = TurnLedger()
    runtime = MainLanguageRuntime(llm, ledger=ledger, foreground=foreground)
    try:
        accepted, _ = runtime.submit_turn(
            "漢字の三は何年生？",
            source="voice",
            observed_at=BASE,
        )
        assert accepted and llm.started.wait(timeout=1.0)

        runtime.interrupt_for_combat()
        llm.release.set()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and runtime.worker.items.unfinished_tasks:
            time.sleep(0.005)

        assert runtime.poll() == []
        assert ledger.snapshot()[-1]["playback_status"] == "cancelled"
        assert ledger.snapshot()[-1]["resolution"] == "combat"
        assert not foreground.completed_turns
    finally:
        llm.release.set()
        runtime.close()


def test_service_suspends_casual_during_combat_and_varies_aftermath() -> None:
    service, session_id = make_service()
    try:
        service.process_event(event(1, user_text="今日は絶好調やで"), session_id=session_id)
        session = service.sessions[session_id]
        assert session.foreground_dialogue.route == "casual"

        service.process_event(event(2, threats=True), session_id=session_id)
        assert session.foreground_dialogue.route == "none"
        assert session.foreground_dialogue.suspended is not None
        assert session.foreground_dialogue.suspended.route == "casual"

        during = service.process_event(
            event(3, user_text="さっきの話の続きやけど", threats=True),
            session_id=session_id,
        )
        assert any(action.route_owner == "combat_chat_ack" for action in during.actions)
        assert session.foreground_dialogue.combat_chat_attempted

        ended = service.process_event(
            event(4, event_name=EventName.COMBAT_ENDED),
            session_id=session_id,
        )
        relief = next(action for action in ended.actions if action.cue_id == "aftermath_relief")
        assert COMBAT_CHAT_AFTERMATH in (relief.text or "")
        assert relief.route_owner == "combat_chat_aftermath"
        assert session.foreground_dialogue.suspended is not None
    finally:
        service.shutdown()


def test_service_focused_combat_aftermath_invites_suspended_topic_back() -> None:
    service, session_id = make_service()
    try:
        service.process_event(event(1, user_text="洞窟の話しよ"), session_id=session_id)
        service.process_event(event(2, threats=True), session_id=session_id)

        ended = service.process_event(
            event(3, event_name=EventName.COMBAT_ENDED),
            session_id=session_id,
        )

        relief = next(action for action in ended.actions if action.cue_id == "aftermath_relief")
        assert COMBAT_FOCUSED_AFTERMATH in (relief.text or "")
        assert COMBAT_CHAT_AFTERMATH not in (relief.text or "")
    finally:
        service.shutdown()


def test_combat_ownership_persists_until_explicit_combat_end() -> None:
    service, session_id = make_service()
    try:
        service.process_event(event(1, user_text="洞窟の話しよ"), session_id=session_id)
        service.process_event(event(2, threats=True), session_id=session_id)
        lull = service.process_event(
            event(40, user_text="まだ話してもええ？"),
            session_id=session_id,
        )

        assert any(action.route_owner == "combat_chat_ack" for action in lull.actions)
        assert service.sessions[session_id].foreground_dialogue.route == "none"
    finally:
        service.shutdown()


def test_suspended_learning_does_not_steal_unrelated_casual_turns_and_expires_at_ten() -> None:
    service, session_id = make_service(llm_enabled=True)
    try:
        session = service.sessions[session_id]
        session.foreground_dialogue.activate(
            "learning",
            now=BASE,
            player_text="漢字の話",
        )
        session.foreground_dialogue.suspend_for_combat(hold_player_turns=10)
        session.foreground_dialogue.finish_combat()

        for index in range(1, 11):
            processed = service.process_event(
                event(index, user_text=f"今日は元気やでその{index}"),
                session_id=session_id,
            )
            assert any(action.layer == "speech" for action in processed.actions)
            assert all(
                row["route"] == "casual"
                for row in session.dialogue_turns.snapshot()
            )
            if index < 10:
                assert session.foreground_dialogue.suspended is not None

        assert session.foreground_dialogue.suspended is None
        assert session.foreground_dialogue.route == "casual"
    finally:
        service.shutdown()


def test_explicit_resume_restores_suspended_learning_before_async_submission() -> None:
    service, session_id = make_service(llm_enabled=True)
    try:
        session = service.sessions[session_id]
        session.foreground_dialogue.activate(
            "learning",
            now=BASE,
            player_text="漢字の話",
        )
        session.foreground_dialogue.suspend_for_combat()
        session.foreground_dialogue.finish_combat()

        processed = service.process_event(
            event(1, user_text="さっきの話の続き、漢字の三は何年生？"),
            session_id=session_id,
        )

        assert not any(action.layer == "speech" for action in processed.actions)
        assert session.foreground_dialogue.route == "learning"
        assert session.foreground_dialogue.suspended is None
        assert len(session.dialogue_turns.snapshot()) == 1
    finally:
        service.shutdown()


def test_main_language_entry_is_narrow_and_does_not_steal_casual_questions() -> None:
    service, session_id = make_service(llm_enabled=True)
    try:
        assert service._looks_like_main_language_request("金床ってどういう意味？")  # noqa: SLF001
        assert service._looks_like_main_language_request("漢字の話をしよう")  # noqa: SLF001
        assert not service._looks_like_main_language_request(  # noqa: SLF001
            "今日は意味ないことばっかり言うてるな"
        )
        assert not service._looks_like_main_language_request("元気なん？")  # noqa: SLF001

        casual = service.process_event(
            event(1, user_text="元気なん？"),
            session_id=session_id,
        )
        session = service.sessions[session_id]
        assert any(action.route_owner == "player_chat" for action in casual.actions)
        assert session.foreground_dialogue.route == "casual"
    finally:
        service.shutdown()


def test_thunder_preempts_current_input_and_requeues_it_without_clearing_topic() -> None:
    service, session_id = make_service()
    try:
        session = service.sessions[session_id]
        session.foreground_dialogue.activate("casual", now=BASE, player_text="天気の話")
        processed = service.process_event(
            event(1, user_text="まだ雷すごいな", thunder_ms=200),
            session_id=session_id,
        )

        assert any(action.defer_player_input for action in processed.actions)
        assert any(action.interrupt for action in processed.actions)
        assert session.pending_player_text == "まだ雷すごいな"
        assert session.foreground_dialogue.route == "casual"
        assert session.foreground_dialogue.last_interrupt_reason == "thunder"
    finally:
        service.shutdown()


def test_requeued_thunder_turn_consumes_suspended_budget_only_after_reply() -> None:
    service, session_id = make_service()
    try:
        session = service.sessions[session_id]
        session.foreground_dialogue.activate("learning", now=BASE, player_text="漢字の話")
        session.foreground_dialogue.suspend_for_combat(hold_player_turns=10)
        session.foreground_dialogue.finish_combat()

        interrupted = service.process_event(
            event(1, user_text="今日は元気やで", thunder_ms=200),
            session_id=session_id,
        )
        assert any(action.defer_player_input for action in interrupted.actions)
        assert session.foreground_dialogue.suspended is not None
        assert session.foreground_dialogue.suspended.remaining_player_turns == 10

        answered = service.process_event(event(2), session_id=session_id)
        assert any(action.route_owner == "player_chat" for action in answered.actions)
        assert session.foreground_dialogue.suspended is not None
        assert session.foreground_dialogue.suspended.remaining_player_turns == 9
    finally:
        service.shutdown()


def test_evening_interrupt_requeues_current_casual_input_once() -> None:
    service, session_id = make_service()
    try:
        service.process_event(event(1, user_text="洞窟の話しよ"), session_id=session_id)
        interrupted = service.process_event(
            event(2, user_text="続きなんやけど", time_phase=TimePhase.EVENING),
            session_id=session_id,
        )
        session = service.sessions[session_id]

        warning = next(action for action in interrupted.actions if action.defer_player_input)
        assert warning.interrupt
        assert session.pending_player_text == "続きなんやけど"
        assert session.foreground_dialogue.route == "casual"
        assert session.foreground_dialogue.last_interrupt_reason == "dusk"
    finally:
        service.shutdown()


def test_foreground_does_not_suppress_hostile_warning() -> None:
    service, session_id = make_service()
    try:
        service.process_event(event(1, user_text="洞窟の話しよ"), session_id=session_id)

        danger = service.process_event(event(2, threats=True), session_id=session_id)

        assert any(
            action.layer in {"panic_cue", "callout"} or action.interrupt
            for action in danger.actions
        )
    finally:
        service.shutdown()


def test_local_knowledge_reply_owns_learning_route_and_has_session_identity() -> None:
    service, session_id = make_service()
    try:
        processed = service.process_event(
            event(1, user_text="枕詞って何？"),
            session_id=session_id,
        )
        session = service.sessions[session_id]
        reply = next(action for action in processed.actions if action.layer == "speech")

        assert session.foreground_dialogue.route == "learning"
        assert reply.route_owner == "knowledge_dialogue"
        assert reply.utterance_id
        assert reply.playback_session_id == session_id
        assert session.dialogue_turns.snapshot()[-1]["route"] == "learning"
        assert session.machine.state.haiku_interval_pause_started_at == event(1).observed_at
    finally:
        service.shutdown()


def test_completed_local_knowledge_exchange_grounds_async_learning_followup() -> None:
    service, session_id = make_service(llm_enabled=True)
    try:
        first = service.process_event(
            event(1, user_text="枕詞って何？"),
            session_id=session_id,
        )
        session = service.sessions[session_id]
        runtime = session.language_runtime
        assert runtime is not None
        reply = next(
            action
            for action in first.actions
            if action.layer == "speech" and action.route_owner == "knowledge_dialogue"
        )
        assert [row["role"] for row in runtime.dialogue.history] == ["user"]

        service._on_audio_playback_event(  # noqa: SLF001
            {
                "session_id": session_id,
                "utterance_id": reply.utterance_id,
                "turn_id": reply.conversation_turn_id,
                "route_owner": reply.route_owner,
                "status": "completed",
                "resolution": "",
                "text": reply.text or "",
            }
        )
        service.process_event(event(2), session_id=session_id)

        completed_history = list(runtime.dialogue.history)
        assert [row["role"] for row in completed_history] == ["user", "assistant"]
        assert completed_history[0]["text"] == "枕詞って何？"
        assert completed_history[1]["text"] == reply.text

        followup = service.process_event(
            event(3, user_text="それはどんな時に使うの？"),
            session_id=session_id,
        )
        assert not any(action.layer == "speech" for action in followup.actions)

        llm = service.llm
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and not llm.requests:
            time.sleep(0.005)
        request = next(
            row for row in llm.requests if row.kind == "language_dialogue_interpretation"
        )
        history = request.details["history"]
        assert [row["turn_id"] for row in history] == [
            reply.conversation_turn_id,
            f"{reply.conversation_turn_id}:reply",
        ]
        assert history[0]["text"] == "枕詞って何？"
        assert history[1]["text"] == reply.text
    finally:
        service.shutdown()


def test_implicit_session_actions_keep_playback_and_display_session_identity() -> None:
    service = DogidoService(
        Settings(
            audio_enabled=False,
            llm_enabled=False,
            memory_enabled=False,
            decision_policy="py_trees",
        )
    )
    try:
        processed = service.process_event(event(1, user_text="今日は元気やで"))
        reply = next(action for action in processed.actions if action.layer == "speech")

        assert reply.playback_session_id == processed.response.session_id
        service.dispatch_actions(processed.actions)
        snapshot = service.display_snapshot(session_id=processed.response.session_id)
        assert [row["text"] for row in snapshot["utterances"]] == [reply.text]
    finally:
        service.shutdown()


def test_main_service_language_result_waits_for_safe_tick_and_completion() -> None:
    service, session_id = make_service(llm_enabled=True)
    try:
        first = service.process_event(
            event(1, user_text="漢字の三は何年生？"),
            session_id=session_id,
        )
        assert not any(action.layer == "speech" for action in first.actions)
        session = service.sessions[session_id]
        assert session.foreground_dialogue.route == "learning"

        delivered = None
        second = 2
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            candidate = service.process_event(event(second), session_id=session_id)
            speech = [action for action in candidate.actions if action.layer == "speech"]
            if speech:
                delivered = speech[0]
                break
            second += 1
            time.sleep(0.005)
        assert delivered is not None
        assert "小学1年生" in (delivered.text or "")
        assert delivered.conversation_turn_id
        assert delivered.utterance_id
        assert not any(line.startswith("ドギド:") for line in session.dialogue.conversation_lines())

        for status in ("queued", "started", "completed"):
            service._on_audio_playback_event(  # noqa: SLF001
                {
                    "session_id": session_id,
                    "utterance_id": delivered.utterance_id,
                    "turn_id": delivered.conversation_turn_id,
                    "route_owner": delivered.route_owner,
                    "status": status,
                    "resolution": "",
                    "text": delivered.text or "",
                }
            )
        service.process_event(event(30), session_id=session_id)

        assert any(line.startswith("ドギド:") for line in session.dialogue.conversation_lines())
        assert session.foreground_dialogue.completed_turns
    finally:
        service.shutdown()


def test_full_playback_event_queue_keeps_incoming_terminal_result() -> None:
    service, session_id = make_service()
    try:
        session = service.sessions[session_id]
        for index in range(session.playback_events.maxlen or 0):
            service._on_audio_playback_event(  # noqa: SLF001
                {
                    "session_id": session_id,
                    "utterance_id": f"queued-{index}",
                    "status": "queued",
                }
            )

        service._on_audio_playback_event(  # noqa: SLF001
            {
                "session_id": session_id,
                "utterance_id": "terminal",
                "status": "completed",
                "text": "最後まで話したで。",
            }
        )
        service._on_audio_playback_event(  # noqa: SLF001
            {
                "session_id": session_id,
                "utterance_id": "terminal",
                "status": "completed",
                "text": "最後まで話したで。",
            }
        )

        assert len(session.playback_events) == session.playback_events.maxlen
        assert session.playback_events[-1]["utterance_id"] == "terminal"
        service.process_event(event(1), session_id=session_id)
        assert sum(
            "最後まで話したで。" in line
            for line in session.dialogue.conversation_lines()
        ) == 1
    finally:
        service.shutdown()


def test_completed_casual_turn_becomes_haiku_material_without_language_runtime() -> None:
    service, session_id = make_service(llm_enabled=False)
    try:
        processed = service.process_event(
            event(1, user_text="今日は石炭を掘ったで"),
            session_id=session_id,
        )
        session = service.sessions[session_id]
        assert session.language_runtime is None
        reply = next(
            action
            for action in processed.actions
            if action.layer == "speech" and action.route_owner == "player_chat"
        )

        service._on_audio_playback_event(  # noqa: SLF001
            {
                "session_id": session_id,
                "utterance_id": reply.utterance_id,
                "turn_id": reply.conversation_turn_id,
                "route_owner": reply.route_owner,
                "status": "completed",
                "resolution": "",
                "text": reply.text or "",
            }
        )
        service.process_event(event(2), session_id=session_id)

        material = session.foreground_dialogue.casual_haiku_material()
        assert "石炭" in str(material["summary"])
        assert material["source_turn_ids"] == [reply.conversation_turn_id]
    finally:
        service.shutdown()


def test_ambient_mob_comment_is_blocked_by_foreground_route() -> None:
    machine = DogidoStateMachine(Settings(llm_enabled=False))
    foreground = ForegroundDialogue()
    machine.foreground_dialogue_provider = foreground.snapshot
    ambient = event(1, passive_mobs=True)

    foreground.activate("casual", now=BASE, player_text="牛の話")
    assert not machine._should_emit_ambient_mob_comment(  # noqa: SLF001
        ambient,
        ambient.observed_at,
    )
    foreground.activate("learning", now=BASE, player_text="牛という字")
    assert not machine._should_emit_ambient_mob_comment(  # noqa: SLF001
        ambient,
        ambient.observed_at,
    )
