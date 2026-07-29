from __future__ import annotations

import unittest

from dogido_server.tts_reading import prepare_text_for_tts


class TtsReadingTests(unittest.TestCase):
    def test_asa_not_chou(self) -> None:
        self.assertEqual(
            prepare_text_for_tts("朝から元気でええねん。"),
            "あさから元気でええねん。",
        )
        self.assertIn("あさ", prepare_text_for_tts("朝やな"))

    def test_kusachi(self) -> None:
        self.assertEqual(prepare_text_for_tts("草地がきれい"), "くさちがきれい")

    def test_empty(self) -> None:
        self.assertEqual(prepare_text_for_tts(""), "")
        self.assertEqual(prepare_text_for_tts("   "), "")


if __name__ == "__main__":
    unittest.main()
