from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from dogido_server.catalog_readings import (
    clear_overlay_for_tests,
    format_label_with_reading,
    haiku_reading_terms,
    resolve_reading,
)
from dogido_server.config import Settings
from dogido_server.entry_catalog import biome_label_with_reading, biome_reading
from dogido_server.memory import MemoryStore
from dogido_server.memory_types import HaikuEmission
from dogido_server.models import (
    Certainty,
    EventDescriptor,
    EventName,
    GameEvent,
    MetaState,
    PlayerState,
    Position,
    PriorityHint,
    SourceKind,
    Weather,
    WorldState,
)
from dogido_server.player_input.guardrails import (
    asks_haiku_recall,
    extract_reading_correction,
    extract_revised_haiku,
)
from dogido_server.player_input.routing import route_player_input
from dogido_server.service import DogidoService
from dogido_server.state_machine import DogidoStateMachine
from dogido_server.state_machine.haiku_context import SceneContext


def make_snapshot(
    observed_at: datetime,
    *,
    sequence: int,
    biome: str = "meadow",
    user_text: str | None = None,
) -> GameEvent:
    return GameEvent(
        schema_version="2026-05-24",
        adapter="test-adapter",
        observed_at=observed_at,
        sequence=sequence,
        event=EventDescriptor(
            name=EventName.STATUS_SNAPSHOT,
            source_kind=SourceKind.SYSTEM,
            priority_hint=PriorityHint.BACKGROUND,
            certainty=Certainty.HIGH,
        ),
        player=PlayerState(
            name="main_player",
            position=Position(x=0, y=64, z=12),
            dimension="minecraft:overworld",
            held_item="minecraft:torch",
        ),
        world=WorldState(
            time_phase="day",
            weather=Weather.CLEAR,
            biome=biome,
            structure=None,
            local_light=15,
            sky_visible=True,
            danger_darkness_score=0.0,
        ),
        meta=MetaState(user_text=user_text),
    )


class CatalogReadingTest(unittest.TestCase):
    def setUp(self) -> None:
        clear_overlay_for_tests()

    def tearDown(self) -> None:
        clear_overlay_for_tests()

    def test_meadow_has_kusachi_reading(self) -> None:
        self.assertEqual(biome_reading("meadow"), "くさち")
        self.assertEqual(biome_label_with_reading("meadow"), "草地（くさち）")

    def test_overlay_forbids_wrong_reading(self) -> None:
        from dogido_server.catalog_readings import apply_overlay_correction

        apply_overlay_correction(surface="草地", reading="くさち", wrong_reading="そうち")
        allowed, forbidden = haiku_reading_terms(["草地"], catalog_readings={"草地": "くさち"})
        self.assertIn("くさち", allowed)
        self.assertIn("そうち", forbidden)
        self.assertEqual(resolve_reading("草地"), "くさち")
        self.assertEqual(format_label_with_reading("草地"), "草地（くさち）")


class PlayerInputHaikuFeedbackTest(unittest.TestCase):
    def test_extract_reading_and_revise(self) -> None:
        self.assertEqual(extract_reading_correction("草地はくさち"), ("草地", "くさち", None))
        self.assertEqual(
            extract_reading_correction("そうちじゃなくてくさち"),
            ("そうち", "くさち", "そうち"),
        )
        revised = extract_revised_haiku("直し: あさのひ / くさちにうし / のんびりと")
        self.assertEqual(revised, "あさのひ\nくさちにうし\nのんびりと")
        self.assertTrue(asks_haiku_recall("草地の句思い出して"))

    def test_route_sets_feedback_flags(self) -> None:
        ctx = route_player_input("読み: 草地=くさち")
        self.assertIsNotNone(ctx.reading_correction)
        assert ctx.reading_correction is not None
        self.assertEqual(ctx.reading_correction.surface, "草地")
        self.assertEqual(ctx.reading_correction.reading, "くさち")

        ctx2 = route_player_input("直し: あああ / いいい / ううう")
        self.assertEqual(ctx2.revised_haiku_text, "あああ\nいいい\nううう")


