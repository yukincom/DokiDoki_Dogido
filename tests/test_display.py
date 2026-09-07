from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

from dogido_server.diagnostics import (
    DIAGNOSTIC_SCHEMA_VERSION,
    DiagnosticHistory,
    DiagnosticLogCapture,
    DiagnosticLogHandler,
    QuietRoutineAccessFilter,
)
from dogido_server.display import DISPLAY_SCHEMA_VERSION, DisplayHistory, RuntimeStatus
from dogido_server.state_machine.types import AudioAction, SpeechReference


def _reference() -> SpeechReference:
    return SpeechReference(
        source_id="mext.course-of-study",
        title_ja="中学校学習指導要領（国語）",
        citation_label_ja="文部科学省",
        locator="第2章",
        url="https://www.mext.go.jp/example",
        source_kind="official_guideline",
    )


class DisplayHistoryTests(unittest.TestCase):
    def test_records_one_segmented_action_as_one_copyable_utterance(self) -> None:
        history = DisplayHistory(max_entries=20)
        ids = history.record_actions(
            [
                AudioAction(
                    layer="speech",
                    interrupt=False,
                    text="一文目。二文目。",
                    speech_segments=("一文目。", "二文目。"),
                    references=(_reference(),),
                    display_player_input_text="枕詞って何？",
                )
            ],
            session_id="ses_a",
            audio_requested=True,
        )

        self.assertEqual(1, len(ids))
        snapshot = history.snapshot(session_id="ses_a")
        self.assertEqual(DISPLAY_SCHEMA_VERSION, snapshot["schema_version"])
        self.assertEqual(1, len(snapshot["utterances"]))
        utterance = snapshot["utterances"][0]
        self.assertEqual("一文目。二文目。", utterance["text"])
        self.assertEqual("knowledge", utterance["category"])
        self.assertEqual("audio_and_text", utterance["output_mode"])
        self.assertEqual("枕詞って何？", utterance["player_input_text"])
        self.assertEqual(1, len(snapshot["references"]))

    def test_reference_list_is_deduplicated_and_session_filtered(self) -> None:
        history = DisplayHistory(max_entries=20)
        for session_id, text in (("ses_a", "一つ目。"), ("ses_a", "二つ目。"), ("ses_b", "別。")):
            history.record_actions(
                [
                    AudioAction(
                        layer="speech",
                        interrupt=False,
                        text=text,
                        references=(_reference(),),
                    )
                ],
                session_id=session_id,
                audio_requested=False,
            )

        snapshot = history.snapshot(session_id="ses_a")
        self.assertEqual(["一つ目。", "二つ目。"], [item["text"] for item in snapshot["utterances"]])
        self.assertEqual(1, len(snapshot["references"]))
        self.assertEqual(2, len(snapshot["references"][0]["utterance_ids"]))

    def test_history_is_bounded_and_ignores_actions_without_text(self) -> None:
        history = DisplayHistory(max_entries=2)
        history.record_actions(
            [AudioAction(layer="panic_cue", interrupt=True, cue_id="scream")],
            session_id="ses_a",
            audio_requested=True,
        )
        for index in range(3):
            history.record_actions(
                [AudioAction(layer="speech", interrupt=False, text=f"発言{index}")],
                session_id="ses_a",
                audio_requested=False,
            )

        snapshot = history.snapshot()
        self.assertEqual(["発言1", "発言2"], [item["text"] for item in snapshot["utterances"]])
        self.assertTrue(snapshot["retention"]["cleared_on_restart"])


class RuntimeStatusTests(unittest.TestCase):
    def test_identifies_worktree_venv_and_tracks_fresh_adapter_presence(self) -> None:
        status = RuntimeStatus(
            heartbeat_interval_ms=5000,
            source_root=Path("/example/.codex/worktrees/2647/DokiDoki-Dogido"),
            python_prefix=Path("/example/dogido-llm"),
            python_base_prefix=Path("/example/python"),
        )
        now = datetime(2026, 9, 4, 21, 0, tzinfo=timezone.utc)

        empty = status.snapshot(now=now)
        self.assertEqual("codex_worktree", empty["runtime"]["source_kind"])
        self.assertEqual("Codex作業ツリー 2647", empty["runtime"]["source_label_ja"])
        self.assertEqual("dogido-llm", empty["runtime"]["python_environment"])
        self.assertTrue(empty["runtime"]["virtual_environment"])
        self.assertEqual("not_connected", empty["minecraft"]["state"])

        status.adapter_seen(
            "ses_a",
            adapter_name="dogido-fabric-client",
            adapter_version="test",
            seen_at=now,
        )
        connected = status.snapshot(now=now + timedelta(seconds=14))
        self.assertTrue(connected["minecraft"]["connected"])
        self.assertEqual(
            ["dogido-fabric-client test"],
            connected["minecraft"]["adapters"],
        )

        stale = status.snapshot(now=now + timedelta(seconds=16))
        self.assertFalse(stale["minecraft"]["connected"])
        self.assertEqual("stale", stale["minecraft"]["state"])

        status.adapter_closed("ses_a")
        closed = status.snapshot(now=now + timedelta(seconds=16))
        self.assertEqual("not_connected", closed["minecraft"]["state"])


