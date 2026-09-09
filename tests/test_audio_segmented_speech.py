"""文単位音声を、一つの論理batchのまま安全に配送する境界。"""

from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from dogido_server.audio import AudioDispatcher, RunningAudio, SpeechBackend
from dogido_server.config import Settings
from dogido_server.state_machine import AudioAction


class _FakeProcess:
    def __init__(self, *, running: bool = False) -> None:
        self.running = running
        self.terminated = False
        self.killed = False

    def poll(self):  # type: ignore[no-untyped-def]
        return None if self.running and not self.terminated else 0

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return 0

    def kill(self) -> None:
        self.killed = True
        self.terminated = True


class _RecordingSpeechBackend(SpeechBackend):
    def __init__(self) -> None:
        self.prepared: list[str] = []
        self.played: list[str] = []

    def start(self, text: str, *, speed_scale: float | None = None) -> RunningAudio:
        del speed_scale
        self.played.append(text)
        return RunningAudio(process=_FakeProcess())  # type: ignore[arg-type]

    def prepare(self, text: str, *, speed_scale: float | None = None) -> object:
        del speed_scale
        self.prepared.append(text)
        return text

    def start_prepared(self, prepared: object) -> RunningAudio:
        return self.start(str(prepared))


class _SlowPrepareBackend(_RecordingSpeechBackend):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()

    def prepare(self, text: str, *, speed_scale: float | None = None) -> object:
        del speed_scale
        self.prepared.append(text)
        self.started.set()
        self.release.wait(timeout=2.0)
        self.finished.set()
        return text


def _dispatcher(*, max_pending: int = 8, on_playback_event=None) -> AudioDispatcher:
    with patch("dogido_server.audio.threading.Thread.start"):
        return AudioDispatcher(
            Settings(
                audio_enabled=False,
                tts_backend="noop",
                cue_backend="noop",
                audio_max_pending_batches=max_pending,
            ),
            on_playback_event=on_playback_event,
        )


