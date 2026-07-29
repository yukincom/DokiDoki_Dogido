from __future__ import annotations

import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from dogido_server.audio import VoicevoxSpeechBackend
from dogido_server.config import Settings


class VoicevoxCachePruneTests(unittest.TestCase):
    def test_prune_removes_old_files_by_age(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                voicevox_temp_dir=root,
                voicevox_cache_max_mb=100.0,
                voicevox_cache_max_age_days=1.0,
                audio_enabled=False,
            )
            backend = VoicevoxSpeechBackend(settings)
            old = backend.cache_dir / "old.wav"
            new = backend.cache_dir / "new.wav"
            old.write_bytes(b"old-audio")
            new.write_bytes(b"new-audio")
            old_mtime = time.time() - (3 * 86400)
            new_mtime = time.time()
            import os

            os.utime(old, (old_mtime, old_mtime))
            os.utime(new, (new_mtime, new_mtime))

            backend._prune_cache()

            self.assertFalse(old.exists())
            self.assertTrue(new.exists())

    def test_prune_enforces_max_size_oldest_first(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            # 合計を約 30KB 上限にし、古い方から消す
            settings = Settings(
                voicevox_temp_dir=root,
                voicevox_cache_max_mb=0.02,  # ~20 KiB
                voicevox_cache_max_age_days=0.0,  # age 無効
                audio_enabled=False,
            )
            backend = VoicevoxSpeechBackend(settings)
            older = backend.cache_dir / "a.wav"
            newer = backend.cache_dir / "b.wav"
            payload = b"x" * 15_000
            older.write_bytes(payload)
            newer.write_bytes(payload)
            import os

            now = time.time()
            os.utime(older, (now - 100, now - 100))
            os.utime(newer, (now, now))

            backend._prune_cache()

            self.assertFalse(older.exists())
            self.assertTrue(newer.exists())


if __name__ == "__main__":
    unittest.main()