class DiagnosticHistoryTests(unittest.TestCase):
    def test_history_is_bounded_and_strips_terminal_escape_sequences(self) -> None:
        history = DiagnosticHistory(max_entries=2)
        history.record(level="info", logger="dogido", message="first")
        history.record(level="warning", logger="dogido", message="\x1b[31msecond\x1b[0m")
        history.record(level="error", logger="dogido", message="third")

        snapshot = history.snapshot()
        self.assertEqual(DIAGNOSTIC_SCHEMA_VERSION, snapshot["schema_version"])
        self.assertEqual(["second", "third"], [row["message"] for row in snapshot["entries"]])
        self.assertTrue(snapshot["retention"]["cleared_on_restart"])

    def test_handler_captures_application_and_http_errors_but_not_routine_success(self) -> None:
        history = DiagnosticHistory(max_entries=20)
        handler = DiagnosticLogHandler(history)
        application = logging.LogRecord(
            "uvicorn.error",
            logging.WARNING,
            __file__,
            1,
            "haiku_decision result=fallback reason=llm_unavailable",
            (),
            None,
        )
        routine_success = logging.LogRecord(
            "uvicorn.access",
            logging.INFO,
            __file__,
            1,
            '%s - "%s %s HTTP/%s" %d',
            ("127.0.0.1:1", "POST", "/api/v1/game-events", "1.1", 202),
            None,
        )
        http_error = logging.LogRecord(
            "uvicorn.access",
            logging.INFO,
            __file__,
            1,
            '%s - "%s %s HTTP/%s" %d',
            ("127.0.0.1:1", "POST", "/api/v1/game-events", "1.1", 500),
            None,
        )

        handler.emit(application)
        handler.emit(routine_success)
        handler.emit(http_error)

        entries = history.snapshot()["entries"]
        self.assertEqual(2, len(entries))
        self.assertIn("llm_unavailable", entries[0]["message"])
        self.assertIn("500", entries[1]["message"])

    def test_access_filter_hides_successful_routine_calls_but_keeps_errors(self) -> None:
        access_filter = QuietRoutineAccessFilter()

        def record(path: str, status: int) -> logging.LogRecord:
            return logging.LogRecord(
                "uvicorn.access",
                logging.INFO,
                __file__,
                1,
                '%s - "%s %s HTTP/%s" %d',
                ("127.0.0.1:1", "GET", path, "1.1", status),
                None,
            )

        self.assertFalse(access_filter.filter(record("/api/v1/display/snapshot", 200)))
        self.assertFalse(access_filter.filter(record("/api/v1/player-input", 200)))
        self.assertTrue(access_filter.filter(record("/api/v1/player-input", 500)))
        self.assertTrue(access_filter.filter(record("/api/v1/adapter-sessions", 201)))

    def test_capture_is_wired_to_access_logger_for_http_errors(self) -> None:
        history = DiagnosticHistory(max_entries=20)
        capture = DiagnosticLogCapture(history)
        access_logger = logging.getLogger("uvicorn.access")

        def record(status: int) -> logging.LogRecord:
            return logging.LogRecord(
                "uvicorn.access",
                logging.INFO,
                __file__,
                1,
                '%s - "%s %s HTTP/%s" %d',
                ("127.0.0.1:1", "POST", "/api/v1/player-input", "1.1", status),
                None,
            )

        try:
            capture.install()
            self.assertIn(capture.handler, access_logger.handlers)
            access_logger.handle(record(200))
            access_logger.handle(record(500))
        finally:
            capture.uninstall()

        entries = history.snapshot()["entries"]
        self.assertEqual(1, len(entries))
        self.assertIn("500", entries[0]["message"])
        self.assertNotIn(capture.handler, access_logger.handlers)


if __name__ == "__main__":
    unittest.main()
