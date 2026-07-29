from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from dogido_server.player_name_voice import (
    build_named_ushiro_sequence,
    clear_player_name_voice_cache,
    resolve_player_name_clip_file,
    resolve_player_name_fragment_id,
)


class PlayerNameVoiceTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_player_name_voice_cache()

    def tearDown(self) -> None:
        clear_player_name_voice_cache()

    def test_manifest_maps_call_name_not_hardcoded_slot(self) -> None:
        with TemporaryDirectory() as tmp:
            names = Path(tmp)
            (names / "alice.mp3").write_bytes(b"a")
            (names / "bob.mp3").write_bytes(b"b")
            (names / "manifest.json").write_text(
                json.dumps(
                    {
                        "call_name_to_file": {
                            "メルちゃん": "alice.mp3",
                            "たろう": "bob.mp3",
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                resolve_player_name_clip_file("メルちゃん", names),
                names / "alice.mp3",
            )
            self.assertEqual(
                resolve_player_name_clip_file("たろう", names),
                names / "bob.mp3",
            )
            # 未知の呼び名は None（player_1 に落とさない）
            self.assertIsNone(resolve_player_name_clip_file("しらない人", names))

    def test_direct_filename_by_call_name(self) -> None:
        with TemporaryDirectory() as tmp:
            names = Path(tmp)
            (names / "メルちゃん.mp3").write_bytes(b"x")
            path = resolve_player_name_clip_file("メルちゃん", names)
            self.assertEqual(path, names / "メルちゃん.mp3")

    def test_named_ushiro_sequence_needs_tail(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            names = root / "player_names"
            names.mkdir()
            (names / "player_1.mp3").write_bytes(b"n")
            (names / "ushiro_tail.mp3").write_bytes(b"t")
            (names / "manifest.json").write_text(
                json.dumps({"call_name_to_file": {"プレイヤーワン": "player_1.mp3"}}),
                encoding="utf-8",
            )
            clear_player_name_voice_cache()
            seq = build_named_ushiro_sequence("プレイヤーワン", cue_audio_dir=root)
            self.assertEqual(seq, ["player_names/player_1", "player_names/ushiro_tail"])
            self.assertEqual(
                resolve_player_name_fragment_id("プレイヤーワン", names),
                "player_names/player_1",
            )

    def test_repo_player_names_optional_live_mapping(self) -> None:
        """リポの cue_voice があれば、manifest のプレイヤーワンが解決できること。"""
        names = Path("cue_voice/player_names")
        if not names.is_dir():
            self.skipTest("cue_voice/player_names missing")
        if not (names / "player_1.mp3").is_file():
            self.skipTest("local player_1.mp3 not present")
        if not (names / "manifest.json").is_file():
            self.skipTest("local manifest.json not present")
        clear_player_name_voice_cache()
        path = resolve_player_name_clip_file("プレイヤーワン", names)
        self.assertIsNotNone(path)
        self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
