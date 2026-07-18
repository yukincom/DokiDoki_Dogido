"""村人 profession / 日課 / カタログマージ。"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from dogido_server.entry_catalog import resolve_mob_catalog_entry
from dogido_server.models import (
    Certainty,
    Direction,
    EventDescriptor,
    EventName,
    GameEvent,
    PassiveMob,
    PlayerState,
    Position,
    WorldState,
)
from dogido_server.state_machine import DogidoStateMachine
from dogido_server.state_machine.villager_schedule import (
    resolve_villager_role,
    resolve_villager_schedule,
    should_suppress_ambient_for_sleep,
    villager_schedule_ja,
)
from dogido_server.config import Settings


class VillagerScheduleTests(unittest.TestCase):
    def test_roles(self) -> None:
        self.assertEqual(resolve_villager_role(is_baby=True, profession="farmer"), "child")
        self.assertEqual(resolve_villager_role(is_baby=False, profession="none"), "unemployed")
        self.assertEqual(resolve_villager_role(is_baby=False, profession="nitwit"), "unemployed")
        self.assertEqual(resolve_villager_role(is_baby=False, profession="farmer"), "employed")

    def test_employed_work_and_sleep(self) -> None:
        self.assertEqual(
            resolve_villager_schedule(3000, is_baby=False, profession="farmer"),
            "work",
        )
        self.assertEqual(
            resolve_villager_schedule(13000, is_baby=False, profession="farmer"),
            "sleep",
        )
        self.assertTrue(should_suppress_ambient_for_sleep("sleep"))
        self.assertEqual(villager_schedule_ja("work"), "仕事中")

    def test_child_play_band(self) -> None:
        self.assertEqual(
            resolve_villager_schedule(3000, is_baby=True, profession="none"),
            "play",
        )
        self.assertEqual(
            resolve_villager_schedule(7000, is_baby=True, profession="none"),
            "wander",
        )

    def test_unemployed_no_work(self) -> None:
        self.assertEqual(
            resolve_villager_schedule(3000, is_baby=False, profession="none"),
            "wander",
        )
        self.assertEqual(
            resolve_villager_schedule(9500, is_baby=False, profession="nitwit"),
            "gather",
        )


class VillagerCatalogTests(unittest.TestCase):
    def test_farmer_label_and_tags(self) -> None:
        entry = resolve_mob_catalog_entry("villager", profession="farmer", is_baby=False)
        assert entry is not None
        self.assertEqual(entry["label"], "農民")
        self.assertEqual(entry.get("job_site"), "composter")
        poetic = entry["poetic"]
        self.assertIn("麦わら帽子", poetic.get("visual_tags", []))

    def test_nitwit_is_neet_not_nitwit_word(self) -> None:
        entry = resolve_mob_catalog_entry("villager", profession="nitwit")
        assert entry is not None
        self.assertEqual(entry["label"], "ニート")
        self.assertNotIn("ニット", entry["label"])

    def test_none_is_job_seeker(self) -> None:
        entry = resolve_mob_catalog_entry("villager", profession="none")
        assert entry is not None
        self.assertEqual(entry["label"], "求職者")

    def test_baby_overrides_profession(self) -> None:
        entry = resolve_mob_catalog_entry("villager", profession="farmer", is_baby=True)
        assert entry is not None
        self.assertEqual(entry["label"], "子供")


class VillagerAmbientIntegrationTests(unittest.TestCase):
    def test_ambient_details_include_schedule_and_skip_sleep(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False, llm_enabled=False))
        farmer = PassiveMob(
            type="villager",
            distance=5.0,
            profession="farmer",
            is_baby=False,
            direction=Direction(horizontal="front", vertical="same"),
        )
        # work band
        event_work = GameEvent(
            schema_version="2026-05-24",
            adapter="test",
            observed_at=datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
            sequence=1,
            event=EventDescriptor(
                name=EventName.STATUS_SNAPSHOT,
                source_kind="system",
                priority_hint="background",
                certainty=Certainty.HIGH,
            ),
            player=PlayerState(name="p", position=Position(x=0, y=64, z=0)),
            world=WorldState(time_phase="day", time_of_day=4000, biome="plains"),
            passive_mobs=[farmer],
        )
        line = machine._render_ambient_mob_line(event_work, [farmer])
        self.assertIsNotNone(line)
        # fallback path when llm off still returns catalog line
        self.assertTrue(line)

        # sleep: next target should skip
        event_sleep = event_work.model_copy(
            update={"world": WorldState(time_phase="night", time_of_day=13000, biome="plains")}
        )
        target = machine._next_ambient_mob_target([farmer], event_sleep.observed_at, event_sleep)
        self.assertIsNone(target)


if __name__ == "__main__":
    unittest.main()
