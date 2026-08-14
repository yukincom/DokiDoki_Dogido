"""サバイバル戦況 chat: 視認∪音・空はおらん・topic 誤マッチ・look 控えめ。"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from dogido_server.config import Settings
from dogido_server.entry_catalog import find_catalog_topics
from dogido_server.models import GameEvent
from dogido_server.player_chat_policy import (
    filter_usable_topic_hits,
    has_threat_presence_query,
    resolve_reply_stance,
)
from dogido_server.player_input import route_player_input
from dogido_server.player_input.guardrails import asks_about_sound
from dogido_server.state_machine import DogidoStateMachine
from dogido_server.state_machine.types import RecentHearingMemo


class PresenceQueryAndStanceTests(unittest.TestCase):
    def test_presence_query_detected(self) -> None:
        self.assertTrue(has_threat_presence_query("クリーパーはまだいますか?"))
        self.assertTrue(has_threat_presence_query("ゾンビの声聞こえてる?"))
        self.assertFalse(has_threat_presence_query("おはよう"))

    def test_sound_markers_include_voice(self) -> None:
        self.assertTrue(asks_about_sound("ゾンビの声聞こえてる?"))
        self.assertTrue(asks_about_sound("今の声は何?"))

    def test_bikkuri_does_not_match_bat(self) -> None:
        hits = find_catalog_topics("やばい、びっくりした")
        ids = [str(h.get("entry_id")) for h in hits]
        self.assertNotIn("bat", ids)

    def test_presence_without_observation_is_none_not_hypothesis(self) -> None:
        raw = find_catalog_topics("クリーパーはまだいますか?")
        usable = filter_usable_topic_hits(raw)
        self.assertTrue(usable)
        stance = resolve_reply_stance(
            has_visual_threats=False,
            topic_hits=raw,
            threat_summary="",
            user_text="クリーパーはまだいますか?",
            observed_ids=[],
        )
        self.assertEqual(stance, "none")

    def test_presence_with_hearing_observation_is_saw(self) -> None:
        raw = find_catalog_topics("クリーパーはまだいますか?")
        stance = resolve_reply_stance(
            has_visual_threats=False,
            topic_hits=raw,
            threat_summary="音メモ: クリーパーの音 前 far",
            user_text="クリーパーはまだいますか?",
            observed_ids=["creeper"],
        )
        self.assertEqual(stance, "saw")


class NarrationPresenceIntegrationTests(unittest.TestCase):
    def _machine(self) -> DogidoStateMachine:
        return DogidoStateMachine(Settings(llm_enabled=False, decision_policy="py_trees"))

    def _event(self, text: str, **kwargs: object) -> GameEvent:
        payload = {
            "schema_version": "2026-05-24",
            "adapter": "unit-test",
            "observed_at": datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc).isoformat(),
            "sequence": 1,
            "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high",
            },
            "player": {
                "name": "tester",
                "position": {"x": 0, "y": 64, "z": 0},
                "dimension": "minecraft:overworld",
            },
            "world": {
                "time_phase": "night",
                "weather": "clear",
                "biome": "plains",
                "local_light": 8,
                "sky_visible": True,
            },
            "meta": {"user_text": text},
        }
        payload.update(kwargs)
        return GameEvent.model_validate(payload)

    def test_presence_empty_does_not_claim_creeper_in_shadow(self) -> None:
        machine = self._machine()
        event = self._event("クリーパーはまだいますか?")
        machine.player_input = route_player_input("クリーパーはまだいますか?")
        text = machine._render_player_chat_reply(event)
        self.assertNotIn("壁の影", text)
        # fallback / 弱い否定寄り（LLM off は固定文のこともある）
        lowered = text
        self.assertFalse("またクリーパー" in lowered and "影" in lowered)

    def test_look_not_forced_on_presence_query(self) -> None:
        machine = self._machine()
        event = self._event(
            "クリーパーはまだいますか?",
            look_target={"kind": "block", "name": "dirt", "distance": 2.0},
        )
        machine.player_input = route_player_input("クリーパーはまだいますか?")
        # details 経路: look は指差し以外では空
        self.assertFalse(machine._player_chat_wants_look_answer("クリーパーはまだいますか?"))
        self.assertTrue(machine._player_chat_wants_look_answer("これは何かな"))
        text = machine._render_player_chat_reply(event)
        self.assertNotIn("土", text)

    def test_hearing_types_feed_presence(self) -> None:
        machine = self._machine()
        now = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
        machine.state.recent_hearing_memos = [
            RecentHearingMemo(
                kind="hostile",
                mob_type="zombie",
                label_ja="ゾンビ",
                direction="前",
                distance_band="near",
                heard_at=now,
                dedupe_key="hostile:zombie:前:near",
            )
        ]
        event = self._event("ゾンビの声聞こえてる?")
        machine.player_input = route_player_input("ゾンビの声聞こえてる?")
        types = machine._player_chat_hearing_mob_types(event)
        self.assertIn("zombie", types)
        summary = machine._player_chat_hearing_summary(event)
        self.assertIn("ゾンビ", summary)


if __name__ == "__main__":
    unittest.main()
