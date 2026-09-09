"""録音子プロセスの選択と回収。AEC失敗時に生マイクへ自動で戻らない。"""

from __future__ import annotations

from collections import deque
import json
import os
from pathlib import Path
import signal
import subprocess
import threading


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ECHO_PYTHON = ROOT / ".dogido_tools/echo-cancel/.venv/bin/python"
DEFAULT_ECHO_HELPER = ROOT / ".dogido_tools/echo-cancel/bin/dogido-audio-capture"


def echo_command(settings) -> list[str]:
    python = Path(settings.voice_echo_python or DEFAULT_ECHO_PYTHON)
    helper = Path(settings.voice_echo_helper or DEFAULT_ECHO_HELPER)
    python = python if python.is_absolute() else ROOT / python
    helper = helper if helper.is_absolute() else ROOT / helper
    for path in (python, helper):
        if not Path(path).is_file() or not os.access(path, os.X_OK):
            raise RuntimeError("AECの準備が必要です: bash scripts/setup_echo_capture.sh")
    command = [str(python), "-m", "dogido_server.echo_input", "--helper", str(helper),
               "--delay-ms", str(settings.voice_echo_delay_ms)]
    if settings.voice_echo_input_uid:
        command += ["--input-uid", settings.voice_echo_input_uid]
    return command


class CaptureProcess:
    """stdoutだけを録音へ渡し、stderrは最大4KiB。所有process groupを停止する。"""

    def __init__(self, process, *, diagnostic=None, own_group=False):
        self.process = process
        self.stdout = process.stdout
        self.own_group = own_group
        self.diagnostic = diagnostic
        self._tail = deque(maxlen=4096)
        self._tail_lock = threading.Lock()
        self._reader = threading.Thread(target=self._drain, name="dogido-capture-log", daemon=True)
        self._reader.start()

    def _drain(self):
        pending = bytearray()
        if self.process.stderr is None:
            return
        while True:
            try:
                chunk = os.read(self.process.stderr.fileno(), 512)
            except (OSError, ValueError):
                return
            if not chunk:
                return
            with self._tail_lock:
                self._tail.extend(chunk)
            pending.extend(chunk)
            while b"\n" in pending:
                line, _, pending = pending.partition(b"\n")
                text = line.decode("utf-8", "replace")[:500]
                print(f"[VOICE] {text}")
                if self.diagnostic:
                    try:
                        event = json.loads(text)
                    except (ValueError, TypeError):
                        continue
                    reason = event.get("reason", "") if isinstance(event, dict) else ""
                    if isinstance(reason, str) and reason.startswith("aec_"):
                        try:
                            self.diagnostic(event="capture", level="error" if reason == "aec_failed" else "info",
                                            reason=reason, detail=text)
                        except Exception:
                            pass  # Optional diagnostics must never stop draining the child pipe.
            if len(pending) > 4096:
                pending.clear()

    def error_tail(self) -> str:
        self._reader.join(timeout=.2)
        with self._tail_lock:
            # Match VoiceInputDiagnosticRequest.detail, including multibyte text.
            return bytes(self._tail).decode("utf-8", "replace")[-800:]

    def read_frame(self, count: int) -> bytes:
        # The unbuffered AEC pipe writes 10 ms; the segmenter reads 30 ms.
        # Pipe read boundaries are not audio frame boundaries.
        data = bytearray()
        while len(data) < count:
            chunk = self.stdout.read(count - len(data))
            if not chunk:
                return b""  # Never deliver a truncated final frame.
            data.extend(chunk)
        return bytes(data)

    def close(self):
        # The sidecar may already have exited while its native child is still alive.
        if self.own_group:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        if self.process.poll() is None:
            try:
                if not self.own_group:
                    self.process.terminate()
            except ProcessLookupError:
                pass
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                if self.own_group:
                    try:
                        os.killpg(self.process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    self.process.kill()
                self.process.wait(timeout=2)
        self._reader.join(timeout=1)
        if self.own_group and self._reader.is_alive():
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self._reader.join(timeout=1)
        if self.stdout:
            self.stdout.close()
        if self.process.stderr:
            self.process.stderr.close()


def spawn_capture(settings, *, raw_factory, diagnostic=None) -> CaptureProcess:
    if settings.voice_echo_cancellation == "off":
        return CaptureProcess(raw_factory(settings.voice_input_device), diagnostic=diagnostic)
    if settings.voice_echo_cancellation != "webrtc":
        raise ValueError("unsupported echo-cancellation backend")
    process = subprocess.Popen(echo_command(settings), cwd=ROOT, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True, bufsize=0)
    return CaptureProcess(process, diagnostic=diagnostic, own_group=True)
