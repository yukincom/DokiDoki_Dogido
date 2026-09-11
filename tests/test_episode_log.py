from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from dogido_server.config import Settings
from dogido_server.episode_log import EPISODE_SCHEMA_VERSION, EpisodeRecorder
from dogido_server.models import GameEvent, OutputFlags, SmellObservation
from dogido_server.service import DogidoService
from dogido_server.state_machine import AudioAction


def make_panic_event(*, sequence: int = 1) -> GameEvent:
    return GameEvent.model_validate(
        {
            "schema_version": "2026-05-24",
            "game": "minecraft-java",
            "adapter": "test-adapter",
            "observed_at": "2026-08-15T12:00:00+09:00",
            "sequence": sequence,
            "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high",
            },
            "player": {
                "name": "main_player",
                "position": {"x": 12.5, "y": 64.0, "z": -3.5},
                "health": 16.0,
                "hunger": 18,
                "dimension": "minecraft:overworld",
                "held_item": "minecraft:torch",
            },
            "world": {
                "time_phase": "night",
                "weather": "clear",
                "biome": "plains",
                "local_light": 3,
                "sky_visible": True,
                "danger_darkness_score": 0.8,
            },
            "visual_threats": [
                {
                    "type": "creeper",
                    "entity_id": "creeper-1",
                    "distance": 5.8,
                    "direction": {"horizontal": "back", "vertical": "same"},
                    "approaching": True,
                    "certainty": "high",
                }
            ],
            "inventory": {"minecraft:torch": 12, "minecraft:stone_sword": 1},
            "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": True,
            },
        }
    )


def make_quiet_event(*, sequence: int = 1) -> GameEvent:
    return GameEvent.model_validate(
        {
            "schema_version": "2026-05-24",
            "game": "minecraft-java",
            "adapter": "test-adapter",
            "observed_at": "2026-08-15T12:00:00+09:00",
            "sequence": sequence,
            "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high",
            },
            "player": {
                "name": "main_player",
                "position": {"x": 0.0, "y": 64.0, "z": 0.0},
                "dimension": "minecraft:overworld",
            },
            "world": {
                "time_phase": "day",
                "weather": "clear",
                "biome": "plains",
                "local_light": 15,
                "sky_visible": True,
                "danger_darkness_score": 0.0,
            },
            "combat": {"combat_active_hint": False},
        }
    )


