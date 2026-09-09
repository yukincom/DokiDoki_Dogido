"""Core Audioの同期マイク/再生参照をWebRTCへ渡す、ローカル音声入力用sidecar。

標準出力は処理済み16 kHz/mono/s16leのみ。参照音・未処理マイクを保存・送信しない。
録音開始は明示起動時だけ。--check は依存・native自己検査だけで、機器を開かない。
"""

from __future__ import annotations

import argparse
from importlib.metadata import version
import json
import os
from pathlib import Path
import selectors
import signal
import struct
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HELPER = ROOT / ".dogido_tools/echo-cancel/bin/dogido-audio-capture"
HEADER = struct.Struct("<8sIHH")
MAGIC = b"DGAEC1\r\n"
RATE = 16000
FRAME_SAMPLES = 160
INPUT_CHANNELS = 3  # 同一時刻の mic / render_left / render_right
INPUT_BYTES = FRAME_SAMPLES * INPUT_CHANNELS * 2
PINNED_WEBRTC = "0.2.0"


def diagnostic(reason: str, **fields) -> None:
    print(json.dumps({"reason": reason, **fields}, ensure_ascii=False), file=sys.stderr, flush=True)


class WebRTCEchoProcessor:
    """単一スレッド・正確な10ms。ステレオ参照を事前に足して打ち消し合わない。"""

    def __init__(self, *, delay_ms=0, factory=None):
        import numpy as np

        if not isinstance(delay_ms, int) or not 0 <= delay_ms <= 500:
            raise ValueError("AEC delay must be an integer between 0 and 500 ms")
        if factory is None:
            if version("pywebrtc-audio") != PINNED_WEBRTC:
                raise RuntimeError("pywebrtc-audio version differs; run the echo setup script")
            from pywebrtc_audio import AudioProcessor
            factory = AudioProcessor
        self.np = np
        # ラッパーはcapture/renderが同じch数。mono micを複製して2ch AECへ渡す。
        # AGC/NSは足さず、AEC3とその内蔵HPFだけ。部屋の遅延は既定の自動推定。
        self.engine = factory(sample_rate=RATE, num_channels=2, echo_cancellation=True,
                              noise_suppression=False, auto_gain_control=False,
                              high_pass_filter=False, stream_delay_ms=delay_ms)
        self.frames = 0
        self.energy = [0.0, 0.0, 0.0]

    def process(self, pcm: bytes) -> bytes:
        if len(pcm) != INPUT_BYTES:
            raise ValueError("AEC requires a complete synchronized 10 ms frame")
        np = self.np
        samples = np.frombuffer(pcm, dtype="<i2").reshape(FRAME_SAMPLES, INPUT_CHANNELS)
        near = np.repeat(samples[:, 0], 2).astype(np.int16, copy=False)
        far = np.ascontiguousarray(samples[:, 1:3]).reshape(-1)
        cleaned = self.engine.process(near, far)
        if (cleaned.dtype != np.int16 or cleaned.ndim != 1
                or cleaned.size != FRAME_SAMPLES * 2):
            raise ValueError("AEC output format changed")
        # 複製したcaptureの2ch出力だけをmonoへ戻す。int32で加算してoverflowしない。
        mono = (cleaned.reshape(-1, 2).astype(np.int32).sum(axis=1) // 2).astype("<i2")
        for i, value in enumerate((samples[:, 0], samples[:, 1:3], mono)):
            self.energy[i] += float(np.mean(value.astype(np.float64) ** 2))
        self.frames += 1
        return mono.tobytes()

    def levels(self):
        if not self.frames:
            return {}
        result = {key: round((value / self.frames) ** .5, 1) for key, value in
                  zip(("mic_rms", "reference_rms", "clean_rms"), self.energy)}
        self.frames, self.energy = 0, [0.0, 0.0, 0.0]
        return result


def read_exact(stream, count: int, *, timeout: float) -> bytes:
    """端数readを組み直す。EOF/停止を無音や未処理PCMに偽装しない。"""
    data = bytearray()
    deadline = time.monotonic() + timeout
    with selectors.DefaultSelector() as selector:
        selector.register(stream, selectors.EVENT_READ)
        while len(data) < count:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise TimeoutError("audio capture stalled")
            chunk = os.read(stream.fileno(), count - len(data))
            if not chunk:
                raise EOFError("audio capture ended or returned a partial frame")
            data.extend(chunk)
    return bytes(data)


def validate_header(data: bytes) -> None:
    if len(data) != HEADER.size or HEADER.unpack(data) != (MAGIC, RATE, INPUT_CHANNELS, 16):
        raise ValueError("unsupported microphone/render stream header")


def stop_child(process) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
    if process.stdout:
        process.stdout.close()


def interrupted(*_args) -> None:
    raise KeyboardInterrupt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--helper", type=Path, default=DEFAULT_HELPER)
    parser.add_argument("--input-uid", default="", help="空ならmacOS既定マイク。avfoundation番号とは別")
    parser.add_argument("--delay-ms", type=int, default=0)
    parser.add_argument("--check", action="store_true", help="録音せず依存とnative自己検査だけ")
    parser.add_argument("--probe-seconds", type=int, default=0,
                        help="指定秒だけ実音声を取得し、PCMを保存・送信せず音量指標だけ表示")
    args = parser.parse_args(argv)
    if args.probe_seconds < 0 or (args.check and args.probe_seconds):
        parser.error("use --check OR a positive --probe-seconds duration")
    if sys.platform != "darwin":
        parser.error("Core Audio capture requires macOS")
    if not args.helper.is_file() or not os.access(args.helper, os.X_OK):
        parser.error("native helper is missing; run scripts/setup_echo_capture.sh")
    try:
        processor = WebRTCEchoProcessor(delay_ms=args.delay_ms)
        if args.check:
            checked = subprocess.run([str(args.helper), "--self-test"], check=True,
                                     capture_output=True, text=True, timeout=10)
            result = json.loads(checked.stdout)
            if result.get("status") != "passed" or result.get("audio_devices_opened") != 0:
                raise ValueError("invalid native self-test result")
            print(json.dumps({"status": "ready_for_device_test", "webrtc": PINNED_WEBRTC,
                              "native": result, "live_audio_verified": False}, ensure_ascii=False))
            return 0
        command = [str(args.helper), "--capture-system-audio"]
        if args.input_uid:
            command += ["--input-uid", args.input_uid]
        # helperのstderrはそのまま親の有界drainへ。ここに未読PIPEを作らない。
        process = subprocess.Popen(command, stdout=subprocess.PIPE, bufsize=0)
        previous = signal.signal(signal.SIGTERM, interrupted)
        try:
            validate_header(read_exact(process.stdout, HEADER.size, timeout=30))
            diagnostic("aec_started", engine="WebRTC AEC3", reference="system_output_stereo",
                       sample_rate=RATE, frame_ms=10, delay_ms=args.delay_ms)
            last_report = time.monotonic()
            frames = 0
            while True:
                frame = read_exact(process.stdout, INPUT_BYTES, timeout=2)
                cleaned = processor.process(frame)
                frames += 1
                if not args.probe_seconds:
                    sys.stdout.buffer.write(cleaned)
                    sys.stdout.buffer.flush()
                elif frames >= args.probe_seconds * 100:
                    print(json.dumps({"status": "capture_completed", "seconds": args.probe_seconds,
                                      "audio_saved": False, "stt_called": False,
                                      "acoustic_quality_verified": False, **processor.levels()}))
                    return 0
                if time.monotonic() - last_report >= 5:
                    diagnostic("aec_levels", **processor.levels())
                    last_report = time.monotonic()
        finally:
            signal.signal(signal.SIGTERM, previous)
            stop_child(process)
    except (KeyboardInterrupt, BrokenPipeError):
        return 0
    except Exception as exc:
        diagnostic("aec_failed", error=type(exc).__name__, detail=str(exc)[:400])
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
