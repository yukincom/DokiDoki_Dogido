"""E′: プロンプト縮退・tactics は観測時のみ。"""

from __future__ import annotations

import unittest

from dogido_server.entry_catalog import find_catalog_topics, format_catalog_topic_hints
from dogido_server.llm.prompts import build_messages
from dogido_server.llm.types import LeafGenerationRequest
from dogido_server.player_chat_policy import reply_policy_line, resolve_reply_stance


class PlayerChatEPrimePromptTests(unittest.TestCase):
    def _content(self, details: dict) -> str:
        messages = build_messages(
            LeafGenerationRequest(
                kind="player_chat",
                fallback_text="fallback",
                details=details,
            )
        )
        return messages[1]["content"]

    def test_hypothesis_prompt_is_compact_without_old_long_rules(self) -> None:
        hits = find_catalog_topics("なんだあのババア")
        stance = resolve_reply_stance(
            has_visual_threats=False, topic_hits=hits, user_text="なんだあのババア"
        )
        content = self._content(
            {
                "user_text": "なんだあのババア",
                "mode": "normal",
                "time_phase": "day",
                "threat_summary": "とくになし",
                "catalog_topic_hints": format_catalog_topic_hints(hits),
                "reply_stance": stance,
                "reply_policy": reply_policy_line(stance),
                "place_context": "地表バイオーム: 沼 / 空間: 開けた地上",
                "hearing_summary": "",
                "hearing_named_mobs": [],
                "nearby_hostile_types": [],
            }
        )
        rules = [line for line in content.splitlines() if line.startswith("- ")]
        self.assertLessEqual(len(rules), 5)
        self.assertIn("【答え方】", content)
        self.assertIn("hypothesis", content)
        # 旧 §2.5b 長文・空 hearing 常時行・mode 二重
        self.assertNotIn("平和時:", content)
        self.assertNotIn("バイオーム名だけ見て", content)
        self.assertNotIn("脅威メモ（視認）に載っていないとき", content)
        self.assertNotIn("はっきり拾えてへん", content)
        self.assertNotIn("キャラクターモードは", content)
        self.assertNotIn("音のメモ: （なし）", content)
        # tactics は仮説だけでは載らない
        self.assertNotIn("周囲の敵の性質メモ", content)
        self.assertNotIn("言ってよい短いヒント例", content)

    def test_observed_tactics_appear_only_with_nearby_types(self) -> None:
        content = self._content(
            {
                "user_text": "気をつけて",
                "mode": "panic",
                "character_mode": "battle",
                "time_phase": "night",
                "threat_summary": "視認 クリーパー が前 5マス",
                "has_visual_threats": True,
                "nearby_hostile_types": ["creeper"],
                "mob_tactics_notes": ["クリーパー: 近づくと爆発"],
                "safe_hints": ["離れて"],
                "reply_stance": "saw",
                "reply_policy": reply_policy_line("saw"),
            }
        )
        self.assertIn("静止指示は禁止", content)
        self.assertIn("周囲の敵の性質メモ", content)
        self.assertIn("言ってよい短いヒント例", content)

    def test_hearing_block_only_when_present(self) -> None:
        with_hearing = self._content(
            {
                "user_text": "音した？",
                "mode": "normal",
                "time_phase": "day",
                "threat_summary": "とくになし",
                "hearing_summary": "ゾンビの音 前 far",
                "hearing_named_mobs": ["ゾンビ"],
                "reply_stance": "none",
                "reply_policy": reply_policy_line("none"),
            }
        )
        self.assertIn("ゾンビの音 前 far", with_hearing)
        self.assertIn("音から使ってよい具体モブ名: ゾンビ", with_hearing)


if __name__ == "__main__":
    unittest.main()
