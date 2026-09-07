"""ゲーム外の診断画面へ出す、上限付きの非永続ログ。

Uvicorn の定常アクセスログは対象外とし、ドギドの判断、LLM、川柳、
STT/TTS などの運用ログをプロセス内だけに保持する。音声波形、認証情報、
内部プロンプトは保存しない。
"""
from __future__ import annotations

import logging
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from threading import RLock

DIAGNOSTIC_SCHEMA_VERSION = 1
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_QUIET_ACCESS_PATHS = {
    "/healthz",
    "/api/v1/display/snapshot",
    "/api/v1/game-events",
    "/api/v1/game-events/batch",
    "/api/v1/player-input",
    "/api/v1/voice-input/context",
    "/api/v1/voice-input/diagnostics",
}


def _is_quiet_routine_access(record: logging.LogRecord) -> bool:
    """成功した高頻度APIか。壊れた書式や400以上は診断対象に残す。"""

    if record.name != "uvicorn.access":
        return False
    try:
        args = record.args
        if not isinstance(args, tuple) or len(args) < 5:
            return False
        path = str(args[2]).split("?", 1)[0]
        status_code = int(args[4])
        is_heartbeat = (
            path.startswith("/api/v1/adapter-sessions/")
            and path.endswith("/heartbeat")
        )
        return status_code < 400 and (path in _QUIET_ACCESS_PATHS or is_heartbeat)
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True, slots=True)
class DiagnosticEntry:
    entry_id: str
    created_at: str
    level: str
    logger: str
    source: str
    event: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {
            "entry_id": self.entry_id,
            "created_at": self.created_at,
            "level": self.level,
            "logger": self.logger,
            "source": self.source,
            "event": self.event,
            "message": self.message,
        }


class DiagnosticHistory:
    """スレッド安全な、起動中だけの診断ログ。"""

    def __init__(self, *, max_entries: int = 1000) -> None:
        self.max_entries = max(1, int(max_entries))
        self._entries: deque[DiagnosticEntry] = deque(maxlen=self.max_entries)
        self._revision = 0
        self._lock = RLock()

    @staticmethod
    def _clean_message(message: object) -> str:
        text = _ANSI_ESCAPE.sub("", str(message or "")).replace("\r", "")
        text = text.strip()
        if len(text) > 4000:
            return text[:3997] + "..."
        return text

    def record(
        self,
        *,
        level: str,
        logger: str,
        message: object,
        source: str = "server",
        event: str = "log",
        created_at: datetime | None = None,
    ) -> str | None:
        clean_message = self._clean_message(message)
        if not clean_message:
            return None
        with self._lock:
            self._revision += 1
            entry_id = f"log_{self._revision}"
            timestamp = created_at or datetime.now().astimezone()
            self._entries.append(
                DiagnosticEntry(
                    entry_id=entry_id,
                    created_at=timestamp.astimezone().isoformat(),
                    level=str(level or "INFO").upper(),
                    logger=str(logger or "dogido"),
                    source=str(source or "server"),
                    event=str(event or "log"),
                    message=clean_message,
                )
            )
            return entry_id

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
                "revision": self._revision,
                "entries": [entry.as_dict() for entry in self._entries],
                "retention": {
                    "storage": "process_memory",
                    "max_entries": self.max_entries,
                    "cleared_on_restart": True,
                },
            }


class DiagnosticLogHandler(logging.Handler):
    """Pythonログを診断履歴へ写す。定常APIの成功ログだけは写さない。"""

    def __init__(self, history: DiagnosticHistory) -> None:
        super().__init__(level=logging.INFO)
        self.history = history

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if getattr(record, "dogido_diagnostic_skip", False):
                return
            if getattr(record, "dogido_diagnostic_captured", False):
                return
            if _is_quiet_routine_access(record):
                return
            record.dogido_diagnostic_captured = True
            self.history.record(
                level=record.levelname,
                logger=record.name,
                message=record.getMessage(),
                created_at=datetime.fromtimestamp(record.created).astimezone(),
            )
        except Exception:  # noqa: BLE001 - logging handler は本処理へ失敗を返さない
            self.handleError(record)


class QuietRoutineAccessFilter(logging.Filter):
    """成功した高頻度APIだけをUvicornの端末アクセスログから除く。"""

    def filter(self, record: logging.LogRecord) -> bool:
        return not _is_quiet_routine_access(record)


class DiagnosticLogCapture:
    """root と uvicorn.error へ同じ handler を安全に着脱する。"""

    def __init__(self, history: DiagnosticHistory) -> None:
        self.handler = DiagnosticLogHandler(history)
        self.access_filter = QuietRoutineAccessFilter()
        self._access_logger = logging.getLogger("uvicorn.access")
        self._loggers = (
            logging.getLogger(),
            logging.getLogger("uvicorn.error"),
            self._access_logger,
        )
        self._installed = False

    def install(self) -> None:
        if self._installed:
            return
        for logger in self._loggers:
            logger.addHandler(self.handler)
        self._access_logger.addFilter(self.access_filter)
        self._installed = True

    def uninstall(self) -> None:
        if not self._installed:
            return
        for logger in self._loggers:
            logger.removeHandler(self.handler)
        self._access_logger.removeFilter(self.access_filter)
        self._installed = False


__all__ = [
    "DIAGNOSTIC_SCHEMA_VERSION",
    "DiagnosticEntry",
    "DiagnosticHistory",
    "DiagnosticLogCapture",
    "DiagnosticLogHandler",
    "QuietRoutineAccessFilter",
]
