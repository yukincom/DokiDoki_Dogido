#!/usr/bin/env python3
"""現行Pythonの実際の区切り・Whisper出力処理を、録音／通信なしでfixture化。"""
import contextlib
import io
import json
from pathlib import Path
import random
import struct
import subprocess
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dogido_server import voice_input as voice
from dogido_server.config import Settings


def segments(case):
    frames = [struct.pack("<h", sample) * 480 for sample, count in case["runs"] for _ in range(count)]
    found = []
    class Capture:
        stdout = True
        def read_frame(self, count):
            assert count == 960
            return frames.pop(0) if frames else b""
        def close(self): pass
        def error_tail(self): return ""
    class Worker:
        def __init__(self, *args, **kwargs): pass
        def submit(self, segment):
            found.append({"duration_ms": segment.duration_ms, "voiced_ms": segment.voiced_ms,
                          "samples": [struct.unpack("<h", segment.pcm[i:i+2])[0] for i in range(0, len(segment.pcm), 960)]})
            return True
        def close(self): pass
    class Diagnostics:
        def __init__(self, *args, **kwargs): pass
        def __call__(self, **kwargs): pass
        def close(self): pass
    settings = Settings(_env_file=None, voice_echo_cancellation="webrtc",
        voice_rms_threshold=case["threshold"], voice_silence_ms=case["silence_ms"],
        voice_min_speech_ms=case["minimum_ms"], voice_max_speech_sec=case["maximum_ms"] / 1000)
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(voice, "resolve_whisper_paths", return_value=(Path("cli"), Path("model"))))
        stack.enter_context(patch.object(voice, "resolve_vad_paths", return_value=None))
        stack.enter_context(patch.object(voice, "spawn_capture", return_value=Capture()))
        stack.enter_context(patch.object(voice, "SpeechRecognitionWorker", Worker))
        stack.enter_context(patch.object(voice, "VoiceDiagnosticDispatcher", Diagnostics))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        voice.main(settings=settings, on_transcript=lambda _: None)
    return found


def main():
    rng = random.Random(627)
    cases = []
    runs = [
        [[0, 30], [700, 11], [0, 26]],
        [[0, 30], [699, 11], [0, 26]],
        [[0, 30], [900, 10], [0, 26]],
        [[900, 11], [0, 25]],  # EOF中の未確定区間は配送しない
        [[900, 1001], [0, 30]],
        [[-32768, 11], [0, 26]],
        [[900, 20], [0, 26], [1200, 25], [0, 26]],
    ]
    runs += [[[rng.choice([0, 699, 700, 900, -1500]), rng.randint(1, 50)] for _ in range(25)] for _ in range(20)]
    for index, sequence in enumerate(runs):
        case = dict(name=f"segmentation-{index}", threshold=700, silence_ms=800, minimum_ms=350,
                    maximum_ms=30000 if index < 7 else 1500, runs=sequence)
        case["expected"] = segments(case)
        cases.append(case)
    outputs = [b"", b"log only", "[00 --> 01] ドギド\n[01 --> 02] おはよう".encode(),
        "[00 --> 01] [_BEG_]ゾンビ[_TT_12_]".encode(), b"[00 --> 01] Thank you!",
        "[00 --> 01] ごーーー".encode(), b"log \xff\n[00 --> 01] hello", b"[00 --> 01] \xff",
        b"[00 --> 01] [__]", "[00 --> 01] ThanKyou".encode(), b"[00 --> 01] Thank you, Dogido"]
    transcripts = []
    for output in outputs:
        diagnostics = []
        result = subprocess.CompletedProcess([], 0, output.decode("utf-8", "replace"), "")
        with patch.object(voice.subprocess, "run", return_value=result), contextlib.redirect_stdout(io.StringIO()):
            text = voice.transcribe(Path("cli"), Path("model"), b"\0\0", no_speech_thold=0.6, diagnostic=lambda **v: diagnostics.append(v))
        transcripts.append({"stdout": list(output), "text": text,
            "reason": next((d["reason"] for d in diagnostics if d["event"] == "stt_rejected"), None)})
    target = ROOT / "dogido-rust/fixtures/voice-parity.json"
    target.write_text(json.dumps({"segments": cases, "transcripts": transcripts}, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"Python参照: 区切り{len(cases)}件、文字起こし{len(transcripts)}件 → {target.name}")

if __name__ == "__main__": main()
