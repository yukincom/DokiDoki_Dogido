"""Rustへ渡す録音コマンド。AEC失敗時に生マイクへ戻さない。"""
import os
from pathlib import Path
import shutil

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


def capture_command(settings):
    if settings.voice_echo_cancellation == "webrtc":
        return echo_command(settings)
    if settings.voice_echo_cancellation != "off":
        raise ValueError("unsupported echo-cancellation backend")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg が見つかりません。録音用の既存環境を確認してください。")
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "avfoundation",
            "-i", settings.voice_input_device, "-ac", "1", "-ar", "16000", "-f", "s16le", "-"]
