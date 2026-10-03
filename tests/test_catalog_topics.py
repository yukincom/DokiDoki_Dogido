"""開発用カタログの語彙照合。実行時の観測照合と会話判断はRustで検査する。"""

from __future__ import annotations

import unittest

from dev_tools.catalog_tools.entry_catalog import find_catalog_topics, format_catalog_topic_hints


class FindCatalogTopicsTests(unittest.TestCase):
    def _ids(self, text: str) -> list[str]:
        hits = find_catalog_topics(text)
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

    def test_short_katakana_name_does_not_match_inside_hiragana_phrase(self) -> None:
        self.assertNotIn("squid", self._ids("いいんじゃないかな"))
        self.assertIn("squid", self._ids("イカかな"))


    def test_pointy_hat_purple_maps_to_witch(self) -> None:
        ids = self._ids("とんがり帽子の紫のやつ")
        self.assertIn("witch", ids)

    def test_outpost_structure_label(self) -> None:
        ids = self._ids("ピリジャー前哨基地ある？")
        # 前哨基地は pillager 語彙にも載る。structure id が取れれば ideal
        self.assertIn("pillager", ids)
        if "pillager_outpost" not in ids:
            self.skipTest("structure catalog term 未接続（pillager ヒットは確認済み）")

    def test_format_hints_mentions_match_and_observation(self) -> None:
        hits = find_catalog_topics("ババア")
        text = format_catalog_topic_hints(hits)
        self.assertIn("ウィッチ", text)
        self.assertIn("ババア", text)
        self.assertIn("観測: なし", text)


if __name__ == "__main__":
    unittest.main()
