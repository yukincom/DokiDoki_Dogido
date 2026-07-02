from __future__ import annotations

import unittest
from pathlib import Path

from dogido_server.cues import resolve_cue_path


class CueResolutionTests(unittest.TestCase):
    def test_named_cue_resolves_to_existing_file(self) -> None:
        resolved = resolve_cue_path(Path("cue_voice"), "spot_hostile_gasp")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.name, "freesound_community-male-gasp-1-7183.mp3")

    def test_panic_cue_resolves_to_existing_file(self) -> None:
        resolved = resolve_cue_path(Path("cue_voice"), "panic_scream_start")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.name, "universfield-man-scream-08-352438.mp3")

    def test_ushiro_cue_resolves_to_existing_file(self) -> None:
        resolved = resolve_cue_path(Path("cue_voice"), "ushiro_scream")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.name, "universfield-man-scream-08-352438.mp3")

    def test_suppressed_cues_resolve_to_existing_files(self) -> None:
        gasp = resolve_cue_path(Path("cue_voice"), "suppressed_gasp")
        breath = resolve_cue_path(Path("cue_voice"), "suppressed_breath")
        self.assertIsNotNone(gasp)
        self.assertIsNotNone(breath)
        self.assertEqual(gasp.name, "universfield-funny-dramatic-gasp-320975.mp3")
        self.assertEqual(breath.name, "freesound_community-heavy-breath-male-63980.mp3")

    def test_aftermath_cue_resolves_to_existing_file(self) -> None:
        resolved = resolve_cue_path(Path("cue_voice"), "aftermath_relief")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.name, "aftermath.mp3")


if __name__ == "__main__":
    unittest.main()
