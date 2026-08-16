from __future__ import annotations

import unittest
from types import SimpleNamespace

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

    def test_game_event_response_carries_typed_select_hotbar_command(self) -> None:
        session = self.client.post(
            "/api/v1/adapter-sessions",
            json={
                "adapter_name": "dogido-fabric-client",
                "adapter_version": "test",
                "game": "minecraft-java",
                "schema_version": "2026-05-24",
                "player_name": "main_player",
                "capabilities": ["hotbar_slots"],
                "execution_capabilities": ["client.hotbar.select.v1"],
            },
        ).json()
        slots = [
            {
                "slot": 0,
                "item_id": "minecraft:stone_sword",
                "count": 1,
                "damage": 10,
                "max_damage": 131,
                "attack_damage": 5,
                "weapon_kind": "sword",
            },
            *[
                {"slot": slot, "count": 0, "weapon_kind": "empty"}
                for slot in range(1, 9)
            ],
        ]
        response = self.client.post(
            "/api/v1/game-events",
            headers={"X-Dogido-Session-Id": session["session_id"]},
            json={
                "schema_version": "2026-05-24",
                "game": "minecraft-java",
                "adapter": "dogido-fabric-client",
                "observed_at": "2026-08-15T12:00:00+09:00",
                "sequence": 1,
                "event": {
                    "name": "status_snapshot",
                    "source_kind": "system",
                    "priority_hint": "background",
                    "certainty": "high",
                },
                "player": {
                    "name": "main_player",
                    "hotbar": {"selected_slot": 8, "slots": slots},
                },
                "world": {"time_phase": "day", "danger_darkness_score": 0},
                "combat": {"combat_active_hint": False},
                "meta": {"user_text": "剣に持ち替えて"},
            },
        )

        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(body["commands"][0]["type"], "select_hotbar")
        self.assertEqual(body["commands"][0]["slot"], 0)
        self.assertEqual(body["commands"][0]["expected_item_id"], "minecraft:stone_sword")

    def test_unknown_explicit_session_is_rejected_for_adapter_reregistration(self) -> None:
        response = self.client.post(
            "/api/v1/game-events",
            headers={"X-Dogido-Session-Id": "ses_from_stopped_server"},
            json={
                "schema_version": "2026-05-24",
                "game": "minecraft-java",
                "adapter": "dogido-fabric-client",
                "observed_at": "2026-08-16T01:00:00+09:00",
                "sequence": 1,
                "event": {
                    "name": "status_snapshot",
                    "source_kind": "system",
                    "priority_hint": "background",
                    "certainty": "high",
                },
                "player": {"name": "main_player"},
                "world": {"time_phase": "day", "danger_darkness_score": 0},
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "unknown_session_id")
        self.assertNotIn("ses_from_stopped_server", self.client.app.state.service.sessions)


if __name__ == "__main__":
    unittest.main()
