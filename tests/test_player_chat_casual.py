"""雑談3本柱: none を守る / 観測だけ短く / 5往復は触らない。"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from dogido_server.config import Settings
from dogido_server.entry_catalog import find_catalog_topics
from dogido_server.llm.prompts import build_messages
from dogido_server.llm.types import LeafGenerationRequest
from dogido_server.models import GameEvent
from dogido_server.player_chat_policy import (
    filter_usable_topic_hits,
    resolve_reply_stance,
)
from dogido_server.player_input import route_player_input
from dogido_server.state_machine import DogidoStateMachine


class UsableTopicAndStanceTests(unittest.TestCase):
    def test_big_tree_is_none_not_hypothesis(self) -> None:
        for text in ("大きい気があるね", "大きい木があるね"):
            raw = find_catalog_topics(text)
            usable = filter_usable_topic_hits(raw)
            self.assertEqual(usable, [], msg=text)
            stance = resolve_reply_stance(
                has_visual_threats=False,
                topic_hits=raw,
                user_text=text,
            )
            self.assertEqual(stance, "none", msg=text)

    def test_babaa_still_hypothesis(self) -> None:
        raw = find_catalog_topics("なんだあのババア")
        usable = filter_usable_topic_hits(raw)
        self.assertTrue(usable)
        self.assertEqual(usable[0]["entry_id"], "witch")
        stance = resolve_reply_stance(
            has_visual_threats=False,
            topic_hits=raw,
            user_text="なんだあのババア",
        )
        self.assertEqual(stance, "hypothesis")

    def test_flag_still_hypothesis(self) -> None:
        raw = find_catalog_topics("変な旗持ってる")
        usable = filter_usable_topic_hits(raw)
        self.assertTrue(any(h["entry_id"] == "pillager" for h in usable))
        self.assertEqual(
            resolve_reply_stance(
                has_visual_threats=False,
                topic_hits=raw,
                user_text="変な旗持ってる",
            ),
            "hypothesis",
        )

    def test_moshimoshi_is_none(self) -> None:
        self.assertEqual(
            resolve_reply_stance(
                has_visual_threats=False,
                topic_hits=[],
                user_text="もしもし",
            ),
            "none",
        )

    def test_are_nani_without_usable_is_clarify(self) -> None:
        self.assertEqual(
            resolve_reply_stance(
                has_visual_threats=False,
                topic_hits=find_catalog_topics("あれ何？"),
                user_text="あれ何？",
            ),
            "clarify",
        )


class NarrationCasualIntegrationTests(unittest.TestCase):
    def _machine(self) -> DogidoStateMachine:
        return DogidoStateMachine(Settings(llm_enabled=False, decision_policy="py_trees"))

    def _event(self, text: str, **kwargs: object) -> GameEvent:
        payload = {
            "schema_version": "2026-05-24",
            "adapter": "unit-test",
            "observed_at": datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc).isoformat(),
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
                "time_phase": "day",
                "weather": "clear",
                "biome": "forest",
                "local_light": 15,
                "sky_visible": True,
            },
            "meta": {"user_text": text},
        }
        payload.update(kwargs)
        return GameEvent.model_validate(payload)

    def test_big_ki_does_not_emit_polar_bear_skeleton(self) -> None:
        machine = self._machine()
        event = self._event("大きい気があるね")
        machine.player_input = route_player_input("大きい気があるね")
        text = machine._render_player_chat_reply(event)
        self.assertNotIn("シロクマ", text)
        self.assertNotIn("スニッファー", text)

    def test_babaa_skeleton_still_works(self) -> None:
        machine = self._machine()
        event = self._event("なんだあのババア")
        machine.player_input = route_player_input("なんだあのババア")
        text = machine._render_player_chat_reply(event)
        self.assertIn("ウィッチ", text)

    def test_none_prompt_has_no_topic_hints_for_big_tree(self) -> None:
        hits = find_catalog_topics("大きい木があるね")
        usable = filter_usable_topic_hits(hits)
        stance = resolve_reply_stance(
            has_visual_threats=False, topic_hits=hits, user_text="大きい木があるね"
        )
        self.assertEqual(stance, "none")
        self.assertEqual(usable, [])
        messages = build_messages(
            LeafGenerationRequest(
                kind="player_chat",
                fallback_text="f",
                details={
                    "user_text": "大きい木があるね",
                    "mode": "normal",
                    "time_phase": "day",
                    "reply_stance": "none",
                    "reply_policy": "雑談として自然に返す。",
                    "catalog_topic_hints": "",
                    "observation_summary": "",
                    "threat_summary": "とくになし",
                },
            )
        )
        content = messages[1]["content"]
        self.assertIn("答え方スタンス: none", content)
        self.assertNotIn("カタログからの話題ヒント", content)
        self.assertNotIn("シロクマ", content)

    def test_observation_summary_includes_passive_not_topic(self) -> None:
        machine = self._machine()
        event = self._event(
            "元気そうやな",
            passive_mobs=[
                {
                    "type": "salmon",
                    "distance": 6.0,
                    "direction": {"horizontal": "left"},
                }
            ],
        )
        machine.player_input = route_player_input("元気そうやな")
        # process で passive 記憶
        machine.process(event)
        summary = machine._player_chat_observation_summary(
            event,
            threat_summary="",
            hearing_summary="",
            passive_types=["salmon"],
        )
        self.assertIn("サケ", summary)
        self.assertIn("近くの生き物", summary)
        self.assertNotIn("シロクマ", summary)


if __name__ == "__main__":
    unittest.main()
