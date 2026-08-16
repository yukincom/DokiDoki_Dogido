from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace

from fastapi.testclient import TestClient

from dogido_server.app import create_app
from dogido_server.config import Settings


def _threat_event(*, sequence: int = 1001) -> dict[str, object]:
    return {
        "schema_version": "2026-05-24",
        "game": "minecraft-java",
        "adapter": "dogido-fabric-client",
        "observed_at": "2026-05-25T21:10:01+09:00",
        "sequence": sequence,
        "event": {
            "name": "threat_approaching",
            "source_kind": "visual",
            "priority_hint": "urgent",
            "certainty": "high",
        },
        "player": {"name": "main_player"},
        "world": {
            "time_phase": "night",
            "danger_darkness_score": 0.8,
            "sky_visible": True,
            "enclosure_score": 0.05,
            "biome": "plains",
        },
        "visual_threats": [
            {
                "type": "creeper",
                "distance": 5.8,
                "direction": {"horizontal": "back", "vertical": "same"},
                "approaching": True,
                "certainty": "high",
            }
        ],
        "combat": {
            "recent_hostile_visual_ms": 100,
            "hostiles_within_7": 1,
            "hostiles_within_10": 1,
            "combat_active_hint": True,
        },
    }


class AppTests(unittest.TestCase):
    def setUp(self) -> None:
        settings = Settings(audio_enabled=False)
        self.client = TestClient(create_app(settings))

    def test_healthz(self) -> None:
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_voice_input_context_switches_with_workshop(self) -> None:
        response = self.client.get("/api/v1/voice-input/context")
        self.assertEqual(
            response.json(),
            {"prompt_mode": "normal", "session_id": None},
        )

        self.client.post(
            "/api/v1/adapter-sessions",
            json={
                "adapter_name": "dogido-fabric-client",
                "adapter_version": "test",
                "schema_version": "2026-05-24",
                "player_name": "main_player",
            },
        )
        service = self.client.app.state.service
        session = next(iter(service.sessions.values()))

        response = self.client.get("/api/v1/voice-input/context")
        self.assertEqual(response.json()["prompt_mode"], "normal")
        self.assertEqual(response.json()["session_id"], session.session_id)

        session.haiku_workshop = SimpleNamespace(open=True)
        response = self.client.get("/api/v1/voice-input/context")
        self.assertEqual(response.json()["prompt_mode"], "haiku_workshop")

        session.haiku_workshop.open = False
        response = self.client.get("/api/v1/voice-input/context")
        self.assertEqual(response.json()["prompt_mode"], "normal")

    def test_game_event_endpoint_accepts_threat(self) -> None:
        response = self.client.post(
            "/api/v1/game-events",
            json=_threat_event(),
        )

        body = response.json()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(body["state"]["mode"], "panic")
        self.assertTrue(body["outputs"]["callout_enqueued"])
        self.assertTrue(body["outputs"]["panic_cue_enqueued"])


