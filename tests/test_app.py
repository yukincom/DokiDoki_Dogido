from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace

from fastapi.testclient import TestClient

from dogido_server.app import create_app
from dogido_server.config import Settings
from dogido_server.state_machine.types import AudioAction, SpeechReference


class AppTests(unittest.TestCase):
    def setUp(self) -> None:
        settings = Settings(audio_enabled=False)
        self.client = TestClient(create_app(settings))

    def test_healthz(self) -> None:
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_local_display_page_and_snapshot_are_available(self) -> None:
        page = self.client.get("/dogido")
        self.assertEqual(200, page.status_code)
        self.assertIn("ドギド発言履歴", page.text)
        self.assertIn("すべてコピー", page.text)
        self.assertIn("参考資料", page.text)
        self.assertIn("診断ログ", page.text)
        self.assertIn("全ログコピー", page.text)
        self.assertIn("文字を大きく", page.text)
        self.assertIn("conversationCopyText", page.text)
        self.assertIn("`> ${line}`", page.text)
        self.assertIn("--diagnostic-font-size: 1.25rem", page.text)
        self.assertIn("実行元", page.text)
        self.assertIn("Minecraft接続", page.text)
        self.assertEqual("no-store", page.headers["cache-control"])

        empty = self.client.get("/api/v1/display/snapshot")
        self.assertEqual(200, empty.status_code)
        self.assertEqual([], empty.json()["utterances"])
        self.assertEqual([], empty.json()["references"])
        self.assertEqual([], empty.json()["diagnostics"])
        self.assertIn("source_label_ja", empty.json()["runtime"])
        self.assertFalse(empty.json()["minecraft"]["connected"])

        service = self.client.app.state.service
        service.dispatch_actions(
            [
                AudioAction(
                    layer="speech",
                    interrupt=False,
                    text="枕詞は、特定の語を導く言葉やで。",
                    references=(
                        SpeechReference(
                            source_id="mext.example",
                            title_ja="中学校学習指導要領（国語）",
                            citation_label_ja="文部科学省",
                            locator="第2章",
                            url="https://www.mext.go.jp/example",
                            source_kind="official_guideline",
                        ),
                    ),
                )
            ],
            session_id="ses_display_test",
        )

        snapshot = self.client.get("/api/v1/display/snapshot").json()
        self.assertEqual(1, len(snapshot["utterances"]))
        self.assertEqual(
            "枕詞は、特定の語を導く言葉やで。",
            snapshot["utterances"][0]["text"],
        )
        self.assertEqual("text_only", snapshot["utterances"][0]["output_mode"])
        self.assertEqual("文部科学省", snapshot["references"][0]["citation_label_ja"])
        self.assertEqual(
            [],
            self.client.get(
                "/api/v1/display/snapshot",
                params={"session_id": "ses_other"},
            ).json()["utterances"],
        )

    def test_display_snapshot_reports_fresh_minecraft_adapter_separately(self) -> None:
        created = self.client.post(
            "/api/v1/adapter-sessions",
            json={
                "adapter_name": "dogido-fabric-client",
                "adapter_version": "test",
                "schema_version": "2026-05-24",
                "player_name": "main_player",
            },
        )
        self.assertEqual(201, created.status_code)

        snapshot = self.client.get("/api/v1/display/snapshot").json()
        self.assertTrue(snapshot["minecraft"]["connected"])
        self.assertEqual(1, snapshot["minecraft"]["active_sessions"])
        self.assertEqual(
            ["dogido-fabric-client test"],
            snapshot["minecraft"]["adapters"],
        )

    def test_display_snapshot_uses_existing_bearer_auth_boundary(self) -> None:
        client = TestClient(create_app(Settings(audio_enabled=False, auth_token="secret")))
        self.assertEqual(200, client.get("/dogido").status_code)
        self.assertEqual(401, client.get("/api/v1/display/snapshot").status_code)
        self.assertEqual(
            200,
            client.get(
                "/api/v1/display/snapshot",
                headers={"Authorization": "Bearer secret"},
            ).status_code,
        )
        self.assertEqual(
            401,
            client.post(
                "/api/v1/voice-input/diagnostics",
                json={"event": "stt_result", "recognized_text": "聞こえた"},
            ).status_code,
        )

    def test_voice_input_diagnostic_reaches_the_display_without_audio_data(self) -> None:
        response = self.client.post(
            "/api/v1/voice-input/diagnostics",
            json={
                "schema_version": 1,
                "event": "stt_rejected",
                "level": "warning",
                "recognized_text": "ごーーー",
                "reason": "noise_pattern",
                "duration_ms": 930,
            },
        )
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.json()["accepted"])

        snapshot = self.client.get("/api/v1/display/snapshot").json()
        self.assertEqual(1, snapshot["diagnostic_revision"])
        self.assertEqual(1, len(snapshot["diagnostics"]))
        entry = snapshot["diagnostics"][0]
        self.assertEqual("voice_input", entry["source"])
        self.assertIn("event=stt_rejected", entry["message"])
        self.assertIn("reason=noise_pattern", entry["message"])
        self.assertIn("text=ごーーー", entry["message"])
        self.assertNotIn("audio", entry)

    def test_runtime_application_log_is_captured_for_the_diagnostic_panel(self) -> None:
        app = create_app(Settings(audio_enabled=False), capture_diagnostics=True)
        client = TestClient(app)
        try:
            logging.getLogger("uvicorn.error").warning(
                "haiku_decision result=fallback reason=llm_unavailable"
            )
            diagnostics = client.get("/api/v1/display/snapshot").json()["diagnostics"]
        finally:
            app.state.diagnostic_capture.uninstall()

        self.assertEqual(1, len(diagnostics))
        self.assertIn("haiku_decision", diagnostics[0]["message"])
        self.assertIn("reason=llm_unavailable", diagnostics[0]["message"])

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
