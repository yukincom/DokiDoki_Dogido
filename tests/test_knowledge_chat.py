"""知識DBとplayer chat/workshopの安全な接続。"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from dogido_server.config import Settings
from dogido_server.haiku.workshop import is_open
from dogido_server.knowledge_query import (
    ExplicitKnowledgeQuery,
    KnowledgeFact,
    KnowledgeLookupResult,
    KnowledgeSource,
    LocalKnowledgeProvider,
)
from dogido_server.memory_types import HaikuEmission
from dogido_server.models import (
    AdapterSessionCreateRequest,
    Certainty,
    CombatState,
    EventDescriptor,
    EventName,
    GameEvent,
    MetaState,
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
from dogido_server.state_machine.fallback_catalog import fallback_text


BASE = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _event(
    *,
    sequence: int,
    user_text: str | None = None,
    with_threat: bool = False,
    threat_distance: float = 4.0,
    combat_active_hint: bool = False,
    event_name: EventName = EventName.STATUS_SNAPSHOT,
    danger_darkness_score: float = 0.0,
    local_light: int = 15,
    time_phase: TimePhase = TimePhase.DAY,
    dimension: str = "minecraft:overworld",
    ominous_sound_kind: str | None = None,
    ominous_sound_recent_ms: int | None = None,
    at: datetime | None = None,
) -> GameEvent:
    threats = (
        [VisualThreat(type="pillager", entity_id="p1", distance=threat_distance)]
        if with_threat
        else []
    )
    return GameEvent(
        schema_version="2026-05-24",
        adapter="test",
        observed_at=at or BASE + timedelta(seconds=sequence),
        sequence=sequence,
        event=EventDescriptor(
            name=event_name,
            source_kind=SourceKind.SYSTEM,
            priority_hint=PriorityHint.BACKGROUND,
            certainty=Certainty.HIGH,
        ),
        player=PlayerState(
            name="p",
            position=Position(x=0, y=64, z=0),
            dimension=dimension,
            health=20,
            hunger=20,
        ),
        world=WorldState(
            time_phase=time_phase,
            weather=Weather.CLEAR,
            biome="plains",
            local_light=local_light,
            sky_visible=True,
            danger_darkness_score=danger_darkness_score,
            ominous_sound_kind=ominous_sound_kind,
            ominous_sound_recent_ms=ominous_sound_recent_ms,
        ),
        visual_threats=threats,
        combat=CombatState(combat_active_hint=combat_active_hint),
        meta=MetaState(user_text=user_text),
    )


def _source(index: int) -> KnowledgeSource:
    return KnowledgeSource(
        source_id=f"src.mext.test.{index}",
        title_ja=f"公式資料{index}",
        citation_label_ja="文部科学省",
        url=f"https://www.mext.go.jp/test/{index}",
        source_kind="organization_authored_or_issued",
    )


def _fact(index: int) -> KnowledgeFact:
    return KnowledgeFact(
        record_id=f"record.{index}",
        dataset_id="test",
        title_ja=f"項目{index}",
        text_ja=f"確認済み事実{index}です。",
        claim_status="source_stated",
        sources=(_source(index),),
    )


class RecordingKnowledgeProvider:
    def __init__(self, *, fact_count: int = 1, error: Exception | None = None) -> None:
        self.fact_count = fact_count
        self.error = error
        self.calls: list[tuple[object, int]] = []
        self.delegate = LocalKnowledgeProvider()

    def lookup(self, query, *, limit: int = 3):  # type: ignore[no-untyped-def]
        self.calls.append((query, limit))
        if self.error is not None:
            raise self.error
        return self.delegate.lookup(query, limit=limit)


class TransformingKnowledgeProvider:
    def __init__(self, transform) -> None:  # type: ignore[no-untyped-def]
        self.transform = transform
        self.calls = 0

    def lookup(self, query, *, limit: int = 3):  # type: ignore[no-untyped-def]
        self.calls += 1
        return self.transform(query)


class NeverKnowledgeProvider:
    def lookup(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("この入力では知識検索してはいけない")


class NeverLLM:
    def generate_leaf_text(self, request):  # type: ignore[no-untyped-def]
        raise AssertionError("知識本文をLLMへ渡してはいけない")

    def generate_structured_json(self, request):  # type: ignore[no-untyped-def]
        raise AssertionError("知識質問でstructured LLMを呼んではいけない")


class NeverAssistClassifierLLM:
    def route_enabled(self, route: str) -> bool:
        return True

    def generate_structured_json(self, request):  # type: ignore[no-untyped-def]
        raise AssertionError("知識質問を剣支援の意図分類へ渡してはいけない")


class KnowledgePlayerChatTests(unittest.TestCase):
    def test_makurakotoba_answer_keeps_dogido_voice_and_points_to_books(self) -> None:
        machine = DogidoStateMachine(
            Settings(decision_policy="py_trees", llm_enabled=False, audio_enabled=False)
        )
        machine.knowledge_provider = LocalKnowledgeProvider()

        result = machine.process(_event(sequence=1, user_text="枕詞って何？"))
        speeches = [
            action
            for action in result.actions
            if action.layer == "speech" and action.text
        ]

        self.assertEqual(1, len(speeches))
        action = speeches[0]
        self.assertEqual(
            "枕詞かいな。枕詞っちゅうのは和歌で特定の言葉につながる定型的な言葉のことやで。"
            "教科書や資料集に色々書いてあるで！気になったらみてみよか！",
            action.text,
        )
        self.assertEqual(4, len(action.speech_segments))
        self.assertEqual(["文部科学省"], [item.citation_label_ja for item in action.references])

    def test_explicit_question_uses_one_lookup_and_no_llm(self) -> None:
        machine = DogidoStateMachine(
            Settings(decision_policy="py_trees", llm_enabled=True, audio_enabled=False),
            llm=NeverLLM(),  # type: ignore[arg-type]
        )
        provider = RecordingKnowledgeProvider()
        machine.knowledge_provider = provider

        result = machine.process(_event(sequence=1, user_text="川柳の決まりを教えて"))
        speech_actions = [
            action
            for action in result.actions
            if action.layer == "speech" and action.text
        ]
        self.assertEqual(1, len(speech_actions))
        action = speech_actions[0]
        self.assertEqual(1, len(provider.calls))
        query, limit = provider.calls[0]
        self.assertEqual("川柳", query.subject)
        self.assertEqual(3, limit)
        self.assertIn("五・七・五の十七拍", action.text)
        self.assertIn("資料を基にした注意点として", action.text)
        self.assertLessEqual(len(action.text), 420)
        self.assertEqual(action.text, "".join(action.speech_segments))
        self.assertGreater(len(action.speech_segments), 1)
        self.assertEqual(650, action.speech_segment_pause_ms)
        self.assertEqual("foreground", action.queue_priority)
        self.assertNotIn("参照資料は", action.text)
        self.assertTrue(action.references)
        self.assertTrue(
            all(reference.citation_label_ja not in action.text for reference in action.references)
        )
        self.assertTrue(
            all(reference.url.startswith("https://") for reference in action.references)
        )

    def test_numeric_kanji_transcription_is_clarified_without_llm(self) -> None:
        for index, (text, digit) in enumerate(
            (
                ("漢字の3は何年生で習うの？", "3"),
                ("じゃあ数字の4は漢字で何年生で習うのかな", "4"),
                ("数字の4の漢字って", "4"),
            ),
            start=1,
        ):
            with self.subTest(text=text):
                machine = DogidoStateMachine(
                    Settings(decision_policy="py_trees", llm_enabled=True, audio_enabled=False),
                    llm=NeverLLM(),  # type: ignore[arg-type]
                )
                machine.knowledge_provider = LocalKnowledgeProvider()
                result = machine.process(_event(sequence=index, user_text=text))
                speeches = [
                    action
                    for action in result.actions
                    if action.layer == "speech" and action.text
                ]

                self.assertEqual(1, len(speeches))
                self.assertEqual(
                    f"「{digit}」だけやと、どの漢字か決められへんわ。"
                    "「一二三の一」みたいに言うてみてな。",
                    speeches[0].text,
                )
                self.assertEqual((), speeches[0].references)

    def test_service_marks_the_input_on_the_player_reply_for_display_copy(self) -> None:
        service = DogidoService(
            Settings(llm_enabled=False, audio_enabled=False, memory_enabled=False)
        )
        created = service.create_session(
            AdapterSessionCreateRequest(
                schema_version="2026-05-24",
                adapter_name="test",
                adapter_version="0",
                game="minecraft",
                player_name="p",
                capabilities=[],
            )
        )

        result = service.process_event(
            _event(sequence=1, user_text="枕詞って何？"),
            session_id=created.session_id,
        )
        replies = [
            action
            for action in result.actions
            if action.layer == "speech" and action.text
        ]

        self.assertEqual(1, len(replies))
        self.assertEqual("枕詞って何？", replies[0].display_player_input_text)

    def test_voice_reply_display_keeps_the_original_stt_text(self) -> None:
        service = DogidoService(
            Settings(llm_enabled=False, audio_enabled=False, memory_enabled=False)
        )
        created = service.create_session(
            AdapterSessionCreateRequest(
                schema_version="2026-05-24",
                adapter_name="test",
                adapter_version="0",
                game="minecraft",
                player_name="p",
                capabilities=[],
            )
        )
        accepted = service.push_player_input("和行為為団って何？", source="voice")
        self.assertTrue(accepted["accepted"])

        result = service.process_event(
            _event(sequence=1),
            session_id=created.session_id,
        )
        replies = [
            action
            for action in result.actions
            if action.layer == "speech" and action.text
        ]

        self.assertEqual(1, len(replies))
        self.assertEqual("和行為為団って何？", replies[0].display_player_input_text)
        self.assertIn("原文の『ゐ』は保存", replies[0].text)

    def test_non_question_uses_existing_chat_without_lookup(self) -> None:
        machine = DogidoStateMachine(
            Settings(decision_policy="py_trees", llm_enabled=False, audio_enabled=False)
        )
        machine.knowledge_provider = NeverKnowledgeProvider()
        result = machine.process(_event(sequence=1, user_text="枕詞ってええ響きやな"))
        speeches = [action.text for action in result.actions if action.layer == "speech" and action.text]
        self.assertEqual([fallback_text("general", "chat", "reply")], speeches)

    def test_provider_failure_is_fixed_no_guess_reply(self) -> None:
        machine = DogidoStateMachine(
            Settings(decision_policy="py_trees", llm_enabled=True, audio_enabled=False),
            llm=NeverLLM(),  # type: ignore[arg-type]
        )
        provider = RecordingKnowledgeProvider(error=ValueError("broken index"))
        machine.knowledge_provider = provider
        result = machine.process(_event(sequence=1, user_text="枕詞って何？"))
        speeches = [action.text for action in result.actions if action.layer == "speech" and action.text]
        self.assertEqual(1, len(provider.calls))
        self.assertEqual(1, len(speeches))
        self.assertIn("公式資料を今は読めへん", speeches[0])
        self.assertIn("推測では答えん", speeches[0])

    def test_invalid_nested_provider_results_fail_closed_without_escaping(self) -> None:
        bad_source = KnowledgeSource(
            source_id="bad.personal",
            title_ja="個人作成資料",
            citation_label_ja="個人塾",
            url="https://example.invalid/blog",
            source_kind="personal_blog",
        )

        def untrusted(query):  # type: ignore[no-untyped-def]
            return KnowledgeLookupResult(
                query=query,
                status="found",
                facts=(
                    KnowledgeFact(
                        record_id="bad.fact",
                        dataset_id="bad",
                        title_ja="不正な項目",
                        text_ja="個人作成の説明です。",
                        claim_status="source_stated",
                        sources=(bad_source,),
                    ),
                ),
            )

        def malformed(query):  # type: ignore[no-untyped-def]
            return KnowledgeLookupResult(
                query=query,
                status="found",
                facts=(
                    KnowledgeFact(
                        record_id="bad.fact",
                        dataset_id="bad",
                        title_ja="不正な項目",
                        text_ja="不正な説明です。",
                        claim_status="source_stated",
                        sources=("bad",),  # type: ignore[arg-type]
                    ),
                ),
            )

        def stale(query):  # type: ignore[no-untyped-def]
            other = ExplicitKnowledgeQuery(
                domain=query.domain,
                subject="別の質問",
                intent=query.intent,
                evidence="別の質問って何？",
            )
            return KnowledgeLookupResult(
                query=other,
                status="found",
                facts=(_fact(1),),
            )

        for name, transform in (
            ("untrusted", untrusted),
            ("malformed", malformed),
            ("stale", stale),
        ):
            with self.subTest(name=name):
                machine = DogidoStateMachine(
                    Settings(
                        decision_policy="py_trees",
                        llm_enabled=True,
                        audio_enabled=False,
                    ),
                    llm=NeverLLM(),  # type: ignore[arg-type]
                )
                provider = TransformingKnowledgeProvider(transform)
                machine.knowledge_provider = provider
                result = machine.process(_event(sequence=1, user_text="枕詞って何？"))
                speeches = [
                    action.text
                    for action in result.actions
                    if action.layer == "speech" and action.text
                ]
                self.assertEqual(1, provider.calls)
                self.assertEqual(1, len(speeches))
                self.assertIn("公式資料を今は読めへん", speeches[0])
                self.assertNotIn("個人作成", speeches[0])

    def test_provider_cannot_reuse_official_metadata_with_modified_fact_text(self) -> None:
        authority = LocalKnowledgeProvider()

        def modified(query):  # type: ignore[no-untyped-def]
            authentic = authority.lookup(query, limit=3)
            self.assertEqual("found", authentic.status)
            changed_fact = replace(
                authentic.facts[0],
                text_ja="枕詞はMinecraftの武器です。",
            )
            # 正規のID・出典一式を引き継いでも、正本再構成との全文照合に失敗する。
            return replace(authentic, facts=(changed_fact, *authentic.facts[1:]))

        machine = DogidoStateMachine(
            Settings(decision_policy="py_trees", llm_enabled=False, audio_enabled=False)
        )
        machine.knowledge_provider = TransformingKnowledgeProvider(modified)
        result = machine.process(_event(sequence=1, user_text="枕詞って何？"))
        speeches = [
            action.text
            for action in result.actions
            if action.layer == "speech" and action.text
        ]
        self.assertEqual(1, len(speeches))
        self.assertIn("公式資料を今は読めへん", speeches[0])
        self.assertNotIn("Minecraftの武器", speeches[0])


class KnowledgeSafetyPriorityTests(unittest.TestCase):
    def test_sword_fact_question_never_reaches_assist_intent_classifier(self) -> None:
        service = DogidoService(
            Settings(llm_enabled=False, audio_enabled=False, memory_enabled=False)
        )
        service.llm = NeverAssistClassifierLLM()  # type: ignore[assignment]
        session = service.create_session(
            AdapterSessionCreateRequest(
                schema_version="2026-05-24",
                adapter_name="test",
                adapter_version="0",
                game="minecraft",
                player_name="p",
                capabilities=[],
            )
        )

        routed = service._route_assist_player_input(  # noqa: SLF001
            service.sessions[session.session_id],
            "ダイヤモンドの剣の耐久値は？",
            interpreted_player_text=None,
        )

        self.assertIsNotNone(routed.knowledge_query)
        self.assertFalse(routed.requests_sword)

    def test_warden_sonic_boom_preempts_lookup_in_both_policies(self) -> None:
        for policy in ("py_trees", "legacy"):
            with self.subTest(policy=policy), TemporaryDirectory() as temporary:
                service = DogidoService(
                    Settings(
                        llm_enabled=False,
                        audio_enabled=False,
                        decision_policy=policy,
                        memory_enabled=False,
                        memory_dir=Path(temporary) / "mem",
                    )
                )
                created = service.create_session(
                    AdapterSessionCreateRequest(
                        schema_version="2026-05-24",
                        adapter_name="test",
                        adapter_version="0",
                        game="minecraft",
                        player_name="p",
                        capabilities=[],
                    )
                )
                session = service.sessions[created.session_id]
                provider = RecordingKnowledgeProvider()
                session.machine.knowledge_provider = provider

                result = service.process_event(
                    _event(
                        sequence=1,
                        user_text="枕詞って何？",
                        ominous_sound_kind="warden_sonic_boom",
                        ominous_sound_recent_ms=100,
                    ),
                    session_id=created.session_id,
                )

                self.assertEqual([], provider.calls)
                self.assertEqual("枕詞って何？", session.pending_player_text)
                self.assertTrue(
                    any(
                        action.cue_id == "warden_sonic_boom_scream"
                        for action in result.actions
                    )
                )

    def test_direct_duplicate_consumes_matching_pending_question_once(self) -> None:
        for policy in ("py_trees", "legacy"):
            with self.subTest(policy=policy), TemporaryDirectory() as temporary:
                service = DogidoService(
                    Settings(
                        llm_enabled=False,
                        audio_enabled=False,
                        decision_policy=policy,
                        memory_enabled=False,
                        memory_dir=Path(temporary) / "mem",
                    )
                )
                created = service.create_session(
                    AdapterSessionCreateRequest(
                        schema_version="2026-05-24",
                        adapter_name="test",
                        adapter_version="0",
                        game="minecraft",
                        player_name="p",
                        capabilities=[],
                    )
                )
                session = service.sessions[created.session_id]
                provider = RecordingKnowledgeProvider()
                session.machine.knowledge_provider = provider
                self.assertTrue(
                    service._queue_player_input(  # noqa: SLF001
                        session,
                        "枕詞って何？",
                        source="voice",
                    )
                )

                service.process_event(
                    _event(sequence=1, user_text="枕詞って何？"),
                    session_id=created.session_id,
                )
                self.assertEqual(1, len(provider.calls))
                self.assertIsNone(session.pending_player_text)

                service.process_event(
                    _event(sequence=2),
                    session_id=created.session_id,
                )
                self.assertEqual(1, len(provider.calls))

    def test_queued_duplicate_is_deduplicated_by_text_across_sources(self) -> None:
        service = DogidoService(
            Settings(audio_enabled=False, llm_enabled=False, memory_enabled=False)
        )
        created = service.create_session(
            AdapterSessionCreateRequest(
                schema_version="2026-05-24",
                adapter_name="test",
                adapter_version="0",
                game="minecraft",
                player_name="p",
                capabilities=[],
            )
        )
        session = service.sessions[created.session_id]

        self.assertTrue(
            service._queue_player_input(  # noqa: SLF001
                session,
                "枕詞って何？",
                source="voice",
            )
        )
        self.assertTrue(
            service._queue_player_input(  # noqa: SLF001
                session,
                "枕詞って何？",
                source="text",
            )
        )

        self.assertEqual("枕詞って何？", session.pending_player_text)
        self.assertEqual("voice", session.pending_player_source)
        self.assertEqual([], list(session.deferred_player_inputs))

    def test_panic_preempts_lookup_then_normal_turn_answers_once(self) -> None:
        with TemporaryDirectory() as temporary:
            service = DogidoService(
                Settings(
                    llm_enabled=False,
                    audio_enabled=False,
                    decision_policy="py_trees",
                    memory_enabled=False,
                    memory_dir=Path(temporary) / "mem",
                )
            )
            created = service.create_session(
                AdapterSessionCreateRequest(
                    schema_version="2026-05-24",
                    adapter_name="test",
                    adapter_version="0",
                    game="minecraft",
                    player_name="p",
                    capabilities=[],
                )
            )
            session = service.sessions[created.session_id]
            provider = RecordingKnowledgeProvider()
            session.machine.knowledge_provider = provider
            session.machine.state.mode = "panic"
            session.pending_player_text = "枕詞って何？"
            session.pending_player_source = "voice"

            danger = service.process_event(
                _event(sequence=1, user_text=None, with_threat=True),
                session_id=created.session_id,
            )
            self.assertEqual([], provider.calls)
            self.assertEqual([], danger.response.commands)
            self.assertEqual("枕詞って何？", session.pending_player_text)

            session.machine.state.mode = "normal"
            safe = service.process_event(
                _event(sequence=2, user_text=None, with_threat=False),
                session_id=created.session_id,
            )
            self.assertEqual(1, len(provider.calls))
            self.assertIsNone(session.pending_player_text)
            self.assertTrue(
                any("枕詞っちゅうのは" in (action.text or "") for action in safe.actions)
            )

    def test_direct_alert_question_is_deferred_without_lookup(self) -> None:
        with TemporaryDirectory() as temporary:
            service = DogidoService(
                Settings(
                    llm_enabled=False,
                    audio_enabled=False,
                    decision_policy="py_trees",
                    memory_enabled=False,
                    memory_dir=Path(temporary) / "mem",
                )
            )
            created = service.create_session(
                AdapterSessionCreateRequest(
                    schema_version="2026-05-24",
                    adapter_name="test",
                    adapter_version="0",
                    game="minecraft",
                    player_name="p",
                    capabilities=[],
                )
            )
            session = service.sessions[created.session_id]
            provider = RecordingKnowledgeProvider()
            session.machine.knowledge_provider = provider

            danger = service.process_event(
                _event(
                    sequence=1,
                    user_text="枕詞って何？",
                    with_threat=True,
                    threat_distance=8.0,
                ),
                session_id=created.session_id,
            )

            self.assertEqual("alert", danger.response.state.mode)
            self.assertEqual([], provider.calls)
            self.assertEqual("枕詞って何？", session.pending_player_text)
            self.assertFalse(
                any("枕詞っちゅうのは" in (action.text or "") for action in danger.actions)
            )

            continued = service.process_event(
                _event(sequence=2, with_threat=True, threat_distance=8.0),
                session_id=created.session_id,
            )
            self.assertEqual("alert", continued.response.state.mode)
            self.assertEqual([], provider.calls)
            self.assertEqual("枕詞って何？", session.pending_player_text)

            # alertだった直前フレームでは一度保留したまま安全遷移を確認する。
            service.process_event(
                _event(sequence=3, with_threat=False),
                session_id=created.session_id,
            )
            self.assertEqual([], provider.calls)
            self.assertEqual("枕詞って何？", session.pending_player_text)

            answered = service.process_event(
                _event(sequence=4, with_threat=False),
                session_id=created.session_id,
            )
            self.assertEqual(1, len(provider.calls))
            self.assertIsNone(session.pending_player_text)
            self.assertTrue(
                any("枕詞っちゅうのは" in (action.text or "") for action in answered.actions)
            )

    def test_question_arriving_while_already_alert_is_not_answered_twice(self) -> None:
        with TemporaryDirectory() as temporary:
            service = DogidoService(
                Settings(
                    llm_enabled=False,
                    audio_enabled=False,
                    decision_policy="py_trees",
                    memory_enabled=False,
                    memory_dir=Path(temporary) / "mem",
                )
            )
            created = service.create_session(
                AdapterSessionCreateRequest(
                    schema_version="2026-05-24",
                    adapter_name="test",
                    adapter_version="0",
                    game="minecraft",
                    player_name="p",
                    capabilities=[],
                )
            )
            session = service.sessions[created.session_id]
            provider = RecordingKnowledgeProvider()
            session.machine.knowledge_provider = provider

            first = service.process_event(
                _event(sequence=1, with_threat=True, threat_distance=8.0),
                session_id=created.session_id,
            )
            self.assertEqual("alert", first.response.state.mode)

            second = service.process_event(
                _event(
                    sequence=2,
                    user_text="枕詞って何？",
                    with_threat=True,
                    threat_distance=8.0,
                ),
                session_id=created.session_id,
            )
            self.assertEqual("alert", second.response.state.mode)
            self.assertEqual([], provider.calls)
            self.assertEqual("枕詞って何？", session.pending_player_text)
            self.assertFalse(
                any("枕詞っちゅうのは" in (action.text or "") for action in second.actions)
            )

            service.process_event(
                _event(sequence=3, with_threat=False),
                session_id=created.session_id,
            )
            answered = service.process_event(
                _event(sequence=4, with_threat=False),
                session_id=created.session_id,
            )
            self.assertEqual(1, len(provider.calls))
            self.assertIsNone(session.pending_player_text)
            self.assertTrue(
                any("枕詞っちゅうのは" in (action.text or "") for action in answered.actions)
            )

    def test_combat_hint_defers_lookup_even_when_mode_remains_normal(self) -> None:
        with TemporaryDirectory() as temporary:
            service = DogidoService(
                Settings(
                    llm_enabled=False,
                    audio_enabled=False,
                    decision_policy="py_trees",
                    memory_enabled=False,
                    memory_dir=Path(temporary) / "mem",
                )
            )
            created = service.create_session(
                AdapterSessionCreateRequest(
                    schema_version="2026-05-24",
                    adapter_name="test",
                    adapter_version="0",
                    game="minecraft",
                    player_name="p",
                    capabilities=[],
                )
            )
            session = service.sessions[created.session_id]
            provider = RecordingKnowledgeProvider()
            session.machine.knowledge_provider = provider

            result = service.process_event(
                _event(
                    sequence=1,
                    user_text="枕詞って何？",
                    combat_active_hint=True,
                ),
                session_id=created.session_id,
            )

            self.assertEqual("normal", result.response.state.mode)
            self.assertTrue(result.response.state.combat_active)
            self.assertEqual([], provider.calls)
            self.assertEqual("枕詞って何？", session.pending_player_text)

    def test_darkness_only_alert_defers_without_treating_it_as_combat(self) -> None:
        for policy in ("py_trees", "legacy"):
            with self.subTest(policy=policy), TemporaryDirectory() as temporary:
                service = DogidoService(
                    Settings(
                        llm_enabled=False,
                        audio_enabled=False,
                        decision_policy=policy,
                        memory_enabled=False,
                        memory_dir=Path(temporary) / "mem",
                    )
                )
                created = service.create_session(
                    AdapterSessionCreateRequest(
                        schema_version="2026-05-24",
                        adapter_name="test",
                        adapter_version="0",
                        game="minecraft",
                        player_name="p",
                        capabilities=[],
                    )
                )
                session = service.sessions[created.session_id]
                provider = RecordingKnowledgeProvider()
                session.machine.knowledge_provider = provider

                result = service.process_event(
                    _event(
                        sequence=1,
                        user_text="枕詞って何？",
                        danger_darkness_score=0.95,
                        local_light=0,
                    ),
                    session_id=created.session_id,
                )

                self.assertEqual("alert", result.response.state.mode)
                self.assertFalse(result.response.state.combat_active)
                self.assertEqual([], provider.calls)
                self.assertEqual("枕詞って何？", session.pending_player_text)

    def test_combat_ended_speech_preempts_and_requeues_knowledge_question(self) -> None:
        for policy in ("py_trees", "legacy"):
            for stale_hint in (False, True):
                with (
                    self.subTest(policy=policy, stale_hint=stale_hint),
                    TemporaryDirectory() as temporary,
                ):
                    service = DogidoService(
                        Settings(
                            llm_enabled=False,
                            audio_enabled=False,
                            decision_policy=policy,
                            memory_enabled=False,
                            memory_dir=Path(temporary) / "mem",
                        )
                    )
                    created = service.create_session(
                        AdapterSessionCreateRequest(
                            schema_version="2026-05-24",
                            adapter_name="test",
                            adapter_version="0",
                            game="minecraft",
                            player_name="p",
                            capabilities=[],
                        )
                    )
                    session = service.sessions[created.session_id]
                    provider = RecordingKnowledgeProvider()
                    session.machine.knowledge_provider = provider

                    ended = service.process_event(
                        _event(
                            sequence=1,
                            user_text="枕詞って何？",
                            event_name=EventName.COMBAT_ENDED,
                            combat_active_hint=stale_hint,
                        ),
                        session_id=created.session_id,
                    )
                    self.assertEqual("aftermath", ended.response.state.mode)
                    self.assertEqual([], provider.calls)
                    self.assertEqual("枕詞って何？", session.pending_player_text)
                    self.assertFalse(
                        any("枕詞っちゅうのは" in (action.text or "") for action in ended.actions)
                    )

                    answered = service.process_event(
                        _event(
                            sequence=2,
                            at=BASE + timedelta(seconds=10),
                        ),
                        session_id=created.session_id,
                    )
                    self.assertEqual(1, len(provider.calls))
                    self.assertIsNone(session.pending_player_text)
                    self.assertTrue(
                        any("枕詞っちゅうのは" in (action.text or "") for action in answered.actions)
                    )

    def test_time_sensitive_night_warning_requeues_knowledge_question(self) -> None:
        with TemporaryDirectory() as temporary:
            service = DogidoService(
                Settings(
                    llm_enabled=False,
                    audio_enabled=False,
                    decision_policy="py_trees",
                    memory_enabled=False,
                    memory_dir=Path(temporary) / "mem",
                )
            )
            created = service.create_session(
                AdapterSessionCreateRequest(
                    schema_version="2026-05-24",
                    adapter_name="test",
                    adapter_version="0",
                    game="minecraft",
                    player_name="p",
                    capabilities=[],
                )
            )
            session = service.sessions[created.session_id]
            provider = RecordingKnowledgeProvider()
            session.machine.knowledge_provider = provider

            warning = service.process_event(
                _event(
                    sequence=1,
                    user_text="枕詞って何？",
                    time_phase=TimePhase.EVENING,
                ),
                session_id=created.session_id,
            )
            self.assertEqual([], provider.calls)
            self.assertEqual("枕詞って何？", session.pending_player_text)
            self.assertFalse(
                any("枕詞っちゅうのは" in (action.text or "") for action in warning.actions)
            )

            answered = service.process_event(
                _event(sequence=2, time_phase=TimePhase.DAY),
                session_id=created.session_id,
            )
            self.assertEqual(1, len(provider.calls))
            self.assertIsNone(session.pending_player_text)
            self.assertTrue(
                any("枕詞っちゅうのは" in (action.text or "") for action in answered.actions)
            )

    def test_direct_panic_question_is_preserved(self) -> None:
        with TemporaryDirectory() as temporary:
            service = DogidoService(
                Settings(
                    llm_enabled=False,
                    audio_enabled=False,
                    decision_policy="py_trees",
                    memory_enabled=False,
                    memory_dir=Path(temporary) / "mem",
                )
            )
            created = service.create_session(
                AdapterSessionCreateRequest(
                    schema_version="2026-05-24",
                    adapter_name="test",
                    adapter_version="0",
                    game="minecraft",
                    player_name="p",
                    capabilities=[],
                )
            )
            session = service.sessions[created.session_id]
            provider = RecordingKnowledgeProvider()
            session.machine.knowledge_provider = provider

            danger = service.process_event(
                _event(sequence=1, user_text="枕詞って何？", with_threat=True),
                session_id=created.session_id,
            )

            self.assertIn(danger.response.state.mode, {"panic", "suppressed_panic"})
            self.assertEqual([], provider.calls)
            self.assertEqual("枕詞って何？", session.pending_player_text)


class KnowledgeWorkshopTests(unittest.TestCase):
    def _service_with_workshop(
        self,
        temporary: str,
        *,
        policy: str = "py_trees",
    ):  # type: ignore[no-untyped-def]
        service = DogidoService(
            Settings(
                llm_enabled=False,
                audio_enabled=False,
                decision_policy=policy,
                memory_enabled=False,
                memory_dir=Path(temporary) / "mem",
            )
        )
        created = service.create_session(
            AdapterSessionCreateRequest(
                schema_version="2026-05-24",
                adapter_name="test",
                adapter_version="0",
                game="minecraft",
                player_name="p",
                capabilities=[],
            )
        )
        session = service.sessions[created.session_id]
        emission = HaikuEmission(
            created_at=BASE,
            text="ひらべった てのきのき ひるのひ",
            preface="ここで一句。",
            interpretation="平原の昼",
            biome="plains",
            structure=None,
            time_phase="day",
            dimension="minecraft:overworld",
            event_sequence=1,
            route="haiku",
        )
        service._open_haiku_workshop(session, emission, entry_id=None, now=BASE)
        return service, created.session_id, session

    def test_general_knowledge_question_keeps_pin_and_does_not_add_drift(self) -> None:
        with TemporaryDirectory() as temporary:
            service, session_id, session = self._service_with_workshop(temporary)
            provider = RecordingKnowledgeProvider()
            session.machine.knowledge_provider = provider
            assert session.haiku_workshop is not None
            before_drift = session.haiku_workshop.drift_count

            result = service.process_event(
                _event(sequence=2, user_text="枕詞って何？", at=BASE + timedelta(seconds=2)),
                session_id=session_id,
            )
            speeches = [action.text for action in result.actions if action.layer == "speech" and action.text]
            self.assertEqual(1, len(speeches))
            self.assertIn("枕詞っちゅうのは", speeches[0])
            self.assertEqual(1, len(provider.calls))
            self.assertIsNotNone(session.haiku_workshop)
            assert session.haiku_workshop is not None
            self.assertTrue(is_open(session.haiku_workshop))
            self.assertEqual(before_drift, session.haiku_workshop.drift_count)

    def test_current_verse_meaning_question_is_not_stolen(self) -> None:
        for player_text in (
            "平べったって何だろうか",
            "この川柳の平べったって何だろうか",
            "この川柳の意味は？",
            "今の川柳の形式は？",
        ):
            with self.subTest(player_text=player_text), TemporaryDirectory() as temporary:
                service, session_id, session = self._service_with_workshop(temporary)
                session.machine.knowledge_provider = NeverKnowledgeProvider()
                result = service.process_event(
                    _event(
                        sequence=2,
                        user_text=player_text,
                        at=BASE + timedelta(seconds=2),
                    ),
                    session_id=session_id,
                )
                speeches = [
                    action.text
                    for action in result.actions
                    if action.layer == "speech" and action.text
                ]
                self.assertEqual(1, len(speeches))
                self.assertNotIn("手元の公式資料", speeches[0])
                self.assertIsNone(session.pending_player_text)

    def test_general_question_keeps_combat_resume_confirmation_waiting(self) -> None:
        with TemporaryDirectory() as temporary:
            service, session_id, session = self._service_with_workshop(temporary)
            provider = RecordingKnowledgeProvider()
            session.machine.knowledge_provider = provider
            assert session.haiku_workshop is not None
            session.haiku_workshop.awaiting_combat_resume_confirmation = True

            result = service.process_event(
                _event(sequence=2, user_text="枕詞って何？", at=BASE + timedelta(seconds=2)),
                session_id=session_id,
            )

            self.assertEqual(1, len(provider.calls))
            self.assertTrue(
                any("枕詞っちゅうのは" in (action.text or "") for action in result.actions)
            )
            self.assertIsNotNone(session.haiku_workshop)
            assert session.haiku_workshop is not None
            self.assertTrue(session.haiku_workshop.awaiting_combat_resume_confirmation)

    def test_death_preempts_workshop_knowledge_and_preserves_the_question(self) -> None:
        with TemporaryDirectory() as temporary:
            service, session_id, session = self._service_with_workshop(temporary)
            provider = RecordingKnowledgeProvider()
            session.machine.knowledge_provider = provider

            result = service.process_event(
                _event(
                    sequence=2,
                    user_text="枕詞って何？",
                    event_name=EventName.PLAYER_DIED,
                    at=BASE + timedelta(seconds=2),
                ),
                session_id=session_id,
            )

            self.assertEqual([], provider.calls)
            self.assertEqual("枕詞って何？", session.pending_player_text)
            self.assertTrue(
                any(
                    action.layer == "speech"
                    and action.text
                    and "枕詞っちゅうのは" not in action.text
                    for action in result.actions
                )
            )

    def test_hint_only_combat_defers_workshop_knowledge_and_keeps_pin(self) -> None:
        for policy in ("py_trees", "legacy"):
            with self.subTest(policy=policy), TemporaryDirectory() as temporary:
                service, session_id, session = self._service_with_workshop(
                    temporary,
                    policy=policy,
                )
                provider = RecordingKnowledgeProvider()
                session.machine.knowledge_provider = provider

                result = service.process_event(
                    _event(
                        sequence=2,
                        user_text="枕詞って何？",
                        combat_active_hint=True,
                        at=BASE + timedelta(seconds=2),
                    ),
                    session_id=session_id,
                )

                self.assertEqual([], provider.calls)
                self.assertEqual("枕詞って何？", session.pending_player_text)
                self.assertIsNotNone(session.haiku_workshop)
                assert session.haiku_workshop is not None
                self.assertTrue(is_open(session.haiku_workshop))
                self.assertFalse(
                    any("枕詞っちゅうのは" in (action.text or "") for action in result.actions)
                )

    def test_combat_start_same_tick_preserves_workshop_input(self) -> None:
        hazards = {
            "visual": {"with_threat": True},
            "hint": {"combat_active_hint": True},
        }
        for policy in ("py_trees", "legacy"):
            for player_text in ("上五をあさのひに変えて", "この川柳の意味は？"):
                for hazard, updates in hazards.items():
                    with (
                        self.subTest(
                            policy=policy,
                            player_text=player_text,
                            hazard=hazard,
                        ),
                        TemporaryDirectory() as temporary,
                    ):
                        service, session_id, session = self._service_with_workshop(
                            temporary,
                            policy=policy,
                        )
                        session.machine.knowledge_provider = NeverKnowledgeProvider()
                        assert session.haiku_workshop is not None
                        before = session.haiku_workshop.display_line()

                        service.process_event(
                            _event(
                                sequence=2,
                                user_text=player_text,
                                at=BASE + timedelta(seconds=2),
                                **updates,
                            ),
                            session_id=session_id,
                        )

                        self.assertIsNotNone(session.haiku_workshop)
                        assert session.haiku_workshop is not None
                        self.assertTrue(session.haiku_workshop.combat_paused)
                        self.assertEqual(before, session.haiku_workshop.display_line())
                        self.assertEqual(player_text, session.pending_player_text)

    def test_darkness_and_evening_preempt_workshop_questions(self) -> None:
        scenarios = {
            "darkness_current_verse": (
                "この川柳の意味は？",
                {"danger_darkness_score": 0.95, "local_light": 0},
            ),
            "evening_general": (
                "枕詞って何？",
                {"time_phase": TimePhase.EVENING},
            ),
        }
        for policy in ("py_trees", "legacy"):
            for scenario, (player_text, updates) in scenarios.items():
                with (
                    self.subTest(policy=policy, scenario=scenario),
                    TemporaryDirectory() as temporary,
                ):
                    service, session_id, session = self._service_with_workshop(
                        temporary,
                        policy=policy,
                    )
                    provider = RecordingKnowledgeProvider()
                    session.machine.knowledge_provider = provider

                    result = service.process_event(
                        _event(
                            sequence=2,
                            user_text=player_text,
                            at=BASE + timedelta(seconds=2),
                            **updates,
                        ),
                        session_id=session_id,
                    )

                    self.assertEqual([], provider.calls)
                    self.assertEqual(player_text, session.pending_player_text)
                    if scenario == "evening_general":
                        self.assertTrue(
                            any(action.layer == "speech" for action in result.actions)
                        )
                    else:
                        self.assertEqual("alert", result.response.state.mode)
                        self.assertFalse(
                            any(
                                "この川柳" in (action.text or "")
                                for action in result.actions
                            )
                        )

    def test_dimension_change_preempts_workshop_knowledge(self) -> None:
        for policy in ("py_trees", "legacy"):
            with self.subTest(policy=policy), TemporaryDirectory() as temporary:
                service, session_id, session = self._service_with_workshop(
                    temporary,
                    policy=policy,
                )
                provider = RecordingKnowledgeProvider()
                session.machine.knowledge_provider = provider
                service.process_event(
                    _event(sequence=1, dimension="minecraft:overworld"),
                    session_id=session_id,
                )

                result = service.process_event(
                    _event(
                        sequence=2,
                        user_text="枕詞って何？",
                        dimension="minecraft:the_nether",
                    ),
                    session_id=session_id,
                )

                self.assertEqual([], provider.calls)
                self.assertEqual("枕詞って何？", session.pending_player_text)
                self.assertTrue(any(action.interrupt for action in result.actions))


if __name__ == "__main__":
    unittest.main()