class TrainingFeedbackEndpointTests(unittest.TestCase):
    def test_feedback_records_private_snapshot_and_last_key_wins(self) -> None:
        with TemporaryDirectory() as tmp:
            training_dir = Path(tmp) / "training"
            client = TestClient(
                create_app(
                    Settings(
                        audio_enabled=False,
                        llm_enabled=False,
                        memory_enabled=False,
                        training_data_dir=training_dir,
                    )
                )
            )
            session_response = client.post(
                "/api/v1/adapter-sessions",
                json={
                    "adapter_name": "dogido-fabric-client",
                    "adapter_version": "test",
                    "schema_version": "2026-05-24",
                    "player_name": "main_player",
                    "capabilities": ["training_feedback_flags"],
                },
            )
            session_id = session_response.json()["session_id"]
            headers = {"X-Dogido-Session-Id": session_id}
            event_response = client.post(
                "/api/v1/game-events",
                headers=headers,
                json=_threat_event(),
            )
            self.assertEqual(event_response.status_code, 202)

            good = client.post(
                "/api/v1/training-feedback",
                headers=headers,
                json={
                    "label": "good_example",
                    "client_event_id": "fabric-good-0001",
                    "pressed_at": "2026-08-16T12:00:00+09:00",
                },
            )
            self.assertTrue(good.json()["accepted"])
            self.assertEqual(good.json()["reason"], "recorded")

            review = client.post(
                "/api/v1/training-feedback",
                headers=headers,
                json={
                    "label": "needs_review",
                    "client_event_id": "fabric-review-0002",
                    "pressed_at": "2026-08-16T13:00:00+09:00",
                },
            )
            self.assertTrue(review.json()["accepted"])
            self.assertTrue(review.json()["replaced_previous"])
            self.assertEqual(review.json()["target_id"], good.json()["target_id"])

            duplicate = client.post(
                "/api/v1/training-feedback",
                headers=headers,
                json={
                    "label": "needs_review",
                    "client_event_id": "fabric-review-0003",
                    "pressed_at": "2026-08-16T14:00:00+09:00",
                },
            )
            self.assertTrue(duplicate.json()["duplicate"])

            stale = client.post(
                "/api/v1/training-feedback",
                headers=headers,
                json={
                    "label": "good_example",
                    "client_event_id": "fabric-stale-0004",
                    "pressed_at": "2026-08-16T12:30:00+09:00",
                },
            )
            self.assertFalse(stale.json()["accepted"])
            self.assertEqual(stale.json()["reason"], "stale_key_event")

            flags_path = training_dir / "inbox" / "evaluation_flags.jsonl"
            rows = [json.loads(line) for line in flags_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[-1]["label"], "needs_review")
            self.assertEqual(rows[-1]["supersedes_flag_id"], rows[0]["flag_id"])
            stored = flags_path.read_text(encoding="utf-8")
            self.assertNotIn("main_player", stored)
            self.assertNotIn('"position"', stored)
            self.assertEqual(rows[0]["target"]["context"]["world"]["biome"], "plains")
            self.assertTrue(rows[0]["target"]["output"]["actions"])

    def test_feedback_without_rateable_response_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            client = TestClient(
                create_app(
                    Settings(
                        audio_enabled=False,
                        llm_enabled=False,
                        memory_enabled=False,
                        training_data_dir=Path(tmp),
                    )
                )
            )
            response = client.post(
                "/api/v1/adapter-sessions",
                json={
                    "adapter_name": "dogido-fabric-client",
                    "adapter_version": "test",
                    "schema_version": "2026-05-24",
                    "player_name": "main_player",
                },
            )
            result = client.post(
                "/api/v1/training-feedback",
                headers={"X-Dogido-Session-Id": response.json()["session_id"]},
                json={
                    "label": "good_example",
                    "client_event_id": "fabric-good-empty",
                },
            )
            self.assertFalse(result.json()["accepted"])
            self.assertEqual(result.json()["reason"], "no_recent_target")

    def test_feedback_disabled_is_reported_without_capturing_targets(self) -> None:
        client = TestClient(
            create_app(
                Settings(
                    audio_enabled=False,
                    llm_enabled=False,
                    memory_enabled=False,
                    training_feedback_enabled=False,
                )
            )
        )
        response = client.post(
            "/api/v1/adapter-sessions",
            json={
                "adapter_name": "dogido-fabric-client",
                "adapter_version": "test",
                "schema_version": "2026-05-24",
                "player_name": "main_player",
            },
        )
        session_id = response.json()["session_id"]
        client.post(
            "/api/v1/game-events",
            headers={"X-Dogido-Session-Id": session_id},
            json=_threat_event(),
        )
        result = client.post(
            "/api/v1/training-feedback",
            headers={"X-Dogido-Session-Id": session_id},
            json={
                "label": "good_example",
                "client_event_id": "fabric-disabled-0001",
            },
        )
        self.assertFalse(result.json()["accepted"])
        self.assertEqual(result.json()["reason"], "feedback_disabled")
        service = client.app.state.service
        self.assertIsNone(service.sessions[session_id].latest_training_target)


if __name__ == "__main__":
    unittest.main()
