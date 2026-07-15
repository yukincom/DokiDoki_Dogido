"""reply_stance / policy（S1）。"""

from __future__ import annotations

import unittest

from dogido_server.entry_catalog import find_catalog_topics, format_catalog_topic_hints
from dogido_server.llm.prompts import build_messages
from dogido_server.llm.types import LeafGenerationRequest
from dogido_server.player_chat_policy import reply_policy_line, resolve_reply_stance


class ReplyStanceTests(unittest.TestCase):
    def test_visual_is_saw(self) -> None:
        self.assertEqual(
            resolve_reply_stance(has_visual_threats=True, topic_hits=[], user_text="何？"),
            "saw",
        )

    def test_topic_without_visual_is_hypothesis(self) -> None:
        hits = find_catalog_topics("なんだあのババア")
        self.assertTrue(hits)
        self.assertEqual(
            resolve_reply_stance(
                has_visual_threats=False,
                topic_hits=hits,
                user_text="なんだあのババア",
            ),
            "hypothesis",
        )

    def test_greeting_is_none(self) -> None:
        self.assertEqual(
            resolve_reply_stance(has_visual_threats=False, topic_hits=[], user_text="おはよう"),
            "none",
        )

    def test_vague_what_without_topic_is_clarify(self) -> None:
        self.assertEqual(
            resolve_reply_stance(has_visual_threats=False, topic_hits=[], user_text="あれ何？"),
            "clarify",
        )

    def test_threat_summary_視認_counts_as_saw(self) -> None:
        self.assertEqual(
            resolve_reply_stance(
                has_visual_threats=False,
                topic_hits=[],
                threat_summary="視認 ピリジャー が前 12マス",
                user_text="あれ",
            ),
            "saw",
        )

    def test_policy_lines_cover_all_stances(self) -> None:
        for stance in ("saw", "hypothesis", "clarify", "none"):
            line = reply_policy_line(stance)
            self.assertTrue(line)
        self.assertIn("おらへん", reply_policy_line("hypothesis"))
        self.assertIn("見えてへん", reply_policy_line("hypothesis"))


class PlayerChatPromptStanceTests(unittest.TestCase):
    def test_hypothesis_prompt_uses_policy_not_long_topic_rules(self) -> None:
        hits = find_catalog_topics("なんだあのババア")
        hints = format_catalog_topic_hints(hits)
        stance = resolve_reply_stance(
            has_visual_threats=False,
            topic_hits=hits,
            user_text="なんだあのババア",
        )
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
                    "reply_stance": stance,
                    "reply_policy": reply_policy_line(stance),
                    "place_context": "地表バイオーム: 平原 / 空間: 開けた地上（空が見える）",
                    "space_kind": "open_surface",
                },
            )
        )
        content = messages[1]["content"]
        self.assertEqual(stance, "hypothesis")
        self.assertIn("答え方スタンス: hypothesis", content)
        self.assertIn("【答え方】", content)
        self.assertIn("見えてへん", content)
        self.assertIn("おらへん", content)
        self.assertIn("ウィッチ", content)
        # 旧の長文 mode_hint / 場所ルールは載せない
        self.assertNotIn("平和時: 気さくで落ち着いて返す", content)
        self.assertNotIn("バイオーム名だけ見て地上の散歩と決めつけない", content)
        # 旧 topic 長文規則の重複を避ける
        self.assertNotIn("脅威メモ（視認）に載っていないとき", content)

    def test_none_stance_prompt_is_short_on_rules(self) -> None:
        messages = build_messages(
            LeafGenerationRequest(
                kind="player_chat",
                fallback_text="fallback",
                details={
                    "user_text": "おはよう",
                    "mode": "normal",
                    "biome": "平原",
                    "time_phase": "day",
                    "threat_summary": "とくになし",
                    "reply_stance": "none",
                    "reply_policy": reply_policy_line("none"),
                    "catalog_topic_hints": "",
                },
            )
        )
        content = messages[1]["content"]
        self.assertIn("答え方スタンス: none", content)
        self.assertNotIn("カタログからの話題ヒント", content)
        # 規則 bullet はスタンス中心で少なめ
        rule_lines = [line for line in content.splitlines() if line.startswith("- ")]
        self.assertLessEqual(len(rule_lines), 6)


if __name__ == "__main__":
    unittest.main()
