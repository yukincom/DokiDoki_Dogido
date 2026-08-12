"""現在語彙だけへ閉じた STT 文脈補正。"""

from __future__ import annotations

import unittest

from dogido_server.player_input.contextual_asr import (
    ContextualASRCandidate,
    apply_candidate_asr_fixes,
    normalize_spoken_kana,
    workshop_asr_candidates,
)
from dogido_server.player_input.routing import route_player_input


class ContextualASRTests(unittest.TestCase):
    def test_unique_phonetic_neighbour_uses_only_current_candidate(self) -> None:
        fixed, applied = apply_candidate_asr_fixes(
            "それだったらユーグレイヤの方がいいんじゃない?",
            (
                ContextualASRCandidate(
                    surface="夕暮れ",
                    readings=("ゆうぐれ",),
                    source="time_phase:evening",
                ),
            ),
        )

        self.assertEqual(fixed, "それだったら夕暮れやの方がいいんじゃない?")
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0].original, "ユーグレイヤ")
        self.assertEqual(applied[0].distance, 1)

    def test_ambiguous_equal_candidates_fail_closed(self) -> None:
        fixed, applied = apply_candidate_asr_fixes(
            "ユーグレイヤがいい",
            (
                ContextualASRCandidate("夕暮れ", ("ゆうぐれ",), "time"),
                ContextualASRCandidate("優暮れ", ("ゆうぐれ",), "invented-test"),
            ),
        )

        self.assertEqual(fixed, "ユーグレイヤがいい")
        self.assertEqual(applied, ())

    def test_common_words_are_not_rewritten_without_context_candidate(self) -> None:
        fixed, applied = apply_candidate_asr_fixes(
            "自然な子供になるんじゃないですか",
            (ContextualASRCandidate("夕暮れ", ("ゆうぐれ",), "time"),),
        )

        self.assertEqual(fixed, "自然な子供になるんじゃないですか")
        self.assertEqual(applied, ())

    def test_workshop_candidate_builder_projects_enum_and_catalog_reading(self) -> None:
        candidates = workshop_asr_candidates(
            verse="さむいあめや\nまどのひかりを\nくさちのね",
            materials={
                "time_phase": "evening",
                "catalog_sources": [
                    {
                        "source_ref": "biome:meadow",
                        "label": "草地",
                        "reading": "くさち",
                    }
                ],
            },
        )
        surfaces = {candidate.surface: candidate.readings for candidate in candidates}

        self.assertEqual(surfaces["夕暮れ"], ("ゆうぐれ",))
        self.assertEqual(surfaces["草地"], ("くさち",))

    def test_long_vowel_and_katakana_are_folded_for_comparison(self) -> None:
        self.assertEqual(normalize_spoken_kana("ユーグレイ"), "ゆうぐれい")

    def test_interpreted_text_is_separate_from_literal_command_surface(self) -> None:
        context = route_player_input(
            "ユーグレイヤの方がいい",
            interpreted_text="夕暮れやの方がいい",
        )

        self.assertEqual(context.raw_text, "ユーグレイヤの方がいい")
        self.assertEqual(context.normalized_text, "ユーグレイヤの方がいい")
        self.assertEqual(context.semantic_text, "夕暮れやの方がいい")


if __name__ == "__main__":
    unittest.main()