class SegmentedSpeechTests(unittest.TestCase):
    def test_haiku_preface_is_prepared_and_played_without_truncation(self) -> None:
        dispatcher = _dispatcher()
        self.addCleanup(dispatcher.close)
        backend = _RecordingSpeechBackend()
        dispatcher.speech_backend = backend
        dispatcher.fallback_speech_backend = backend
        text = "窓辺の夕暮れ、穏やかな草地が見えるわ、なんか浮かんできたわ"
        action = AudioAction(
            layer="speech",
            interrupt=False,
            text=text,
            speech_profile="haiku",
        )

        handle, stale = dispatcher._start_action(  # noqa: SLF001
            action,
            expected_epoch=0,
        )
        if handle is not None:
            dispatcher._wait_for(handle)  # noqa: SLF001

        self.assertFalse(stale)
        self.assertEqual([text], backend.prepared)
        self.assertEqual([text], backend.played)

    def test_segments_are_played_in_order_inside_one_action(self) -> None:
        dispatcher = _dispatcher()
        self.addCleanup(dispatcher.close)
        backend = _RecordingSpeechBackend()
        dispatcher.speech_backend = backend
        dispatcher.fallback_speech_backend = backend
        action = AudioAction(
            layer="speech",
            interrupt=False,
            text="一文目。二文目。参照資料です。",
            speech_segments=("一文目。", "二文目。", "参照資料です。"),
            speech_segment_pause_ms=0,
        )

        stale = dispatcher._play_segmented_speech(action, expected_epoch=0)  # noqa: SLF001

        self.assertFalse(stale)
        self.assertEqual(list(action.speech_segments), backend.prepared)
        self.assertEqual(list(action.speech_segments), backend.played)

    def test_segmented_action_occupies_only_one_pending_batch(self) -> None:
        dispatcher = _dispatcher()
        self.addCleanup(dispatcher.close)
        action = AudioAction(
            layer="speech",
            interrupt=False,
            text="一。二。三。",
            speech_segments=("一。", "二。", "三。"),
        )

        dispatcher.play_actions([action])

        self.assertEqual(1, len(dispatcher._pending))  # noqa: SLF001
        _, queued = dispatcher._pending[0]  # noqa: SLF001
        self.assertEqual([action], queued)

    def test_pause_wakes_immediately_when_epoch_changes(self) -> None:
        dispatcher = _dispatcher()
        self.addCleanup(dispatcher.close)
        result: list[bool] = []
        waiter = threading.Thread(
            target=lambda: result.append(
                dispatcher._wait_segment_pause(0, 10_000)  # noqa: SLF001
            )
        )
        waiter.start()
        time.sleep(0.03)

        dispatcher.play_actions([AudioAction(layer="flush", interrupt=True)])
        waiter.join(timeout=1.0)

        self.assertFalse(waiter.is_alive())
        self.assertEqual([True], result)

    def test_stale_voicevox_prepare_never_starts_playback(self) -> None:
        dispatcher = _dispatcher()
        self.addCleanup(dispatcher.close)
        backend = _SlowPrepareBackend()
        dispatcher.speech_backend = backend
        dispatcher.fallback_speech_backend = backend
        action = AudioAction(layer="speech", interrupt=False, text="古い説明。")
        result: list[tuple[RunningAudio | None, bool]] = []
        starter = threading.Thread(
            target=lambda: result.append(
                dispatcher._start_speech_text(  # noqa: SLF001
                    action,
                    action.text or "",
                    expected_epoch=0,
                    interrupt=False,
                )
            )
        )
        starter.start()
        self.assertTrue(backend.started.wait(timeout=1.0))

        dispatcher.play_actions([AudioAction(layer="flush", interrupt=True)])
        starter.join(timeout=1.0)
        backend.release.set()
        self.assertTrue(backend.finished.wait(timeout=1.0))

        self.assertFalse(starter.is_alive())
        self.assertEqual([(None, True)], result)
        self.assertEqual([], backend.played)

    def test_full_queue_replaces_matching_foreground_and_drops_new_normal(self) -> None:
        dispatcher = _dispatcher(max_pending=2)
        self.addCleanup(dispatcher.close)
        old = AudioAction(
            layer="speech",
            interrupt=False,
            text="古い知識回答",
            queue_priority="foreground",
            queue_replace_key="knowledge_reply",
        )
        ambient = AudioAction(layer="speech", interrupt=False, text="環境コメント")
        dispatcher.play_actions([old])
        dispatcher.play_actions([ambient])

        replacement = AudioAction(
            layer="speech",
            interrupt=False,
            text="新しい知識回答",
            queue_priority="foreground",
            queue_replace_key="knowledge_reply",
        )
        dispatcher.play_actions([replacement])
        dispatcher.play_actions(
            [AudioAction(layer="speech", interrupt=False, text="満杯後の通常発話")]
        )

        queued_texts = [
            actions[0].text
            for _, actions in dispatcher._pending  # noqa: SLF001
        ]
        self.assertEqual(["新しい知識回答", "環境コメント"], queued_texts)

    def test_hard_panic_bypasses_protect_and_skips_breath_fadeout(self) -> None:
        dispatcher = _dispatcher()
        self.addCleanup(dispatcher.close)
        process = _FakeProcess(running=True)
        dispatcher._current = RunningAudio(  # noqa: SLF001
            process=process,  # type: ignore[arg-type]
            cue_id="suppressed_breath",
        )
        dispatcher._current_protected_until = time.monotonic() + 30.0  # noqa: SLF001
        control = AudioAction(layer="control", interrupt=True)
        warden = AudioAction(
            layer="panic_cue",
            interrupt=True,
            cue_id="warden_sonic_boom_scream",
        )

        dispatcher.play_actions([control, warden])

        self.assertTrue(process.terminated)
        self.assertEqual(1, dispatcher._epoch)  # noqa: SLF001
        self.assertEqual(1, len(dispatcher._pending))  # noqa: SLF001
        _, queued = dispatcher._pending[0]  # noqa: SLF001
        self.assertEqual([warden], queued)
        self.assertNotIn(
            "suppressed_breath_fadeout",
            [action.cue_id for action in queued],
        )

    def test_playback_callback_reports_queued_started_completed_in_order(self) -> None:
        events: list[dict[str, str]] = []
        dispatcher = AudioDispatcher(
            Settings(audio_enabled=False, tts_backend="noop", cue_backend="noop"),
            on_playback_event=lambda event: events.append(dict(event)),
        )
        self.addCleanup(dispatcher.close)
        backend = _RecordingSpeechBackend()
        dispatcher.speech_backend = backend
        dispatcher.fallback_speech_backend = backend
        action = AudioAction(
            layer="speech",
            interrupt=False,
            text="最後まで読めた返事。",
            utterance_id="utt-complete",
            conversation_turn_id="turn-complete",
            route_owner="player_chat",
            playback_session_id="session-1",
        )

        dispatcher.play_actions([action])
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and not any(
            event["status"] == "completed" for event in events
        ):
            time.sleep(0.005)

        self.assertEqual(
            ["queued", "started", "completed"],
            [event["status"] for event in events],
        )
        self.assertTrue(all(event["utterance_id"] == "utt-complete" for event in events))
        self.assertTrue(all(event["session_id"] == "session-1" for event in events))

    def test_replaced_and_closed_pending_speech_are_reported_cancelled(self) -> None:
        events: list[dict[str, str]] = []
        dispatcher = _dispatcher(max_pending=1, on_playback_event=events.append)
        old = AudioAction(
            layer="speech",
            interrupt=False,
            text="古い返事",
            utterance_id="old",
            queue_replace_key="main_language_reply",
        )
        replacement = AudioAction(
            layer="speech",
            interrupt=False,
            text="新しい返事",
            utterance_id="replacement",
            queue_replace_key="main_language_reply",
        )

        dispatcher.play_actions([old])
        dispatcher.play_actions([replacement])
        dispatcher.close()

        self.assertIn(
            ("old", "cancelled", "queue_replaced"),
            [
                (event["utterance_id"], event["status"], event["resolution"])
                for event in events
            ],
        )
        self.assertIn(
            ("replacement", "cancelled", "dispatcher_closed"),
            [
                (event["utterance_id"], event["status"], event["resolution"])
                for event in events
            ],
        )


if __name__ == "__main__":
    unittest.main()