class HaikuFeedbackMemoryTest(unittest.TestCase):
    def setUp(self) -> None:
        clear_overlay_for_tests()

    def tearDown(self) -> None:
        clear_overlay_for_tests()

    def test_save_feedback_pair_and_search(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(Path(tmp))
            emission = HaikuEmission(
                created_at=datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc),
                text="そうちにて\nうしがのんびり\nくさをはむ",
                preface="ここで一句。",
                interpretation="草地の牛",
                biome="meadow",
                structure=None,
                time_phase="day",
                dimension="minecraft:overworld",
                event_sequence=10,
            )
            rev = store.save_haiku_feedback(
                emission,
                revised_text="くさちにて\nうしがのんびり\nくさをはむ",
            )
            self.assertEqual(rev["original_text"], emission.text)
            self.assertIn("くさちにて", rev["revised_text"])
            self.assertEqual(len(store.list_haiku_entries()), 1)
            hits = store.search_haiku_memory(biome="meadow")
            self.assertGreaterEqual(len(hits), 1)
            self.assertEqual(hits[0]["kind"], "revision")
            self.assertEqual(hits[0]["revised_text"], rev["revised_text"])

    def test_reading_correction_persists_and_applies(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(Path(tmp))
            store.save_reading_correction(
                surface="草地",
                reading="くさち",
                wrong_reading="そうち",
                source="biome:meadow",
            )
            self.assertEqual(resolve_reading("草地"), "くさち")
            rows = store._read_jsonl(store.catalog_corrections_path)
            self.assertEqual(rows[0]["reading"], "くさち")
            self.assertIn("そうち", rows[0]["forbidden_readings"])


class HaikuFeedbackServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        clear_overlay_for_tests()

    def tearDown(self) -> None:
        clear_overlay_for_tests()

    def test_service_reading_revise_and_recall(self) -> None:
        with TemporaryDirectory() as tmp:
            settings = Settings(
                audio_enabled=False,
                llm_enabled=False,
                decision_policy="py_trees",
                memory_dir=Path(tmp),
                haiku_interval_ms=300000,
                haiku_quiet_time_ms=300000,
            )
            service = DogidoService(settings)
            base = datetime(2026, 7, 14, 15, 0, tzinfo=timezone.utc)

            service.process_event(make_snapshot(base, sequence=1, biome="meadow"))
            emitted = service.process_event(
                make_snapshot(base + timedelta(seconds=301), sequence=2, biome="meadow")
            )
            self.assertTrue(
                any(action.text and "ここで一句" in (action.text or "") for action in emitted.actions)
            )

            fixed = service.process_event(
                make_snapshot(
                    base + timedelta(seconds=302),
                    sequence=3,
                    biome="meadow",
                    user_text="草地はくさち",
                )
            )
            self.assertTrue(any("くさち" in (a.text or "") for a in fixed.actions))
            self.assertEqual(resolve_reading("草地"), "くさち")

            revised = service.process_event(
                make_snapshot(
                    base + timedelta(seconds=303),
                    sequence=4,
                    biome="meadow",
                    user_text="直し: くさちにて / うしがねむる / ひるやすみ",
                )
            )
            self.assertTrue(any("覚えといた" in (a.text or "") for a in revised.actions))
            assert service.memory is not None
            self.assertEqual(len(service.memory.list_haiku_revisions()), 1)

            recalled = service.process_event(
                make_snapshot(
                    base + timedelta(seconds=304),
                    sequence=5,
                    biome="meadow",
                    user_text="草地の句思い出して",
                )
            )
            self.assertTrue(any("覚えとる句" in (a.text or "") for a in recalled.actions))
            self.assertTrue(any("くさちにて" in (a.text or "") for a in recalled.actions))

    def test_haiku_context_includes_biome_reading(self) -> None:
        machine = DogidoStateMachine(Settings(llm_enabled=False, decision_policy="py_trees"))
        event = make_snapshot(datetime(2026, 7, 14, tzinfo=timezone.utc), sequence=1, biome="meadow")
        context = machine._haiku_context(event)
        self.assertIn("くさち", context.biome_label)
        details = machine._haiku_constraint_details(event, SceneContext())
        assert details is not None
        self.assertIn("くさち", details.get("allowed_terms") or [])


if __name__ == "__main__":
    unittest.main()


class HaikuTimeRangeTest(unittest.TestCase):
    def test_parse_this_month_and_past_month(self) -> None:
        from dogido_server.player_input.guardrails import parse_haiku_time_range

        now = datetime(2026, 7, 14, 15, 30, tzinfo=timezone.utc)
        since, until, label = parse_haiku_time_range("今月の句思い出して", now=now)
        self.assertEqual(label, "今月")
        assert since is not None and until is not None
        self.assertEqual(since.day, 1)
        self.assertEqual(since.month, 7)
        self.assertEqual(until, now.astimezone())

        since2, until2, label2 = parse_haiku_time_range("ここひと月の句", now=now)
        self.assertEqual(label2, "ここひと月")
        assert since2 is not None and until2 is not None
        self.assertAlmostEqual((until2 - since2).total_seconds(), 30 * 86400, delta=2)

    def test_search_filters_by_created_at(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(Path(tmp))
            old = HaikuEmission(
                created_at=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
                text="ごがつやで\nふるいことば\nのこりけり",
                preface="ここで一句。",
                interpretation=None,
                biome="plains",
                structure=None,
                time_phase="day",
                dimension="minecraft:overworld",
                event_sequence=1,
            )
            new = HaikuEmission(
                created_at=datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc),
                text="なつのかぜ\nくさちにうし\nねむりけり",
                preface="ここで一句。",
                interpretation=None,
                biome="meadow",
                structure=None,
                time_phase="day",
                dimension="minecraft:overworld",
                event_sequence=2,
            )
            store.save_agent_haiku(old)
            store.save_agent_haiku(new)
            now = datetime(2026, 7, 14, 15, 0, tzinfo=timezone.utc)
            month_start = now.astimezone().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            hits = store.search_haiku_memory(since=month_start, until=now, limit=5)
            texts = [h["original_text"] for h in hits]
            self.assertIn(new.text, texts)
            self.assertNotIn(old.text, texts)

            past = store.search_haiku_memory(since=now - timedelta(days=30), until=now, limit=5)
            past_texts = [h["original_text"] for h in past]
            self.assertIn(new.text, past_texts)
            self.assertNotIn(old.text, past_texts)

    def test_route_recall_query_has_time(self) -> None:
        ctx = route_player_input("今月の草地の句思い出して")
        self.assertTrue(ctx.asks_haiku_recall)
        self.assertEqual(ctx.haiku_recall_biome_hint, "meadow")
        assert ctx.haiku_recall_query is not None
        self.assertEqual(ctx.haiku_recall_query.time_label, "今月")
        self.assertEqual(ctx.haiku_recall_query.biome_id, "meadow")


class BiomeGroupRecallTest(unittest.TestCase):
    def test_catalog_groups_resolve_vague_places(self) -> None:
        from dogido_server.entry_catalog import biomes_in_groups, resolve_biome_place_from_text

        cold = resolve_biome_place_from_text("寒いところの句")
        self.assertIsNone(cold["biome_id"])
        self.assertEqual(set(cold["group_ids"]), {"cold", "snowy"})
        self.assertIn("snowy_taiga", cold["biome_ids"])
        self.assertIn("taiga", cold["biome_ids"])
        self.assertEqual(cold["place_label"], "寒いところ")

        dry = resolve_biome_place_from_text("乾燥帯")
        self.assertEqual(set(dry["group_ids"]), {"dry"})
        self.assertIn("desert", dry["biome_ids"])

        concrete = resolve_biome_place_from_text("雪のタイガで詠んだ句")
        self.assertEqual(concrete["biome_id"], "snowy_taiga")
        self.assertEqual(concrete["biome_ids"], frozenset({"snowy_taiga"}))

        self.assertEqual(len(biomes_in_groups(["nether"])), 5)

    def test_search_by_group_expanded_biome_ids(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(Path(tmp))
            store.save_agent_haiku(
                HaikuEmission(
                    created_at=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
                    text="さばくかぜ\nかわききった\nすなのうみ",
                    preface="ここで一句。",
                    interpretation=None,
                    biome="desert",
                    structure=None,
                    time_phase="day",
                    dimension="minecraft:overworld",
                    event_sequence=1,
                )
            )
            store.save_agent_haiku(
                HaikuEmission(
                    created_at=datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc),
                    text="ゆきのやま\nしずかすぎて\nこごえそう",
                    preface="ここで一句。",
                    interpretation=None,
                    biome="snowy_taiga",
                    structure=None,
                    time_phase="day",
                    dimension="minecraft:overworld",
                    event_sequence=2,
                )
            )
            from dogido_server.entry_catalog import biomes_in_groups

            cold_ids = biomes_in_groups(["cold", "snowy"])
            hits = store.search_haiku_memory(biome_ids=cold_ids, limit=5)
            texts = [h["original_text"] for h in hits]
            self.assertIn("ゆきのやま\nしずかすぎて\nこごえそう", texts)
            self.assertNotIn("さばくかぜ\nかわききった\nすなのうみ", texts)

    def test_route_vague_place_and_time(self) -> None:
        ctx = route_player_input("ここひと月の寒いところの句思い出して")
        self.assertTrue(ctx.asks_haiku_recall)
        assert ctx.haiku_recall_query is not None
        self.assertEqual(ctx.haiku_recall_query.place_label, "寒いところ")
        self.assertIn("snowy", ctx.haiku_recall_query.group_ids)
        self.assertEqual(ctx.haiku_recall_query.time_label, "ここひと月")
        self.assertGreater(len(ctx.haiku_recall_query.biome_ids), 5)
