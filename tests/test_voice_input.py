from __future__ import annotations

import subprocess
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

from dogido_server.config import Settings
from dogido_server.player_input.voice_vocalization import is_pure_voice_vocalization
from dogido_server.voice_input import (
    CapturedSpeech,
    HAIKU_WORKSHOP_STT_PROMPT,
    NORMAL_STT_PROMPT,
    SpeechRecognitionWorker,
    VoiceDiagnosticDispatcher,
    contains_speech,
    fetch_voice_prompt_mode,
    report_voice_diagnostic,
    stt_prompt,
    transcribe,
    transcribe_with_empty_retry,
    NOISE_PATTERNS,
)


class VoiceInputPromptTests(unittest.TestCase):
    def test_pure_vocalization_is_separate_from_noise_and_meaningful_voice(self) -> None:
        for text in ("うおおお", "うわあああ！", "ぎゃあああ", "ああああ"):
            self.assertTrue(is_pure_voice_vocalization(text), text)
        for text in (
            "ああ、そうか",
            "『うおおお』って言った",
            "うおおお、剣に持ち替えて",
            "石炭だ",
        ):
            self.assertFalse(is_pure_voice_vocalization(text), text)
        self.assertNotIn("うおおお", NOISE_PATTERNS)

    def test_default_capture_window_allows_a_thoughtful_pause(self) -> None:
        settings = Settings()

        self.assertEqual(1200, settings.voice_silence_ms)
        self.assertEqual(30.0, settings.voice_max_speech_sec)
        self.assertEqual(1, settings.voice_stt_max_pending_segments)
        self.assertEqual(8.0, settings.voice_stt_max_segment_age_sec)

    def test_stt_worker_replaces_only_the_unprocessed_segment(self) -> None:
        first_started = threading.Event()
        release_first = threading.Event()
        latest_finished = threading.Event()
        processed: list[int] = []
        diagnostics: list[dict[str, object]] = []

        def process(segment: CapturedSpeech) -> None:
            value = segment.pcm[0]
            processed.append(value)
            if value == 1:
                first_started.set()
                release_first.wait(1.0)
            if value == 3:
                latest_finished.set()

        worker = SpeechRecognitionWorker(
            process,
            diagnostic=lambda **fields: diagnostics.append(fields),
            max_pending=1,
        )
        self.addCleanup(worker.close)

        def segment(value: int) -> CapturedSpeech:
            return CapturedSpeech(
                pcm=bytes([value]),
                duration_ms=900,
                voiced_ms=600,
                captured_at=time.monotonic(),
            )

        self.assertTrue(worker.submit(segment(1)))
        self.assertTrue(first_started.wait(1.0))
        self.assertTrue(worker.submit(segment(2)))
        self.assertTrue(worker.submit(segment(3)))
        release_first.set()
        self.assertTrue(latest_finished.wait(1.0))
        worker.close()

        self.assertEqual([1, 3], processed)
        self.assertIn(
            "pending_segment_replaced",
            [item.get("reason") for item in diagnostics],
        )

    def test_stt_worker_drops_a_stale_segment(self) -> None:
        processed: list[CapturedSpeech] = []
        stale_reported = threading.Event()

        def diagnostic(**fields: object) -> None:
            if fields.get("reason") == "stale_segment_dropped":
                stale_reported.set()

        worker = SpeechRecognitionWorker(
            processed.append,
            diagnostic=diagnostic,
            max_age_sec=0.01,
        )
        self.addCleanup(worker.close)
        worker.submit(
            CapturedSpeech(
                pcm=b"stale",
                duration_ms=1000,
                voiced_ms=500,
                captured_at=time.monotonic() - 1.0,
            )
        )

        self.assertTrue(stale_reported.wait(1.0))
        self.assertEqual([], processed)

    def test_diagnostic_dispatcher_drops_when_bounded_queue_is_full(self) -> None:
        started = threading.Event()
        release = threading.Event()
        sent: list[str] = []

        def send(**fields: object) -> None:
            sent.append(str(fields["reason"]))
            if fields["reason"] == "first":
                started.set()
                release.wait(1.0)

        dispatcher = VoiceDiagnosticDispatcher(send, max_pending=1)
        self.assertTrue(dispatcher.submit(reason="first"))
        self.assertTrue(started.wait(1.0))
        self.assertTrue(dispatcher.submit(reason="second"))
        self.assertFalse(dispatcher.submit(reason="overflow"))
        release.set()
        dispatcher.close(timeout=1.0)

        self.assertEqual("first", sent[0])
        self.assertNotIn("overflow", sent)

    def test_prompt_is_scoped_to_normal_or_workshop_conversation(self) -> None:
        self.assertEqual(
            NORMAL_STT_PROMPT,
            "Minecraftのプレイ内容について日本語で会話しています。"
            "話題はブロック、アイテム、モブ、バイオーム、建築、戦闘です。"
            "国語や詩について質問することもあり、ワ行イ段、ゐ、枕詞、川柳、"
            "ソネットという語を使います。",
        )
        self.assertEqual(
            HAIKU_WORKSHOP_STT_PROMPT,
            "Minecraftのプレイ内容について日本語で会話しています。"
            "話題はブロック、アイテム、モブ、バイオーム、建築、戦闘と、"
            "日本語の読み方、言い換え、川柳の上五・中七・下五の推敲です。",
        )
        self.assertEqual(stt_prompt("normal"), NORMAL_STT_PROMPT)
        self.assertNotIn("上五", NORMAL_STT_PROMPT)
        self.assertIn("ワ行イ段", NORMAL_STT_PROMPT)
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
        diagnostics: list[dict[str, object]] = []
        self.assertEqual(
            fetch_voice_prompt_mode(
                "http://127.0.0.1:5055",
                None,
                diagnostic=lambda **fields: diagnostics.append(fields),
            ),
            "normal",
        )
        self.assertEqual("context", diagnostics[0]["event"])
        self.assertEqual("context_unavailable", diagnostics[0]["reason"])

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

    @patch("dogido_server.voice_input.subprocess.run")
    def test_transcribe_enables_whisper_vad_when_model_is_supplied(
        self,
        run: Mock,
    ) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="[00:00:00.000 --> 00:00:01.000] 聞こえます\n",
            stderr="",
        )

        text = transcribe(
            Path("/tmp/whisper-cli"),
            Path("/tmp/model.bin"),
            b"\x00\x00" * 160,
            no_speech_thold=0.6,
            vad_model=Path("/tmp/silero.bin"),
        )

        self.assertEqual("聞こえます", text)
        command = run.call_args.args[0]
        self.assertIn("--vad", command)
        self.assertEqual(
            "/tmp/silero.bin",
            command[command.index("--vad-model") + 1],
        )

    @patch("dogido_server.voice_input.subprocess.run")
    def test_transcribe_can_disable_gpu_for_a_control_run(self, run: Mock) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="[00:00:00.000 --> 00:00:01.000] 聞こえます\n",
            stderr="",
        )

        text = transcribe(
            Path("/tmp/whisper-cli"),
            Path("/tmp/model.bin"),
            b"\x00\x00" * 160,
            no_speech_thold=0.6,
            use_gpu=False,
        )

        self.assertEqual("聞こえます", text)
        self.assertIn("--no-gpu", run.call_args.args[0])

    @patch("dogido_server.voice_input.subprocess.run")
    def test_empty_transcript_reports_a_visible_reason(self, run: Mock) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="whisper: no speech detected",
        )
        diagnostics: list[dict[str, object]] = []

        text = transcribe(
            Path("/tmp/whisper-cli"),
            Path("/tmp/model.bin"),
            b"\x00\x00" * 160,
            no_speech_thold=0.6,
            diagnostic=lambda **fields: diagnostics.append(fields),
        )

        self.assertIsNone(text)
        self.assertEqual("stt_rejected", diagnostics[0]["event"])
        self.assertEqual("empty_transcript", diagnostics[0]["reason"])
        self.assertIn("no speech", diagnostics[0]["detail"])

    @patch("dogido_server.voice_input.subprocess.run")
    def test_empty_transcript_retries_once_with_the_higher_threshold(
        self,
        run: Mock,
    ) -> None:
        run.side_effect = [
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="",
                stderr="no speech",
            ),
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="[00:00:00.000 --> 00:00:01.000] 聞こえる\n",
                stderr="",
            ),
        ]

        text = transcribe_with_empty_retry(
            Path("/tmp/whisper-cli"),
            Path("/tmp/model.bin"),
            b"\x00\x00" * 160,
            primary_no_speech_thold=0.6,
            retry_no_speech_thold=1.0,
            prompt=NORMAL_STT_PROMPT,
        )

        self.assertEqual("聞こえる", text)
        self.assertEqual(2, run.call_count)
        thresholds = [
            call.args[0][call.args[0].index("--no-speech-thold") + 1]
            for call in run.call_args_list
        ]
        self.assertEqual(["0.6", "1.0"], thresholds)
        prompts = [
            call.args[0][call.args[0].index("--prompt") + 1]
            for call in run.call_args_list
        ]
        self.assertEqual([NORMAL_STT_PROMPT, ""], prompts)

    @patch("dogido_server.voice_input.subprocess.run")
    def test_empty_transcript_does_not_retry_with_a_stricter_threshold(
        self,
        run: Mock,
    ) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="no speech",
        )

        text = transcribe_with_empty_retry(
            Path("/tmp/whisper-cli"),
            Path("/tmp/model.bin"),
            b"\x00\x00" * 160,
            primary_no_speech_thold=0.6,
            retry_no_speech_thold=0.3,
            prompt=NORMAL_STT_PROMPT,
        )

        self.assertIsNone(text)
        self.assertEqual(1, run.call_count)

    @patch("dogido_server.voice_input.subprocess.run")
    def test_short_meaningful_transcript_is_not_rejected(self, run: Mock) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="[00:00:00.000 --> 00:00:01.000] 石炭だ\n",
            stderr="",
        )

        text = transcribe_with_empty_retry(
            Path("/tmp/whisper-cli"),
            Path("/tmp/model.bin"),
            b"\x00\x00" * 160,
            primary_no_speech_thold=0.6,
            retry_no_speech_thold=1.0,
            prompt=NORMAL_STT_PROMPT,
        )

        self.assertEqual("石炭だ", text)
        self.assertEqual(1, run.call_count)

    @patch("dogido_server.voice_input.subprocess.run")
    def test_silero_gate_rejects_only_a_successful_zero_segment_result(
        self,
        run: Mock,
    ) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="Detected 0 speech segments:\n",
            stderr="",
        )
        diagnostics: list[dict[str, object]] = []

        accepted = contains_speech(
            Path("/tmp/vad-cli"),
            Path("/tmp/silero.bin"),
            b"\x00\x00" * 160,
            threshold=0.5,
            diagnostic=lambda **fields: diagnostics.append(fields),
        )

        self.assertFalse(accepted)
        self.assertEqual("no_speech_segment", diagnostics[0]["reason"])

    @patch("dogido_server.voice_input.subprocess.run")
    def test_silero_gate_fails_open_when_probe_output_is_invalid(
        self,
        run: Mock,
    ) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=2,
            stdout="",
            stderr="bad model",
        )
        diagnostics: list[dict[str, object]] = []

        accepted = contains_speech(
            Path("/tmp/vad-cli"),
            Path("/tmp/silero.bin"),
            b"\x00\x00" * 160,
            threshold=0.5,
            diagnostic=lambda **fields: diagnostics.append(fields),
        )

        self.assertTrue(accepted)
        self.assertEqual("silero_probe_failed_open", diagnostics[0]["reason"])

    @patch("dogido_server.voice_input.httpx.post")
    def test_reporter_sends_text_and_reason_without_audio(self, post: Mock) -> None:
        response = Mock()
        post.return_value = response

        report_voice_diagnostic(
            "http://127.0.0.1:5055",
            "secret",
            event="stt_rejected",
            level="warning",
            recognized_text="ごーーー",
            reason="noise_pattern",
            duration_ms=900,
        )

        payload = post.call_args.kwargs["json"]
        self.assertEqual("ごーーー", payload["recognized_text"])
        self.assertEqual("noise_pattern", payload["reason"])
        self.assertEqual(900, payload["duration_ms"])
        self.assertNotIn("audio", payload)
        post.assert_called_once_with(
            "http://127.0.0.1:5055/api/v1/voice-input/diagnostics",
            json=payload,
            headers={"Authorization": "Bearer secret"},
            timeout=1.5,
        )
        response.raise_for_status.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
