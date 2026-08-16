from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

from dogido_server.voice_input import (
    HAIKU_WORKSHOP_STT_PROMPT,
    NORMAL_STT_PROMPT,
    fetch_voice_prompt_mode,
    stt_prompt,
    transcribe,
)


class VoiceInputPromptTests(unittest.TestCase):
    def test_prompt_is_scoped_to_normal_or_workshop_conversation(self) -> None:
        self.assertEqual(
            NORMAL_STT_PROMPT,
            "Minecraftのプレイ内容について日本語で会話しています。"
            "話題はブロック、アイテム、モブ、バイオーム、建築、戦闘です。",
        )
        self.assertEqual(
            HAIKU_WORKSHOP_STT_PROMPT,
            "Minecraftのプレイ内容について日本語で会話しています。"
            "話題はブロック、アイテム、モブ、バイオーム、建築、戦闘と、"
            "日本語の読み方、言い換え、川柳の上五・中七・下五の推敲です。",
        )
        self.assertEqual(stt_prompt("normal"), NORMAL_STT_PROMPT)
        self.assertNotIn("上五", NORMAL_STT_PROMPT)
        self.assertEqual(stt_prompt("haiku_workshop"), HAIKU_WORKSHOP_STT_PROMPT)
        self.assertIn("上五・中七・下五", HAIKU_WORKSHOP_STT_PROMPT)

    @patch("dogido_server.voice_input.httpx.get")
    def test_fetch_prompt_mode_uses_server_context(self, get: Mock) -> None:
        response = Mock()
        response.json.return_value = {"prompt_mode": "haiku_workshop"}
        get.return_value = response

        mode = fetch_voice_prompt_mode("http://127.0.0.1:5055", "secret")

        self.assertEqual(mode, "haiku_workshop")
        get.assert_called_once_with(
            "http://127.0.0.1:5055/api/v1/voice-input/context",
            headers={"Authorization": "Bearer secret"},
            timeout=1.0,
        )
        response.raise_for_status.assert_called_once_with()

    @patch("dogido_server.voice_input.httpx.get")
    def test_fetch_prompt_mode_falls_back_to_normal(self, get: Mock) -> None:
        get.side_effect = httpx.ConnectError("server unavailable")
        self.assertEqual(
            fetch_voice_prompt_mode("http://127.0.0.1:5055", None),
            "normal",
        )

    @patch("dogido_server.voice_input.subprocess.run")
    def test_transcribe_passes_selected_prompt_to_whisper(self, run: Mock) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="[00:00:00.000 --> 00:00:01.000] 下五を変えたい\n",
            stderr="",
        )

        text = transcribe(
            Path("/tmp/whisper-cli"),
            Path("/tmp/model.bin"),
            b"\x00\x00" * 160,
            no_speech_thold=0.6,
            prompt=HAIKU_WORKSHOP_STT_PROMPT,
        )

        self.assertEqual(text, "下五を変えたい")
        command = run.call_args.args[0]
        prompt_index = command.index("--prompt") + 1
        self.assertEqual(command[prompt_index], HAIKU_WORKSHOP_STT_PROMPT)


if __name__ == "__main__":
    unittest.main()
