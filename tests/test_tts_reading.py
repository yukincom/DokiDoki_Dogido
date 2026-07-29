from __future__ import annotations

import unittest

from dogido_server.tts_reading import (
    apply_manual_tts_replacements,
    prepare_text_for_tts,
    unidic_available,
)


class TtsReadingManualTests(unittest.TestCase):
    """辞書無しでも必ず通る例外表の挙動。"""

    def test_asa_not_chou(self) -> None:
        self.assertEqual(
            prepare_text_for_tts("朝から元気でええねん。", engine="off"),
            "あさから元気でええねん。",
        )
        self.assertIn("あさ", prepare_text_for_tts("朝やな", engine="off"))

    def test_kusachi(self) -> None:
        self.assertEqual(
            prepare_text_for_tts("草地がきれい", engine="off"),
            "くさちがきれい",
        )

    def test_empty(self) -> None:
        self.assertEqual(prepare_text_for_tts(""), "")
        self.assertEqual(prepare_text_for_tts("   "), "")

    def test_manual_helper(self) -> None:
        self.assertEqual(apply_manual_tts_replacements("今日は一日"), "きょうはいちにち")

    def test_idempotent_after_manual(self) -> None:
        once = prepare_text_for_tts("朝から元気", engine="off")
        twice = prepare_text_for_tts(once, engine="off")
        self.assertEqual(once, twice)


@unittest.skipUnless(unidic_available(), "fugashi+unidic-lite not installed")
class TtsReadingUnidicTests(unittest.TestCase):
    """UniDic 導入環境での補完パス。"""

    def setUp(self) -> None:
        # 他テストのモック後でも使えるようリセットしない（available 済み）
        pass

    def test_choseng_not_asa(self) -> None:
        # 単純 replace の「朝→あさ」が朝鮮を壊さない（ちょうせん or 漢字のまま）
        out = prepare_text_for_tts("朝鮮半島", engine="unidic")
        self.assertNotIn("あさ", out)
        self.assertTrue("ちょうせん" in out or "朝鮮" in out)

    def test_wago_content_word_hiragana(self) -> None:
        # 例外表に無い和語漢字もひらがな化される
        out = prepare_text_for_tts("旗が立ってる", engine="unidic")
        self.assertIn("はた", out)
        self.assertNotIn("旗", out)

    def test_mizube(self) -> None:
        out = prepare_text_for_tts("水辺にいるで", engine="unidic")
        self.assertIn("みずべ", out)

    def test_manual_still_wins_for_ichinichi(self) -> None:
        # UniDic 既定は「ついたち」寄り。例外表を先に当てていちにちを守る
        out = prepare_text_for_tts("今日は一日のんびり", engine="unidic")
        self.assertIn("いちにち", out)
        self.assertNotIn("ついたち", out)

    def test_asa_still_ok_with_unidic(self) -> None:
        out = prepare_text_for_tts("朝から元気でええねん。", engine="auto")
        self.assertEqual(out, "あさから元気でええねん。")

    def test_off_skips_unidic_long_tail(self) -> None:
        # off では例外表に無い漢字が残る
        out = prepare_text_for_tts("旗が立ってる", engine="off")
        self.assertIn("旗", out)


class TtsReadingEngineResolveTests(unittest.TestCase):
    def test_unknown_engine_falls_back_to_auto_behavior(self) -> None:
        # 不正値は auto 扱い。辞書無しでも例外表は動く
        out = prepare_text_for_tts("朝だ", engine="nope")  # type: ignore[arg-type]
        self.assertIn("あさ", out)


if __name__ == "__main__":
    unittest.main()