def read_episode_rows(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class EpisodeRecorderIntegrationTest(unittest.TestCase):
    def test_service_records_panic_decision_as_one_versioned_json_line(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = DogidoService(
                Settings(
                    audio_enabled=False,
                    llm_enabled=False,
                    memory_enabled=True,
                    memory_dir=root,
                    decision_policy="py_trees",
                )
            )
            event = make_panic_event()

            processed = service.process_event(event)
            duplicate = service.process_event(event)

            self.assertTrue(processed.actions)
            self.assertTrue(duplicate.response.deduplicated)
            path = root / "eval" / "episodes.jsonl"
            rows = read_episode_rows(path)
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["schema_version"], EPISODE_SCHEMA_VERSION)
            self.assertEqual(row["trigger"]["event_name"], "threat_approaching")  # type: ignore[index]
            self.assertEqual(row["trigger"]["sequence"], 1)  # type: ignore[index]
            self.assertEqual(row["observation"]["visual_threats"]["nearest"]["type"], "creeper")  # type: ignore[index]
            self.assertEqual(row["observation"]["combat"]["hostiles_within_7"], 1)  # type: ignore[index]
            self.assertEqual(row["state_before"]["mode"], "normal")  # type: ignore[index]
            self.assertEqual(row["decision"]["mode_after"], "panic")  # type: ignore[index]
            self.assertTrue(row["decision"]["mode_changed"])  # type: ignore[index]
            self.assertIn("panic_cue", row["decision"]["layers"])  # type: ignore[index]
            self.assertGreater(row["action"]["count"], 0)  # type: ignore[index]
            self.assertEqual(row["result"]["status"], "actions_selected")  # type: ignore[index]
            self.assertTrue(row["result"]["output_flags"]["panic_cue_enqueued"])  # type: ignore[index]

    def test_service_records_quiet_no_action_decision(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = DogidoService(
                Settings(
                    audio_enabled=False,
                    llm_enabled=False,
                    memory_enabled=True,
                    memory_dir=root,
                )
            )

            processed = service.process_event(make_quiet_event())

            self.assertEqual(processed.actions, [])
            row = read_episode_rows(root / "eval" / "episodes.jsonl")[0]
            self.assertEqual(row["decision"]["kind"], "no_action")  # type: ignore[index]
            self.assertEqual(row["decision"]["mode_after"], "normal")  # type: ignore[index]
            self.assertEqual(row["action"]["items"], [])  # type: ignore[index]
            self.assertEqual(row["result"]["status"], "no_action")  # type: ignore[index]

    def test_episode_records_explicit_no_smell_observation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = DogidoService(
                Settings(
                    audio_enabled=False,
                    llm_enabled=False,
                    memory_enabled=True,
                    memory_dir=root,
                )
            )
            event = make_quiet_event().model_copy(
                update={"smell_observation": SmellObservation(status="none")}
            )

            service.process_event(event)

            row = read_episode_rows(root / "eval" / "episodes.jsonl")[0]
            smell = row["observation"]["smell_observation"]  # type: ignore[index]
            self.assertEqual("none", smell["status"])
            self.assertEqual("smell_policy_v1", smell["basis"])

    def test_state_before_is_captured_before_service_clears_stuck_haiku(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = DogidoService(
                Settings(
                    audio_enabled=False,
                    llm_enabled=False,
                    memory_enabled=True,
                    memory_dir=root,
                )
            )
            first_event = make_quiet_event(sequence=1)
            service.process_event(first_event)
            session = next(iter(service.sessions.values()))
            session.machine.state.pending_haiku_after_preface = True
            session.machine.state.pending_haiku_started_at = (
                first_event.observed_at - timedelta(seconds=30)
            )

            service.process_event(make_quiet_event(sequence=2))

            rows = read_episode_rows(root / "eval" / "episodes.jsonl")
            self.assertTrue(rows[1]["state_before"]["pending_haiku_after_preface"])  # type: ignore[index]
            self.assertFalse(session.machine.state.pending_haiku_after_preface)

    def test_recorder_failure_does_not_escape_best_effort_boundary(self) -> None:
        with TemporaryDirectory() as tmp:
            root_file = Path(tmp) / "not-a-directory"
            root_file.write_text("occupied", encoding="utf-8")
            recorder = EpisodeRecorder(root_file)

            recorded = recorder.record(
                event=make_quiet_event(),
                event_id="evt_test",
                session_id="ses_test",
                state_before={"mode": "normal"},
                mode_after="normal",
                combat_active=False,
                actions=[AudioAction(layer="speech", interrupt=False, text="test")],
                output_flags=OutputFlags(speech_enqueued=True),
                haiku_emitted=False,
                interpreted_user_text=None,
                recorded_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
            )

            self.assertFalse(recorded)

    def test_service_continues_when_recorder_itself_raises(self) -> None:
        class RaisingRecorder:
            def record(self, **_kwargs: object) -> bool:
                raise OSError("disk unavailable")

        with TemporaryDirectory() as tmp:
            service = DogidoService(
                Settings(
                    audio_enabled=False,
                    llm_enabled=False,
                    memory_enabled=True,
                    memory_dir=Path(tmp),
                )
            )
            service.episodes = RaisingRecorder()  # type: ignore[assignment]

            processed = service.process_event(make_panic_event())

            self.assertTrue(processed.response.accepted)
            self.assertTrue(processed.actions)


if __name__ == "__main__":
    unittest.main()
