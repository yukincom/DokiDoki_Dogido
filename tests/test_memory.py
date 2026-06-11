from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from dogido_server.config import Settings
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
from dogido_server.service import DogidoService


def make_snapshot(
    observed_at: datetime,
    *,
    sequence: int,
    user_text: str | None = None,
    advancements: list[str] | None = None,
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
            time_phase="night",
            weather=Weather.CLEAR,
            biome="minecraft:snowy_taiga",
            structure=None,
            local_light=15,
            sky_visible=True,
            danger_darkness_score=0.0,
        ),
        meta=MetaState(user_text=user_text, advancements=advancements or []),
    )


class MemoryStoreTest(unittest.TestCase):
    def test_save_agent_haiku_uses_minimal_long_term_schema(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(Path(tmp))
            emission = HaikuEmission(
                created_at=datetime(2026, 6, 11, 17, 27, 1, tzinfo=timezone.utc),
                text="ゆきのこもれ\nやみにはかぶる\nそらのとびら",
                preface="ここで一句。",
                interpretation="雪のタイガの冷たい夜、手元のエンダーポータルフレームが、別の次元への扉を暗示している。",
                biome="snowy_taiga",
                structure=None,
                time_phase="night",
                dimension="minecraft:overworld",
                event_sequence=4919,
            )

            entry, created = store.save_agent_haiku(emission)
            duplicate, duplicated_created = store.save_agent_haiku(emission)

            self.assertTrue(created)
            self.assertFalse(duplicated_created)
            self.assertEqual(entry["id"], duplicate["id"])
            self.assertEqual(entry["kind"], "agent_haiku")
            self.assertEqual(entry["author"], "dogido")
            self.assertNotIn("saved_at", entry)
            self.assertEqual(entry["world"]["biome"], "snowy_taiga")
            self.assertEqual(store.list_haiku_entries(), [entry])

    def test_record_progress_keeps_only_selected_advancements(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(Path(tmp))
            changed = store.record_progress(
                "main_player",
                ["minecraft:story/root", "minecraft:story/mine_diamond"],
                datetime(2026, 6, 11, 18, 0, tzinfo=timezone.utc),
            )

            profile = store.load_profile()
            self.assertTrue(changed)
            self.assertTrue(profile["progress"]["story/mine_diamond"]["unlocked"])
            self.assertFalse(profile["progress"]["nether/root"]["unlocked"])


class MemoryServiceTest(unittest.TestCase):
    def test_service_logs_haiku_and_saves_last_haiku_from_player_input(self) -> None:
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
            base_time = datetime(2026, 6, 11, 17, 0, tzinfo=timezone.utc)

            service.process_event(make_snapshot(base_time, sequence=1))
            emitted = service.process_event(make_snapshot(base_time + timedelta(seconds=301), sequence=2))
            saved = service.process_event(
                make_snapshot(base_time + timedelta(seconds=302), sequence=3, user_text="今の句保存して")
            )

            self.assertTrue(any(action.text and "ここで一句" in action.text for action in emitted.actions))
            self.assertTrue(any(action.text == "今の句、保存したで。" for action in saved.actions))

            entries = service.list_haiku_memory()
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["author"], "dogido")
            self.assertEqual(entries[0]["kind"], "agent_haiku")
            self.assertEqual(entries[0]["world"]["biome"], "snowy_taiga")

            short_events = service.memory._read_jsonl(service.memory.short_term_path)  # type: ignore[union-attr]
            self.assertTrue(any(row["type"] == "haiku_emitted" for row in short_events))
            self.assertTrue(any(row["type"] == "player_input" and row["text"] == "今の句保存して" for row in short_events))

    def test_service_saves_player_haiku_and_progress(self) -> None:
        with TemporaryDirectory() as tmp:
            settings = Settings(audio_enabled=False, llm_enabled=False, memory_dir=Path(tmp))
            service = DogidoService(settings)
            event = make_snapshot(
                datetime(2026, 6, 11, 18, 0, tzinfo=timezone.utc),
                sequence=10,
                user_text="川柳保存: ダイヤより/土の階段/ありがたい",
                advancements=["minecraft:nether/root", "end/elytra"],
            )

            result = service.process_event(event)

            self.assertTrue(any(action.text == "プレイヤーの川柳、保存したで。" for action in result.actions))
            entries = service.list_haiku_memory()
            self.assertEqual(entries[0]["author"], "player")
            self.assertEqual(entries[0]["kind"], "player_haiku")
            self.assertEqual(entries[0]["text"], "ダイヤより\n土の階段\nありがたい")
            profile = service.memory_profile()
            self.assertTrue(profile["progress"]["nether/root"]["unlocked"])
            self.assertTrue(profile["progress"]["end/elytra"]["unlocked"])
            self.assertFalse(profile["progress"]["story/mine_diamond"]["unlocked"])


if __name__ == "__main__":
    unittest.main()
