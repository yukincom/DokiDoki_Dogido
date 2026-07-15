"""汎用トピック照合 find_catalog_topics（PR-B）。"""

from __future__ import annotations

import unittest

from dogido_server.entry_catalog import find_catalog_topics, format_catalog_topic_hints
from dogido_server.llm.prompts import build_messages
from dogido_server.llm.types import LeafGenerationRequest


class FindCatalogTopicsTests(unittest.TestCase):
    def _ids(self, text: str, *, observed: list[str] | None = None) -> list[str]:
        hits = find_catalog_topics(text, observed_ids=observed or ())
        return [str(hit["entry_id"]) for hit in hits]

    def test_flag_maps_to_pillager(self) -> None:
        ids = self._ids("あいつら変な旗持ってる")
        self.assertIn("pillager", ids)
        self.assertEqual(ids[0], "pillager")

    def test_babaa_maps_to_witch(self) -> None:
        ids = self._ids("なんだあのババア")
        self.assertIn("witch", ids)
        self.assertEqual(ids[0], "witch")

    def test_ossan_can_hit_illagers(self) -> None:
        ids = self._ids("オッサンらが旗持ってる")
        self.assertIn("pillager", ids)

    def test_greeting_has_no_topic_hits(self) -> None:
        self.assertEqual(self._ids("おはよう"), [])
        self.assertEqual(self._ids("こんにちは"), [])

    def test_observed_boosts_matching_type(self) -> None:
        hits = find_catalog_topics("変な旗", observed_ids=["pillager"])
        self.assertTrue(hits)
        self.assertEqual(hits[0]["entry_id"], "pillager")
        self.assertTrue(hits[0]["observed"])

    def test_pointy_hat_purple_maps_to_witch(self) -> None:
        ids = self._ids("とんがり帽子の紫のやつ")
        self.assertIn("witch", ids)

    def test_outpost_structure_label(self) -> None:
        ids = self._ids("ピリジャー前哨基地ある？")
        self.assertIn("pillager_outpost", ids)

    def test_format_hints_mentions_match_and_observation(self) -> None:
        hits = find_catalog_topics("ババア", observed_ids=())
        text = format_catalog_topic_hints(hits)
        self.assertIn("ウィッチ", text)
        self.assertIn("ババア", text)
        self.assertIn("観測: なし", text)


class PlayerChatTopicPromptTests(unittest.TestCase):
    def test_prompt_includes_topic_hints_and_gap_rules(self) -> None:
        hints = format_catalog_topic_hints(find_catalog_topics("なんだあのババア"))
        messages = build_messages(
            LeafGenerationRequest(
                kind="player_chat",
                fallback_text="fallback",
                details={
                    "user_text": "なんだあのババア",
                    "mode": "normal",
                    "biome": "平原",
                    "time_phase": "day",
                    "threat_summary": "とくになし",
                    "hearing_summary": "",
                    "catalog_topic_hints": hints,
                },
            )
        )
        content = messages[1]["content"]
        self.assertIn("カタログからの話題ヒント", content)
        self.assertIn("ウィッチ", content)
        self.assertIn("否定して落とさない", content)
        self.assertIn("見えんけど", content)
        self.assertIn("捏造しない", content)

    def test_prompt_without_hints_still_forbids_denying_player(self) -> None:
        messages = build_messages(
            LeafGenerationRequest(
                kind="player_chat",
                fallback_text="fallback",
                details={
                    "user_text": "あれ何？",
                    "mode": "normal",
                    "biome": "平原",
                    "time_phase": "day",
                    "threat_summary": "とくになし",
                    "catalog_topic_hints": "",
                },
            )
        )
        content = messages[1]["content"]
        self.assertNotIn("カタログからの話題ヒント", content)
        self.assertIn("おらへん", content)
        self.assertIn("種名を当てず", content)


if __name__ == "__main__":
    unittest.main()
