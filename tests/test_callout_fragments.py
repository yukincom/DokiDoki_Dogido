from __future__ import annotations

import unittest
from pathlib import Path

from dogido_server.callout_fragments import (
    build_count_summary_sequence,
    build_single_mob_presence_sequence,
    fragment_path,
)
from dogido_server.config import Settings
from dogido_server.state_machine.types import AudioAction, CalloutPayload


class CalloutFragmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cue_dir = Settings().cue_audio_dir
        self.assertIsNotNone(self.cue_dir)
        self.assertTrue(Path(self.cue_dir).is_dir())

    def test_count_summary_sequence_resolves_on_disk(self) -> None:
        seq = build_count_summary_sequence(
            {"zombie": 2, "skeleton": 1},
            suppressed=False,
            cue_dir=self.cue_dir,
        )
        self.assertEqual(
            seq,
            [
                "mob/zombie",
                "common/counts/2",
                "mob/skeleton",
                "common/counts/1",
                "common/phrases/orude",
            ],
        )
        for fragment_id in seq:
            self.assertIsNotNone(fragment_path(self.cue_dir, fragment_id), fragment_id)

    def test_suppressed_uses_ga_orude(self) -> None:
        seq = build_count_summary_sequence(
            {"creeper": 1},
            suppressed=True,
            cue_dir=self.cue_dir,
        )
        self.assertEqual(seq[-1], "common/phrases/ga_orude")

    def test_missing_mob_returns_none(self) -> None:
        seq = build_count_summary_sequence(
            {"definitely_not_a_mob_xyz": 2},
            suppressed=False,
            cue_dir=self.cue_dir,
        )
        self.assertIsNone(seq)

    def test_single_presence(self) -> None:
        seq = build_single_mob_presence_sequence("enderman", count=1, cue_dir=self.cue_dir)
        self.assertEqual(seq, ["mob/enderman", "common/counts/1", "common/phrases/orude"])

    def test_callout_payload_and_audio_action_shape(self) -> None:
        payload = CalloutPayload(
            text="ゾンビ2体おるで。",
            cue_sequence=("mob/zombie", "common/counts/2", "common/phrases/orude"),
        )
        action = AudioAction(
            layer="callout",
            interrupt=False,
            text=payload.text,
            cue_sequence=payload.cue_sequence,
            protect_ms=0,
        )
        self.assertEqual(action.layer, "callout")
        self.assertEqual(len(action.cue_sequence), 3)


if __name__ == "__main__":
    unittest.main()
