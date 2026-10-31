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
    # 見込み: 同じモデル所有プロセスが複数要求と取消を受ける。
    # 合格: 取消は当該要求だけに働き、次の要求が同じPIDで完了する。Whisper自体は模擬。
    import threading
    cancelled = threading.Event()
    output_lock = threading.Lock()
    active = None
    active_id = None
    if config.get("hang_load"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        time.sleep(120)
    def response(value):
        with output_lock:
            line = json.dumps(value) + "\n"
            if config.get("split_first_response") and value.get("id") == 1:
                midpoint = len(line) // 2
                sys.stdout.write(line[:midpoint]); sys.stdout.flush()
                (folder / "partial-frame").touch()
                cancelled.wait(5)
                sys.stdout.write(line[midpoint:]); sys.stdout.flush()
            else:
                sys.stdout.write(line); sys.stdout.flush()
    def transcribe(request):
        current = json.loads(Path(os.environ["DOGIDO_VOICE_TEST_CONFIG"]).read_text())
        path = Path(request["wav"])
        with wave.open(str(path), "rb") as wav:
            assert wav.getnchannels() == 1 and wav.getsampwidth() == 2 and wav.getframerate() == 16000
            pcm = wav.readframes(wav.getnframes())
        sample = next((s[0] for s in struct.iter_unpack("<h", pcm) if s[0]), 0)
        # 既存試験のprompt・閾値・入力PCMの観測を保つ。
        args = ["--prompt", request["prompt"], "--no-speech-thold", str(request["no_speech_threshold"])]
        log(event="transcribe", args=args, request_id=request["id"])
        log(event="wav", path=str(path), sample=sample)
        first = not (folder / "whisper-started").exists()
        (folder / "whisper-started").touch()
        if current.get("hang_whisper"):
            cancelled.wait(120)
        else:
            cancelled.wait(current.get("first_delay", 0) if first else 0)
        if cancelled.is_set():
            log(event="cancelled", request_id=request["id"])
            response(dict(id=request["id"], success=False, stdout="", stderr="cancelled"))
            return
        if current.get("empty_first") and request["prompt"]:
            output = ""
        else:
            output = current.get("output", f"[00 --> 01] ドギド {sample}\n")
            if current.get("bad_log"): output = "log \ufffd\n" + output
            if current.get("bad_text"): output = "[00 --> 01] \ufffd\n"
        response(dict(id=request["id"], success=not current.get("whisper_exit", 0),
                      stdout=output, stderr="no speech" if not output else ""))
    response({"ready": True})
    for line in sys.stdin:
        request = json.loads(line)
        if request["op"] == "cancel":
            if request["id"] == active_id: cancelled.set()
        elif request["op"] == "transcribe":
            if active: active.join()
            cancelled.clear()
            active_id = request["id"]
            active = threading.Thread(target=transcribe, args=(request,))
            active.start()
        elif request["op"] == "shutdown":
            break
    cancelled.set()
    if active: active.join()

else:
    raise RuntimeError(role)
