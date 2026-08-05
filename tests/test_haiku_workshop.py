"""haiku workshop pin / open-close / intent."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dogido_server.config import Settings
from dogido_server.haiku.workshop import (
    classify_workshop_intent,
    close_workshop,
    is_open,
    is_workshop_hard_off_topic,
    maybe_close_for_time,
    open_from_emission,
    record_drift,
    render_workshop_reply,
    should_handle_as_workshop,
    workshop_open_intent,
)
from dogido_server.memory import MemoryStore
from dogido_server.memory_types import HaikuEmission
from dogido_server.models import (
    AdapterSessionCreateRequest,
    EventDescriptor,
    EventName,
    GameEvent,
    MetaState,
    PlayerState,
    Position,
    PriorityHint,
    SourceKind,
    Certainty,
    TimePhase,
    Weather,
    WorldState,
)
from dogido_server.service import DogidoService
from dogido_server.state_machine import AudioAction


def _emission(text: str = "あさひさす むらに あかがね", *, interpretation: str | None = None) -> HaikuEmission:
    return HaikuEmission(
        created_at=datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
        text=text,
        preface="ここで一句。",
        interpretation=interpretation or "平原の村の朝と銅のドア",
        biome="plains",
        structure=None,
        time_phase="day",
        dimension="minecraft:overworld",
        event_sequence=1,
        route="haiku",
    )


class WorkshopLifecycleTests(unittest.TestCase):
    def test_open_and_close(self) -> None:
        ws = open_from_emission(_emission())
        self.assertTrue(is_open(ws))
        self.assertIn("あさひさす", ws.surface_text)
        self.assertIn("平原", ws.materials.get("interpretation", ""))
        close_workshop(ws, reason="explicit")
        self.assertFalse(is_open(ws))
        self.assertEqual(ws.close_reason, "explicit")

    def test_drift_closes_after_two(self) -> None:
        ws = open_from_emission(_emission())
        now = ws.emitted_at
        record_drift(ws, now=now)
        self.assertTrue(is_open(ws))
        closed = record_drift(ws, now=now + timedelta(seconds=1))
        self.assertIsNotNone(closed)
        assert closed is not None
        self.assertFalse(closed.open)
        self.assertEqual(closed.close_reason, "drift")

    def test_timeout_idle(self) -> None:
        ws = open_from_emission(_emission())
        later = ws.emitted_at + timedelta(seconds=200)
        closed = maybe_close_for_time(ws, now=later, t_open=timedelta(seconds=300), t_idle=timedelta(seconds=90))
        self.assertIsNotNone(closed)
        assert closed is not None
        self.assertFalse(closed.open)
        self.assertEqual(closed.close_reason, "timeout_idle")


class WorkshopIntentTests(unittest.TestCase):
    def test_classify(self) -> None:
        self.assertEqual(classify_workshop_intent("グーの木の水って何?"), "ask_meaning")
        self.assertEqual(classify_workshop_intent("木木って何ですか?"), "ask_meaning")
        self.assertEqual(classify_workshop_intent("平べったって何だろうか"), "ask_meaning")
        self.assertEqual(classify_workshop_intent("無理やり圧縮しすぎ"), "critique_forced")
        self.assertEqual(classify_workshop_intent("いい句やな"), "praise")
        self.assertEqual(classify_workshop_intent("もうええ"), "close")
        # 納得は ack（「意味」誤爆しない）
        self.assertEqual(classify_workshop_intent("なるほどねそういう意味か"), "ack")
        # 句断片 + 疑問（verse 渡し）
        verse = "はれのばら\nわびたふどうの\nてのなか"
        self.assertEqual(
            classify_workshop_intent("晴れのバラ?", verse=verse),
            "ask_meaning",
        )
        self.assertEqual(
            classify_workshop_intent("バラとは何でしょうか", verse=verse),
            "ask_meaning",
        )
        # 読みメタ + 好み（素材名に依存しない）
        self.assertEqual(
            classify_workshop_intent("こっちの読み方の方がよかったんじゃないかなと思う"),
            "other_haiku",
        )
        # 言い換えパターン（ドメイン語リストは使わない）
        self.assertEqual(
            classify_workshop_intent("AじゃなくてBの方がしっくりくる"),
            "other_haiku",
        )
        # 場面ずれも固有モチーフ名ではなくメタ表現
        self.assertEqual(classify_workshop_intent("場と違うやろ"), "critique_offscene")
        self.assertIsNone(classify_workshop_intent("おはよう"))
        self.assertIsNone(classify_workshop_intent("松明ある？"))
        # 固有モチーフ名だけでは hit しない（リストに載せていない）
        self.assertIsNone(classify_workshop_intent("海やな"))
        self.assertIsNone(classify_workshop_intent("村がある"))
        # Stage1: 実ログ由来の soft マーカー（メタ語のみ）
        self.assertEqual(
            classify_workshop_intent("なかなかいいねでも黒い石炭がちょっと長いかも"),
            "critique_forced",
        )
        self.assertEqual(
            classify_workshop_intent("黒石炭とかにしたらいいんじゃない?"),
            "other_haiku",
        )
        self.assertEqual(
            classify_workshop_intent("じゃあ覚えておいてください"),
            "other_haiku",
        )
        self.assertEqual(
            classify_workshop_intent("どこから来たのかな?"),
            "ask_meaning",
        )
        self.assertEqual(
            classify_workshop_intent("そっち抜くしだったら意味が通じるんじゃない"),
            "other_haiku",  # だったら + 通じる → soft suggest が先
        )
        self.assertEqual(
            classify_workshop_intent("土の抜くっていうのはちょっと日本語としておかしいんじゃないでしょうか"),
            "critique_gibberish",
        )

    def test_soft_default_and_hard_off_topic_gate(self) -> None:
        """Stage2: open 中は soft 既定。hard off-topic だけ chat/drift。"""
        verse = "そらまぶし\nくさむらにうかぶ\nくろいせきたん"
        # マーカー外でも open 中は soft_default
        self.assertEqual(
            workshop_open_intent("いや土ぬくしだよ", verse=verse),
            "soft_default",
        )
        self.assertTrue(should_handle_as_workshop("いや土ぬくしだよ", verse=verse))
        # 明確な別件
        self.assertTrue(is_workshop_hard_off_topic("松明ある？"))
        self.assertIsNone(workshop_open_intent("松明ある？", verse=verse))
        self.assertFalse(should_handle_as_workshop("松明ある？", verse=verse))
        self.assertIsNone(workshop_open_intent("おはよう", verse=verse))
        # マーカー hit は soft_default に落ちない
        self.assertEqual(
            workshop_open_intent("黒石炭とかにしたらいいんじゃない?", verse=verse),
            "other_haiku",
        )

    def test_remember_template_reply(self) -> None:
        ws = open_from_emission(_emission())
        reply = render_workshop_reply(
            "other_haiku",
            ws,
            player_text="じゃあ覚えておいてください",
        )
        self.assertIn("覚え", reply)

    def test_reply_fragment_picks_material(self) -> None:
        from dogido_server.haiku.workshop import (
            finalize_ask_meaning_reply,
            material_candidates_for_speech,
            materials_speech_line,
            pick_material_for_fragment,
        )

        ws = open_from_emission(
            _emission(text="はれのばら\nわびたふどうの\nてのなか"),
            materials={
                "interpretation": "広大な平原の昼間の明るさと、錆びた銅のランタンの古びた雰囲気の対比",
                "biome": "plains",
                "biome_ja": "平原",
                "structure": "village_plains",
                "structure_ja": "平原の村",
                "motifs": ["錆びた銅のランタン", "平原", "昼"],
            },
        )

        speech = materials_speech_line(ws)
        self.assertNotIn("biome:", speech)
        self.assertNotIn("plains", speech)
        cands = material_candidates_for_speech(ws)
        self.assertIn("平原", cands)
        self.assertIn("錆びた銅のランタン", cands)

        # LLM 経路: pick + 柔軟な言い回し
        plains_idx = cands.index("平原")
        reply, path = finalize_ask_meaning_reply(
            ws,
            "晴れのバラ?",
            {"pick_index": plains_idx, "reply": "平原のことやで。"},
        )
        self.assertEqual(path, "llm")
        self.assertEqual(reply, "平原のことやで。")
        self.assertNotIn("biome", reply)

        # 言い回しが空でも pick があればテンプレ
        reply_t, path_t = finalize_ask_meaning_reply(
            ws,
            "バラとは何でしょうか",
            {"pick_index": plains_idx, "reply": ""},
        )
        self.assertEqual(path_t, "template")
        self.assertEqual(reply_t, "それは、平原やで。")

        # meta 漏れは落とす
        reply_bad, path_bad = finalize_ask_meaning_reply(
            ws,
            "晴れのバラ?",
            {"pick_index": plains_idx, "reply": "biome: plains やで"},
        )
        self.assertEqual(path_bad, "template")
        self.assertEqual(reply_bad, "それは、平原やで。")

        # 部分一致フォールバック（LLM なし）: 「平原」が fragment に含まれる場合のみ
        self.assertEqual(pick_material_for_fragment("平原", ws), "平原")
        # 詩的対応はコードでは当てない
        self.assertIsNone(pick_material_for_fragment("はれのばら", ws))

        soft, soft_path = finalize_ask_meaning_reply(ws, "はれのばらって何", None)
        self.assertEqual(soft_path, "soft_fail")
        self.assertIn("はれのばら", soft)

        ack = render_workshop_reply("ack", ws, player_text="なるほどねそういう意味か")
        self.assertLess(len(ack), 20)

    def test_reply_soft_tones(self) -> None:
        ws = open_from_emission(_emission())
        self.assertIn("余白", render_workshop_reply("critique_forced", ws))
        self.assertIn("ずれた", render_workshop_reply("critique_offscene", ws))
        praise = render_workshop_reply("praise", ws)
        self.assertIn("残しとく", praise)
        self.assertNotIn("緩める", praise)

    def test_conversational_revise_extract(self) -> None:
        from dogido_server.haiku.workshop import extract_conversational_revise

        self.assertEqual(
            extract_conversational_revise("こう直して: あさひさす / むらのどう / あかがね"),
            "あさひさす\nむらのどう\nあかがね",
        )
        self.assertIsNone(extract_conversational_revise("こう直してや"))

    def test_lessons_from_critique(self) -> None:
        from dogido_server.haiku.workshop import lessons_from_critique_kind

        lessons = lessons_from_critique_kind("forced_compress")
        self.assertTrue(lessons)
        self.assertIn("余白", lessons[0]["note"])
        self.assertEqual(lessons[0]["polarity"], "tighten")
        # praise / other は常駐 lesson を増やさない
        self.assertEqual(lessons_from_critique_kind("praise"), [])
        self.assertEqual(lessons_from_critique_kind("other", player_text="なんか微妙"), [])


    def test_lessons_list_soft_and_loosen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(Path(tmp) / "mem")
            store.save_haiku_lesson(
                lesson_type="compress",
                note="要素を少し絞って余白を残すとよい",
                polarity="tighten",
            )
            store.save_haiku_lesson(
                lesson_type="readability",
                note="読みやすさを少し意識する",
                polarity="tighten",
            )
            # 同軸は最新1件
            store.save_haiku_lesson(
                lesson_type="compress",
                note="詰め込み注意（新しい方）",
                polarity="tighten",
            )
            listed = store.list_recent_haiku_lessons(limit=3)
            notes = [str(x.get("note")) for x in listed]
            self.assertIn("詰め込み注意（新しい方）", notes)
            self.assertNotIn("要素を少し絞って余白を残すとよい", notes)
            self.assertEqual(len(listed), 2)
            # 明示「気にせんで」相当 → 全軸 loosen（praise ではこれを書かない）
            store.save_haiku_lesson(lesson_type="*", note="", polarity="loosen", strength=0.0)
            self.assertEqual(store.list_recent_haiku_lessons(limit=3), [])

    def test_wants_clear_lessons_not_close(self) -> None:
        from dogido_server.haiku.workshop import wants_clear_haiku_lessons

        self.assertTrue(wants_clear_haiku_lessons("もう気にせんでええわ"))
        self.assertTrue(wants_clear_haiku_lessons("前の注意いらない"))
        self.assertFalse(wants_clear_haiku_lessons("もうええ"))
        self.assertEqual(classify_workshop_intent("もう気にせんで"), "clear_lessons")
        self.assertEqual(classify_workshop_intent("もうええ"), "close")

    def test_lessons_ttl_by_age_and_emissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(Path(tmp) / "mem")
            old = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
            now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
            store.save_haiku_lesson(
                lesson_type="compress",
                note="古い注意",
                polarity="tighten",
                observed_at=old,
            )
            # 14日超 → 出ない
            self.assertEqual(
                store.list_recent_haiku_lessons(limit=3, now=now, max_age_days=14),
                [],
            )
            recent = now - timedelta(days=1)
            store.save_haiku_lesson(
                lesson_type="compress",
                note="最近の注意",
                polarity="tighten",
                observed_at=recent,
            )
            listed = store.list_recent_haiku_lessons(limit=3, now=now, max_age_days=14)
            self.assertEqual(len(listed), 1)
            self.assertIn("最近", listed[0]["note"])
            # 発句を max 回積むと薄まる
            emission = _emission()
            for i in range(6):
                emission = HaikuEmission(
                    created_at=recent + timedelta(minutes=i + 1),
                    text=f"てすとく{i} あ い",
                    preface="ここで一句。",
                    interpretation="test",
                    biome="plains",
                    structure=None,
                    time_phase="day",
                    dimension="minecraft:overworld",
                    event_sequence=10 + i,
                    route="haiku",
                )
                store.save_agent_haiku(emission)
            self.assertEqual(
                store.list_recent_haiku_lessons(
                    limit=3, now=now, max_age_days=14, max_emissions_after=6
                ),
                [],
            )


class WorkshopServiceIntegrationTests(unittest.TestCase):
    def test_emit_opens_workshop_and_critique_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                llm_enabled=False,
                audio_enabled=False,
                decision_policy="py_trees",
                memory_enabled=True,
                memory_dir=Path(tmp) / "mem",
            )
            service = DogidoService(settings)
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
            # session_id from response
            sid = session.session_id
            sess = service.sessions[sid]
            emission = _emission()
            service._open_haiku_workshop(sess, emission, entry_id="h_test", now=emission.created_at)
            self.assertTrue(is_open(sess.haiku_workshop))

            event = GameEvent(
                schema_version="2026-05-24",
                adapter="test",
                observed_at=emission.created_at + timedelta(seconds=5),
                sequence=2,
                event=EventDescriptor(
                    name=EventName.STATUS_SNAPSHOT,
                    source_kind=SourceKind.SYSTEM,
                    priority_hint=PriorityHint.BACKGROUND,
                    certainty=Certainty.HIGH,
                ),
                player=PlayerState(
                    name="p",
                    position=Position(x=0, y=64, z=0),
                    dimension="minecraft:overworld",
                ),
                world=WorldState(
                    time_phase=TimePhase.DAY,
                    weather=Weather.CLEAR,
                    biome="plains",
                    local_light=15,
                    sky_visible=True,
                ),
                meta=MetaState(user_text="グーの木の水って何?"),
            )
            sess.machine.player_input = __import__(
                "dogido_server.player_input", fromlist=["route_player_input"]
            ).route_player_input("グーの木の水って何?")
            actions = service._haiku_workshop_actions(sess, event)
            self.assertEqual(1, len(actions))
            # 句に無い語への問い → 聞き返し（全文講義しない）
            self.assertTrue(
                "どの言葉" in (actions[0].text or "")
                or "読みにく" in (actions[0].text or ""),
                msg=actions[0].text,
            )
            self.assertNotIn("biome", actions[0].text or "")
            # critique was written via service.memory
            self.assertTrue((Path(tmp) / "mem" / "long_term" / "haiku_critiques.jsonl").exists())
            self.assertTrue((Path(tmp) / "mem" / "long_term" / "haiku_lessons.jsonl").exists())
            # list は wall-clock 基準の TTL があるので、発句時刻基準で見る（日付固定テストのゆらぎ防止）
            lessons = MemoryStore(Path(tmp) / "mem").list_recent_haiku_lessons(
                limit=3,
                now=event.observed_at,
            )
            self.assertTrue(any("読みやす" in str(x.get("note")) for x in lessons))
            # hard 合流用の fragments があっても soft のまま
            self.assertTrue(all(x.get("polarity") != "loosen" for x in lessons))

    def test_workshop_defers_night_warning_until_closed(self) -> None:
        """workshop open 中は夕方割り込みを出さず、閉じたあとに出せる。"""
        from dogido_server.haiku.workshop import close_workshop
        from dogido_server.player_input.routing import route_player_input
        from dogido_server.state_machine import DogidoStateMachine

        settings = Settings(
            llm_enabled=False,
            audio_enabled=False,
            decision_policy="py_trees",
            player_input_priority_cooldown_ms=20000,
        )
        machine = DogidoStateMachine(settings)
        emission = _emission()
        workshop = open_from_emission(emission, materials={"interpretation": "平原"}, now=emission.created_at)
        machine.haiku_workshop_provider = lambda: workshop

        evening = GameEvent(
            schema_version="2026-05-24",
            adapter="test",
            observed_at=emission.created_at + timedelta(seconds=10),
            sequence=2,
            event=EventDescriptor(
                name=EventName.STATUS_SNAPSHOT,
                source_kind=SourceKind.SYSTEM,
                priority_hint=PriorityHint.BACKGROUND,
                certainty=Certainty.HIGH,
            ),
            player=PlayerState(
                name="p",
                position=Position(x=0, y=64, z=0),
                dimension="minecraft:overworld",
            ),
            world=WorldState(
                time_phase=TimePhase.EVENING,
                weather=Weather.CLEAR,
                biome="plains",
                local_light=12,
                sky_visible=True,
                enclosure_score=0.05,
                ceiling_height=24.0,
                danger_darkness_score=0.2,
            ),
            meta=MetaState(user_text="平原と平場は結びつけない方がいい"),
        )
        # 入力優先を立てる（夜警告の注意喚起条件）
        machine.player_input = route_player_input(evening.meta.user_text or "")
        machine.state.last_player_input_at = evening.observed_at
        machine.state.night_warning_pending = True
        machine.state.night_warning_emitted_this_cycle = False

        during = machine.process(evening)
        speeches = [a.text for a in during.actions if a.layer == "speech" and a.text]
        self.assertFalse(
            any(t and ("夜" in t or "夕方" in t) for t in speeches),
            msg=speeches,
        )
        # pending は消費しない
        self.assertFalse(machine.state.night_warning_emitted_this_cycle)

        close_workshop(workshop, reason="explicit")
        later = evening.model_copy(
            update={
                "sequence": 3,
                "observed_at": evening.observed_at + timedelta(seconds=2),
                "meta": MetaState(user_text=None),
            }
        )
        machine.player_input = route_player_input(None)
        after = machine.process(later)
        after_speech = [a.text for a in after.actions if a.layer == "speech" and a.text]
        self.assertTrue(
            any(t and ("夜" in t or "夕方" in t) for t in after_speech),
            msg=after_speech,
        )

    def test_workshop_meaning_question_beats_player_chat(self) -> None:
        """workshop open 中の『〜って何』は player_chat ではなく workshop 返事だけ。"""

        class CaptureChatLLM:
            def __init__(self) -> None:
                self.kinds: list[str] = []
                self.structured_kinds: list[str] = []

            def preload(self) -> bool:
                return False

            def generate_leaf_text(self, request) -> str:  # type: ignore[no-untyped-def]
                self.kinds.append(request.kind)
                return "LLMが雑談で答えた文"

            def generate_structured_json(self, request) -> dict[str, object]:  # type: ignore[no-untyped-def]
                self.structured_kinds.append(request.kind)
                # 材料 pick は正直に soft_fail を返す想定
                return {
                    "pick_index": None,
                    "reply": "「ひらべった」の読みやね。ちょっと分かりにくかったかも。",
                }

        with tempfile.TemporaryDirectory() as tmp:
            llm = CaptureChatLLM()
            settings = Settings(
                llm_enabled=True,
                audio_enabled=False,
                decision_policy="py_trees",
                memory_enabled=True,
                memory_dir=Path(tmp) / "mem",
            )
            service = DogidoService(settings)
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
            sid = session.session_id
            sess = service.sessions[sid]
            sess.machine.llm = llm
            service.llm = llm  # ask_meaning は service.llm を使う
            emission = _emission(text="ひらべった てのきのき ひるのひ")
            service._open_haiku_workshop(sess, emission, entry_id="h_test", now=emission.created_at)

            event = GameEvent(
                schema_version="2026-05-24",
                adapter="test",
                observed_at=emission.created_at + timedelta(seconds=5),
                sequence=2,
                event=EventDescriptor(
                    name=EventName.STATUS_SNAPSHOT,
                    source_kind=SourceKind.SYSTEM,
                    priority_hint=PriorityHint.BACKGROUND,
                    certainty=Certainty.HIGH,
                ),
                player=PlayerState(
                    name="p",
                    position=Position(x=0, y=64, z=0),
                    dimension="minecraft:overworld",
                ),
                world=WorldState(
                    time_phase=TimePhase.DAY,
                    weather=Weather.CLEAR,
                    biome="plains",
                    local_light=15,
                    sky_visible=True,
                ),
                meta=MetaState(user_text="平べったって何だろうか"),
            )
            result = service.process_event(event, session_id=sid)
            speeches = [a.text for a in result.actions if a.layer == "speech" and a.text]
            self.assertEqual(len(speeches), 1)
            self.assertNotIn("LLMが雑談で答えた文", speeches[0])
            self.assertTrue(
                "ひらべった" in (speeches[0] or "") or "読みにく" in (speeches[0] or ""),
                msg=speeches[0],
            )
            self.assertNotIn("player_chat", llm.kinds)
            self.assertIn("haiku_workshop_material_pick", llm.structured_kinds)

    def test_ask_meaning_llm_picks_material(self) -> None:
        """ask_meaning は候補を閉じて LLM に選ばせ、柔軟な reply を通す。"""

        class PickLLM:
            def preload(self) -> bool:
                return False

            def generate_leaf_text(self, request) -> str:  # type: ignore[no-untyped-def]
                raise AssertionError("leaf should not run")

            def generate_structured_json(self, request) -> dict[str, object]:  # type: ignore[no-untyped-def]
                self.last = request
                cands = list(request.details.get("candidates") or [])
                self.assert_plains = "平原" in cands
                idx = cands.index("平原") if "平原" in cands else 0
                return {"pick_index": idx, "reply": "平原のことやで。"}

        with tempfile.TemporaryDirectory() as tmp:
            llm = PickLLM()
            settings = Settings(
                llm_enabled=True,
                audio_enabled=False,
                decision_policy="py_trees",
                memory_enabled=True,
                memory_dir=Path(tmp) / "mem",
            )
            service = DogidoService(settings)
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
            sid = session.session_id
            sess = service.sessions[sid]
            service.llm = llm
            emission = _emission(text="はれのばら\nわびたふどうの\nてのなか")
            service._open_haiku_workshop(
                sess,
                emission,
                entry_id="h_pick",
                now=emission.created_at,
            )
            # open の materials を上書き（現実に近い候補）
            assert sess.haiku_workshop is not None
            sess.haiku_workshop.materials = {
                "interpretation": "広大な平原と錆びた銅のランタン",
                "biome": "plains",
                "biome_ja": "平原",
                "motifs": ["錆びた銅のランタン", "平原", "昼"],
            }
            event = GameEvent(
                schema_version="2026-05-24",
                adapter="test",
                observed_at=emission.created_at + timedelta(seconds=5),
                sequence=2,
                event=EventDescriptor(
                    name=EventName.STATUS_SNAPSHOT,
                    source_kind=SourceKind.SYSTEM,
                    priority_hint=PriorityHint.BACKGROUND,
                    certainty=Certainty.HIGH,
                ),
                player=PlayerState(
                    name="p",
                    position=Position(x=0, y=64, z=0),
                    dimension="minecraft:overworld",
                ),
                world=WorldState(
                    time_phase=TimePhase.DAY,
                    weather=Weather.CLEAR,
                    biome="plains",
                    local_light=15,
                    sky_visible=True,
                ),
                meta=MetaState(user_text="晴れのバラ?"),
            )
            result = service.process_event(event, session_id=sid)
            speeches = [a.text for a in result.actions if a.layer == "speech" and a.text]
            self.assertEqual(speeches, ["平原のことやで。"])
            self.assertEqual(getattr(llm, "last").kind, "haiku_workshop_material_pick")
            self.assertTrue(getattr(llm, "assert_plains"))

    def test_soft_critique_uses_poet_leaf_not_player_chat(self) -> None:
        """Stage2+4: 添削っぽい自然文は poet leaf。player_chat に落ちない。pin も維持。"""

        class PoetLLM:
            def __init__(self) -> None:
                self.kinds: list[str] = []

            def preload(self) -> bool:
                return False

            def generate_leaf_text(self, request) -> str:  # type: ignore[no-untyped-def]
                self.kinds.append(request.kind)
                if request.kind == "haiku_workshop_reply":
                    return "せやな、くろいせきたんはちょっと長いかもな。"
                return "LLMが雑談で答えた文"

            def generate_structured_json(self, request) -> dict[str, object]:  # type: ignore[no-untyped-def]
                return {}

        with tempfile.TemporaryDirectory() as tmp:
            llm = PoetLLM()
            settings = Settings(
                llm_enabled=True,
                audio_enabled=False,
                decision_policy="py_trees",
                memory_enabled=True,
                memory_dir=Path(tmp) / "mem",
            )
            service = DogidoService(settings)
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
            sid = session.session_id
            sess = service.sessions[sid]
            sess.machine.llm = llm
            service.llm = llm
            emission = _emission(text="そらまぶし\nくさむらにうかぶ\nくろいせきたん")
            service._open_haiku_workshop(sess, emission, entry_id="h_soft", now=emission.created_at)

            def _event(seq: int, user_text: str, *, seconds: int = 5) -> GameEvent:
                return GameEvent(
                    schema_version="2026-05-24",
                    adapter="test",
                    observed_at=emission.created_at + timedelta(seconds=seconds),
                    sequence=seq,
                    event=EventDescriptor(
                        name=EventName.STATUS_SNAPSHOT,
                        source_kind=SourceKind.SYSTEM,
                        priority_hint=PriorityHint.BACKGROUND,
                        certainty=Certainty.HIGH,
                    ),
                    player=PlayerState(
                        name="p",
                        position=Position(x=0, y=64, z=0),
                        dimension="minecraft:overworld",
                    ),
                    world=WorldState(
                        time_phase=TimePhase.DAY,
                        weather=Weather.CLEAR,
                        biome="plains",
                        local_light=15,
                        sky_visible=True,
                    ),
                    meta=MetaState(user_text=user_text),
                )

            # Stage1 マーカー hit → critique_forced は定型（poet ではない）でも chat に落ちない
            r1 = service.process_event(
                _event(2, "なかなかいいねでも黒い石炭がちょっと長いかも", seconds=5),
                session_id=sid,
            )
            speeches1 = [a.text for a in r1.actions if a.layer == "speech" and a.text]
            self.assertEqual(len(speeches1), 1)
            self.assertIn("余白", speeches1[0])
            self.assertNotIn("player_chat", llm.kinds)
            self.assertTrue(is_open(sess.haiku_workshop))
            self.assertEqual(sess.haiku_workshop.drift_count if sess.haiku_workshop else -1, 0)

            # Stage4: other_haiku → poet leaf
            r2 = service.process_event(
                _event(3, "黒石炭とかにしたらいいんじゃない?", seconds=10),
                session_id=sid,
            )
            speeches2 = [a.text for a in r2.actions if a.layer == "speech" and a.text]
            self.assertEqual(len(speeches2), 1)
            self.assertIn("haiku_workshop_reply", llm.kinds)
            self.assertIn("長いかも", speeches2[0])
            self.assertTrue(is_open(sess.haiku_workshop), msg="2回添削でも drift close しない")
            self.assertNotIn("player_chat", llm.kinds)

            # soft_default（マーカー外の読み訂正）→ poet leaf
            r3 = service.process_event(
                _event(4, "いや土ぬくしだよ", seconds=15),
                session_id=sid,
            )
            speeches3 = [a.text for a in r3.actions if a.layer == "speech" and a.text]
            self.assertEqual(len(speeches3), 1)
            self.assertEqual(llm.kinds.count("haiku_workshop_reply"), 2)
            self.assertTrue(is_open(sess.haiku_workshop))
            self.assertNotIn("player_chat", llm.kinds)

    def test_hard_off_topic_can_drift(self) -> None:
        """Stage2: 松明など明確な別件は chat 側で drift しうる。"""
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                llm_enabled=False,
                audio_enabled=False,
                decision_policy="py_trees",
                memory_enabled=True,
                memory_dir=Path(tmp) / "mem",
            )
            service = DogidoService(settings)
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
            sid = session.session_id
            sess = service.sessions[sid]
            emission = _emission()
            service._open_haiku_workshop(sess, emission, entry_id="h_off", now=emission.created_at)
            from dogido_server.player_input.routing import route_player_input

            event = GameEvent(
                schema_version="2026-05-24",
                adapter="test",
                observed_at=emission.created_at + timedelta(seconds=5),
                sequence=2,
                event=EventDescriptor(
                    name=EventName.STATUS_SNAPSHOT,
                    source_kind=SourceKind.SYSTEM,
                    priority_hint=PriorityHint.BACKGROUND,
                    certainty=Certainty.HIGH,
                ),
                player=PlayerState(
                    name="p",
                    position=Position(x=0, y=64, z=0),
                    dimension="minecraft:overworld",
                ),
                world=WorldState(
                    time_phase=TimePhase.DAY,
                    weather=Weather.CLEAR,
                    biome="plains",
                    local_light=15,
                    sky_visible=True,
                ),
                meta=MetaState(user_text="松明ある？"),
            )
            sess.machine.player_input = route_player_input("松明ある？")
            actions = service._haiku_workshop_actions(sess, event)
            self.assertEqual(actions, [])
            # miss 後に chat speech があると drift
            service._note_workshop_after_actions(
                sess,
                event,
                [AudioAction(layer="speech", interrupt=False, text="松明は持ってるで")],
            )
            self.assertTrue(is_open(sess.haiku_workshop))
            self.assertEqual(sess.haiku_workshop.drift_count if sess.haiku_workshop else -1, 1)

    def test_clear_lessons_without_workshop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                llm_enabled=False,
                audio_enabled=False,
                decision_policy="py_trees",
                memory_enabled=True,
                memory_dir=Path(tmp) / "mem",
            )
            service = DogidoService(settings)
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
            sid = session.session_id
            sess = service.sessions[sid]
            store = MemoryStore(Path(tmp) / "mem")
            store.save_haiku_lesson(
                lesson_type="compress",
                note="要素を少し絞って余白を残すとよい",
                polarity="tighten",
            )
            self.assertTrue(store.list_recent_haiku_lessons(limit=3))
            event = GameEvent(
                schema_version="2026-05-24",
                adapter="test",
                observed_at=datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
                sequence=4,
                event=EventDescriptor(
                    name=EventName.STATUS_SNAPSHOT,
                    source_kind=SourceKind.SYSTEM,
                    priority_hint=PriorityHint.BACKGROUND,
                    certainty=Certainty.HIGH,
                ),
                player=PlayerState(
                    name="p",
                    position=Position(x=0, y=64, z=0),
                    dimension="minecraft:overworld",
                ),
                world=WorldState(
                    time_phase=TimePhase.DAY,
                    weather=Weather.CLEAR,
                    biome="plains",
                    local_light=15,
                    sky_visible=True,
                ),
                meta=MetaState(user_text="もう気にせんで"),
            )
            sess.machine.player_input = __import__(
                "dogido_server.player_input", fromlist=["route_player_input"]
            ).route_player_input("もう気にせんで")
            actions = service._memory_input_actions(sess, event)
            self.assertEqual(1, len(actions))
            self.assertIn("気にせんでええ", actions[0].text)
            self.assertEqual(MemoryStore(Path(tmp) / "mem").list_recent_haiku_lessons(limit=3), [])

    def test_conversational_revise_closes_workshop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                llm_enabled=False,
                audio_enabled=False,
                decision_policy="py_trees",
                memory_enabled=True,
                memory_dir=Path(tmp) / "mem",
            )
            service = DogidoService(settings)
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
            sid = session.session_id
            sess = service.sessions[sid]
            emission = _emission()
            sess.last_haiku_emission = emission
            service._open_haiku_workshop(sess, emission, entry_id="h_test", now=emission.created_at)
            event = GameEvent(
                schema_version="2026-05-24",
                adapter="test",
                observed_at=emission.created_at + timedelta(seconds=5),
                sequence=3,
                event=EventDescriptor(
                    name=EventName.STATUS_SNAPSHOT,
                    source_kind=SourceKind.SYSTEM,
                    priority_hint=PriorityHint.BACKGROUND,
                    certainty=Certainty.HIGH,
                ),
                player=PlayerState(
                    name="p",
                    position=Position(x=0, y=64, z=0),
                    dimension="minecraft:overworld",
                ),
                world=WorldState(
                    time_phase=TimePhase.DAY,
                    weather=Weather.CLEAR,
                    biome="plains",
                    local_light=15,
                    sky_visible=True,
                ),
                meta=MetaState(user_text="こう直して: あさひさす / むらのどう / あかがね"),
            )
            sess.machine.player_input = __import__(
                "dogido_server.player_input", fromlist=["route_player_input"]
            ).route_player_input("こう直して: あさひさす / むらのどう / あかがね")
            actions = service._haiku_workshop_actions(sess, event)
            self.assertEqual(1, len(actions))
            self.assertIn("覚えといた", actions[0].text)
            self.assertIsNone(sess.haiku_workshop)


if __name__ == "__main__":
    unittest.main()
