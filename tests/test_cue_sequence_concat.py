from __future__ import annotations

import unittest
from pathlib import Path

from dogido_server.audio import AudioDispatcher, FFMPEG_BIN
from dogido_server.config import Settings


class CueSequenceConcatTests(unittest.TestCase):
    def test_concat_multi_fragments_when_assets_exist(self) -> None:
        cue_dir = Path("cue_voice")
        parts = [
            cue_dir / "mob" / "zombie.mp3",
            cue_dir / "common" / "counts" / "1.mp3",
            cue_dir / "common" / "phrases" / "orude.mp3",
        ]
        if not all(p.is_file() for p in parts):
            self.skipTest("cue_voice callout fragments missing")
        if not Path(FFMPEG_BIN).exists():
            self.skipTest("ffmpeg missing")

        settings = Settings(
            audio_enabled=False,
            tts_backend="noop",
            cue_backend="noop",
            cue_audio_dir=cue_dir,
        )
        dispatcher = AudioDispatcher(settings)
        ids = ["mob/zombie", "common/counts/1", "common/phrases/orude"]
        combined = dispatcher._concat_cue_files(ids, parts)
        self.assertIsNotNone(combined)
        assert combined is not None
        self.assertTrue(combined.is_file())
        self.assertGreater(combined.stat().st_size, 500)

        # 2回目はキャッシュヒット（同じパス）
        again = dispatcher._concat_cue_files(ids, parts)
        self.assertEqual(again, combined)

    def test_single_fragment_skips_concat(self) -> None:
        settings = Settings(audio_enabled=False, tts_backend="noop", cue_backend="noop")
        dispatcher = AudioDispatcher(settings)
        # 1本は concat を呼ばず path をそのまま使う経路（ここは _start 側）
        # concat 自体に1本を渡しても動くことだけ確認
        cue = Path("cue_voice/common/phrases/orude.mp3")
        if not cue.is_file():
            self.skipTest("orude.mp3 missing")
        if not Path(FFMPEG_BIN).exists():
            self.skipTest("ffmpeg missing")
        out = dispatcher._concat_cue_files(["common/phrases/orude"], [cue])
        self.assertIsNotNone(out)


if __name__ == "__main__":
    unittest.main()
