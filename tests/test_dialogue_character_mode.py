from __future__ import annotations

import unittest

from dogido_server.llm.prompts import (
    character_mode_for_request,
    resolve_character_mode_from_state,
    system_prompt_for_mode,
    build_messages,
)
from dogido_server.llm.types import LeafGenerationRequest


class CharacterModeResolutionTests(unittest.TestCase):
    def test_state_modes_map_to_character_modes(self) -> None:
        self.assertEqual(resolve_character_mode_from_state("normal"), "peace")
        self.assertEqual(resolve_character_mode_from_state("alert"), "tension")
        self.assertEqual(resolve_character_mode_from_state("panic"), "battle")
        self.assertEqual(resolve_character_mode_from_state("suppressed_panic"), "battle")
        self.assertEqual(resolve_character_mode_from_state("aftermath"), "battle")

    def test_combat_or_threats_force_battle(self) -> None:
        self.assertEqual(
            resolve_character_mode_from_state("normal", combat_active=True),
            "battle",
        )
        self.assertEqual(
            resolve_character_mode_from_state("normal", has_visual_threats=True),
            "battle",
        )

    def test_darkness_raises_tension(self) -> None:
        self.assertEqual(
            resolve_character_mode_from_state("normal", danger_darkness_high=True),
            "tension",
        )

    def test_kind_defaults(self) -> None:
        self.assertEqual(character_mode_for_request("ambient"), "peace")
        self.assertEqual(character_mode_for_request("hostile_callout"), "battle")
        self.assertEqual(character_mode_for_request("dark_push_no_light"), "tension")
        self.assertEqual(character_mode_for_request("aftermath"), "battle")
        self.assertEqual(character_mode_for_request("death"), "peace")


class CharacterModePromptTests(unittest.TestCase):
    def test_peace_system_prompt_suppresses_fear_spam(self) -> None:
        system = system_prompt_for_mode("peace")
        self.assertIn("平和時", system)
        self.assertIn("気さく", system)
        self.assertIn("怖がり反応は抑え", system)
        self.assertNotIn("わーきゃー", system)

    def test_battle_system_prompt_encourages(self) -> None:
        system = system_prompt_for_mode("battle")
        self.assertIn("バトル時", system)
        self.assertIn("応援", system)
        self.assertIn("わーきゃー", system)

    def test_player_chat_peace_prompt(self) -> None:
        messages = build_messages(
            LeafGenerationRequest(
                kind="player_chat",
                fallback_text="fallback",
                details={
                    "user_text": "おはようさん",
                    "mode": "normal",
                    "biome": "平原",
                    "time_phase": "day",
                },
            )
        )
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("平和時", messages[0]["content"])
        self.assertIn("おはようさん", messages[1]["content"])
        # S1: mode トーンは system のみ。user に「キャラクターモードはpeace」を重ねない

    def test_player_chat_battle_prompt(self) -> None:
        messages = build_messages(
            LeafGenerationRequest(
                kind="player_chat",
                fallback_text="fallback",
                details={
                    "user_text": "大丈夫？",
                    "mode": "panic",
                    "character_mode": "battle",
                    "has_visual_threats": True,
                    "threat_summary": "視認 クリーパー が後ろ 4マス",
                    "biome": "洞窟",
                    "time_phase": "night",
                    "reply_stance": "saw",
                    "reply_policy": "脅威メモの視認を優先してよい。",
                },
            )
        )
        self.assertIn("バトル時", messages[0]["content"])
        self.assertIn("応援", messages[0]["content"])
        self.assertIn("視認 クリーパー", messages[1]["content"])
        # S1: user の「バトル時:」mode_hint は廃止。敵対時の静止禁止は残す
        self.assertIn("静止指示は禁止", messages[1]["content"])
        self.assertIn("答え方スタンス: saw", messages[1]["content"])

    def test_ambient_uses_peace_system(self) -> None:
        messages = build_messages(
            LeafGenerationRequest(
                kind="ambient",
                fallback_text="fallback",
                details={"mob": "牛", "direction": "前", "biome": "平原", "time_phase": "day"},
            )
        )
        self.assertIn("平和時", messages[0]["content"])
        self.assertIn("怖がり連発は禁止", messages[1]["content"])

    def test_hostile_callout_uses_battle_system(self) -> None:
        messages = build_messages(
            LeafGenerationRequest(
                kind="hostile_callout",
                fallback_text="fallback",
                details={"hostile": "ゾンビ", "direction": "後ろ", "mode": "panic"},
            )
        )
        self.assertIn("バトル時", messages[0]["content"])
        self.assertIn("応援", messages[1]["content"])


if __name__ == "__main__":
    unittest.main()
