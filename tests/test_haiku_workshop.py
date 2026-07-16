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
    maybe_close_for_time,
    open_from_emission,
    record_drift,
    render_workshop_reply,
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
        self.assertEqual(classify_workshop_intent("無理やり圧縮しすぎ"), "critique_forced")
        self.assertEqual(classify_workshop_intent("いい句やな"), "praise")
        self.assertEqual(classify_workshop_intent("もうええ"), "close")
        self.assertIsNone(classify_workshop_intent("おはよう"))
        self.assertIsNone(classify_workshop_intent("松明ある？"))

    def test_reply_includes_materials(self) -> None:
        ws = open_from_emission(_emission())
        reply = render_workshop_reply("ask_meaning", ws)
        self.assertIn("平原", reply)
        self.assertIn("あさひさす", reply)
        # H5.1: ガチ約束せず soft
        self.assertIn("ちょっと意識", reply)
        self.assertNotIn("外れんようにする", reply)

    def test_reply_soft_tones(self) -> None:
        ws = open_from_emission(_emission())
        self.assertIn("ちょっと意識", render_workshop_reply("critique_forced", ws))
        self.assertIn("外れすぎん", render_workshop_reply("critique_offscene", ws))
        praise = render_workshop_reply("praise", ws)
        self.assertIn("緩める", praise)

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
            # praise → 全軸 loosen
            store.save_haiku_lesson(lesson_type="*", note="", polarity="loosen", strength=0.0)
            self.assertEqual(store.list_recent_haiku_lessons(limit=3), [])


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
            self.assertIn("読みにく", actions[0].text)
            self.assertIn("平原", actions[0].text)
            # critique was written via service.memory
            self.assertTrue((Path(tmp) / "mem" / "long_term" / "haiku_critiques.jsonl").exists())
            self.assertTrue((Path(tmp) / "mem" / "long_term" / "haiku_lessons.jsonl").exists())
            lessons = MemoryStore(Path(tmp) / "mem").list_recent_haiku_lessons(limit=3)
            self.assertTrue(any("読みやす" in str(x.get("note")) for x in lessons))
            # hard 合流用の fragments があっても soft のまま
            self.assertTrue(all(x.get("polarity") != "loosen" for x in lessons))

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
