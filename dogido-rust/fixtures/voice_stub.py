"""音声入力の模擬試験専用。マイク・モデル・再生装置は開かない。"""
import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import time
import wave

config = json.loads(Path(os.environ["DOGIDO_VOICE_TEST_CONFIG"]).read_text())
folder = Path(config["folder"])
role = sys.argv[1] if Path(sys.argv[0]).stem == "voice_stub" else Path(sys.argv[0]).name

def log(**event):
    fd = os.open(folder / "children.jsonl", os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try: os.write(fd, (json.dumps({"role": role, "pid": os.getpid(), **event}) + "\n").encode())
    finally: os.close(fd)

log(event="started", args=sys.argv[1:])
if role == "capture":
    # UTF-8の途中でpipeが区切れても診断の日本語を壊さない。
    message = (json.dumps({"reason": "aec_started", "detail": "模擬録音"}, ensure_ascii=False) + "\n").encode()
    for byte in message: os.write(2, bytes([byte]))
    grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log(event="grandchild", child_pid=grandchild.pid)
    def stop(*_):
        grandchild.terminate()
        grandchild.wait(timeout=1)
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    def write(data):
        # pipeの境界は30msフレームと一致しない。
        while data:
            n = os.write(1, data[:137])
            data = data[n:]
    def speech(sample):
        write(b"\0" * 960 * 10 + struct.pack("<h", sample) * 480 * 11 + b"\0" * 960 * 26)
    if config.get("partial_only"):
        write(b"\1" * 959)
        stop()
    speech(900)
    if config.get("extra_segments") or config.get("eof_during_stt"):
        until = time.monotonic() + 5
        while not (folder / "whisper-started").exists():
            if time.monotonic() > until: raise RuntimeError("STT did not start")
            time.sleep(.01)
        if config.get("eof_during_stt"):
            write(b"\1")
            stop()
        for sample in config["extra_segments"]: speech(sample)
    while True:
        if not config.get("stall_capture"): write(b"\0" * 960)
        time.sleep(.015)
elif role == "vad":
    if config.get("vad_failure"):
        print("VAD failure", file=sys.stderr)
        raise SystemExit(2)
    print(f"Detected {config.get('vad_count', 1)} speech segments")
elif role == "whisper":
    args = sys.argv
    path = Path(args[args.index("-f") + 1])
    with wave.open(str(path), "rb") as wav:
        assert wav.getnchannels() == 1 and wav.getsampwidth() == 2 and wav.getframerate() == 16000
        pcm = wav.readframes(wav.getnframes())
    sample = next((s[0] for s in struct.iter_unpack("<h", pcm) if s[0]), 0)
    log(event="wav", path=str(path), sample=sample)
    first = not (folder / "whisper-started").exists()
    (folder / "whisper-started").touch()
    if config.get("hang_whisper"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        time.sleep(120)
    time.sleep(config.get("first_delay", 0) if first else 0)
    prompt = args[args.index("--prompt") + 1]
    if config.get("empty_first") and prompt:
        print("no speech", file=sys.stderr)
    else:
        output = config.get("output", f"[00 --> 01] ドギド {sample}\n").encode()
        if config.get("bad_log"): output = b"log \xff\n" + output
        if config.get("bad_text"): output = b"[00 --> 01] \xff\n"
        sys.stdout.buffer.write(output)
    raise SystemExit(config.get("whisper_exit", 0))
else:
    raise RuntimeError(role)
