"""STT 既知誤変換の最小固定表（#29）。"""

from __future__ import annotations

import unittest

from dogido_server.player_input.asr_fixes import apply_asr_fixes
from dogido_server.player_input.normalize import normalize_player_text
from dogido_server.player_input.routing import route_player_input


class AsrFixTableTests(unittest.TestCase):
    def test_pressure_plate_garbage_to_canonical(self) -> None:
        cases = (
            "関圧番だよ",
            "管轄版だよ",
            "間月版です",
            "貨物板ある？",
            "感圧版を置いた",
            "かんあつばん",
        )
        for raw in cases:
            fixed, applied = apply_asr_fixes(raw)
            self.assertIn("感圧板", fixed, msg=raw)
            self.assertTrue(applied, msg=raw)
            self.assertNotIn("関圧番", fixed)
            self.assertNotIn("管轄版", fixed)

    def test_w_row_i_grade_garbage_reaches_the_knowledge_subject(self) -> None:
        for raw in ("和行為為団って何？", "和業・イダンって何？"):
            with self.subTest(raw=raw):
                context = route_player_input(raw)
                self.assertEqual("ワ行イ段って何？", context.raw_text)
                self.assertIsNotNone(context.knowledge_query)
                assert context.knowledge_query is not None
                self.assertEqual("ワ行イ段", context.knowledge_query.subject)

    def test_unrelated_text_unchanged(self) -> None:
        for raw in ("こんにちは", "うん", "松明ある？", "感圧板だよ"):
            fixed, applied = apply_asr_fixes(raw)
            self.assertEqual(fixed, raw)
            self.assertEqual(applied, [])

    def test_normalize_and_route_use_fixed_surface(self) -> None:
        self.assertEqual(normalize_player_text("関圧番だよ"), "感圧板だよ")
        ctx = route_player_input("関圧番だよ")
        # player_chat は raw_text を見る
        self.assertEqual(ctx.raw_text, "感圧板だよ")
        self.assertEqual(ctx.normalized_text, "感圧板だよ")


if __name__ == "__main__":
    unittest.main()
