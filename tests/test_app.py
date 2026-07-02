from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from dogido_server.app import create_app
from dogido_server.config import Settings


class AppTests(unittest.TestCase):
    def setUp(self) -> None:
        settings = Settings(audio_enabled=False)
        self.client = TestClient(create_app(settings))

    def test_healthz(self) -> None:
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_game_event_endpoint_accepts_threat(self) -> None:
        response = self.client.post(
            "/api/v1/game-events",
            json={
                "schema_version": "2026-05-24",
                "game": "minecraft-java",
                "adapter": "dogido-fabric-client",
                "observed_at": "2026-05-25T21:10:01+09:00",
                "sequence": 1001,
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
            },
        )

        body = response.json()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(body["state"]["mode"], "panic")
        self.assertTrue(body["outputs"]["callout_enqueued"])
        self.assertTrue(body["outputs"]["panic_cue_enqueued"])


if __name__ == "__main__":
    unittest.main()
