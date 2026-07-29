from __future__ import annotations

import unittest

from dogido_server.audio import AudioDispatcher
from dogido_server.config import Settings
from dogido_server.state_machine.types import AudioAction


class SpeechSpeedProfileTests(unittest.TestCase):
    def test_settings_profile_speeds(self) -> None:
        settings = Settings(
            voicevox_speed_scale=1.0,
            voicevox_speed_scale_peace=0.88,
            voicevox_speed_scale_haiku=0.80,
            voicevox_speed_scale_battle=None,
        )
        self.assertEqual(settings.tts_speed_for_profile("battle"), 1.0)
        self.assertEqual(settings.tts_speed_for_profile("peace"), 0.88)
        self.assertEqual(settings.tts_speed_for_profile("haiku"), 0.80)
        self.assertEqual(settings.tts_speed_for_profile(None), 1.0)

        settings_battle = Settings(
            voicevox_speed_scale=1.0,
            voicevox_speed_scale_battle=1.05,
        )
        self.assertEqual(settings_battle.tts_speed_for_profile("battle"), 1.05)

    def test_dispatcher_layer_defaults(self) -> None:
        settings = Settings(
            audio_enabled=False,
            tts_backend="noop",
            cue_backend="noop",
            voicevox_speed_scale=1.0,
            voicevox_speed_scale_peace=0.88,
            voicevox_speed_scale_haiku=0.80,
        )
        dispatcher = AudioDispatcher(settings)
        callout = AudioAction(layer="callout", interrupt=False, text="テスト")
        speech = AudioAction(layer="speech", interrupt=False, text="テスト")
        haiku = AudioAction(
            layer="speech",
            interrupt=False,
            text="ここで一句。",
            speech_profile="haiku",
        )
        explicit = AudioAction(
            layer="speech",
            interrupt=False,
            text="x",
            speed_scale=0.5,
        )
        self.assertEqual(dispatcher._resolve_tts_speed(callout), 1.0)
        self.assertEqual(dispatcher._resolve_tts_speed(speech), 0.88)
        self.assertEqual(dispatcher._resolve_tts_speed(haiku), 0.80)
        self.assertEqual(dispatcher._resolve_tts_speed(explicit), 0.5)


if __name__ == "__main__":
    unittest.main()
