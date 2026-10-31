"""見込み: native worker がモデル寿命と入力寿命を分ければ、取消後も同じモデルを使える。
合格: 一度の初期化で複数入力、先行取消、終了時回収が成立する。
範囲: whisper API は C++ stub。実モデル・認識精度・Metal・マイクは未検証。

WHISPER_CPP_ROOT points to local headers/json.hpp only; the real library is not linked.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import unittest
import wave

ROOT = Path(__file__).resolve().parents[3]


class NativeWhisperWorkerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = os.environ.get("WHISPER_CPP_ROOT")
        compiler = shutil.which("c++")
        if not source or not compiler:
            raise unittest.SkipTest("local C++ compiler and WHISPER_CPP_ROOT are required")
        whisper = Path(source)
        cls.build_dir = tempfile.TemporaryDirectory(prefix="dogido-whisper-native-test-")
        cls.addClassCleanup(cls.build_dir.cleanup)
        cls.binary = Path(cls.build_dir.name) / "dogido-whisper-worker-stub"
        command = [compiler, "-std=c++17", "-pthread", str(ROOT / "native/whisper_worker/main.cpp"),
                   str(Path(__file__).with_name("whisper_worker_stub.cpp"))]
        for path in (whisper / "include", whisper / "ggml/include", whisper / "examples"):
            command.extend(["-isystem", str(path)])
        subprocess.run([*command, "-o", str(cls.binary)], check=True)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="dogido-whisper-native-request-")
        self.addCleanup(self.directory.cleanup)
        self.events = Path(self.directory.name) / "events"
        self.wav = Path(self.directory.name) / "request.wav"
        with wave.open(str(self.wav), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(struct.pack("<hhhh", -32768, 0, 16384, 32767))
        env = {**os.environ, "DOGIDO_WHISPER_STUB_EVENTS": str(self.events)}
        self.process = subprocess.Popen(
            [str(self.binary), "--model", "unused-by-stub", "--no-gpu"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )
        self.addCleanup(self.finish)
        self.output = queue.Queue()
        self.reader = threading.Thread(target=self.read_output, daemon=True)
        self.reader.start()
        self.assertEqual(self.receive(), {"ready": True})

    def read_output(self):
        for line in self.process.stdout:
            self.output.put(json.loads(line))

    def send(self, frame):
        self.process.stdin.write((json.dumps(frame) + "\n").encode())
        self.process.stdin.flush()

    def transcribe(self, ident, prompt, threshold=0.6):
        self.send({"op": "transcribe", "id": ident, "wav": str(self.wav),
                   "prompt": prompt, "no_speech_threshold": threshold})

    def receive(self):
        return self.output.get(timeout=5)

    def logged(self):
        return self.events.read_text().splitlines() if self.events.exists() else []

    def wait_event(self, event):
        deadline = time.monotonic() + 5
        while event not in self.logged():
            if time.monotonic() > deadline:
                self.fail(f"missing stub event {event}")
            time.sleep(0.005)

    def finish(self):
        if self.process.poll() is None:
            self.send({"op": "shutdown"})
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.reader.join(timeout=1)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()

    # 見込み: 各要求のprompt/閾値だけを更新し、重みとCLI既定は維持する。
    # 合格: 二応答の本文が入力別でloadは一回、shutdownでfree。実認識はstub。
    def test_resident_model_preserves_per_request_parameters_and_cli_defaults(self):
        for ident, prompt, threshold in ((1, "first", 0.35), (2, "second", 0.85)):
            self.transcribe(ident, prompt, threshold)
            reply = self.receive()
            self.assertEqual(reply, {"id": ident, "success": True, "stderr": "",
                                    "stdout": f"[00:00:00.000 --> 00:00:01.000]  {prompt}:{threshold:.6f}\n"})
        self.send({"op": "shutdown"})
        self.assertEqual(self.process.wait(timeout=5), 0)
        self.assertEqual(self.logged(), ["load:cpu", "begin:first", "begin:second", "free"])

    # 見込み: 受付時のcancel flagはdequeueで消えず、次の要求を巻き込まない。
    # 合格: 実行中と待機中の二件が取消応答、待機中は推論せず次入力は同じloadで成功。APIはstub。
    def test_cancel_before_dequeue_and_during_inference_keeps_model(self):
        self.transcribe(1, "hold")
        self.wait_event("waiting:hold")
        self.transcribe(2, "must-not-run")
        self.send({"op": "cancel", "id": 2})
        self.send({"op": "cancel", "id": 1})
        for ident in (1, 2):
            self.assertEqual(self.receive(), {"id": ident, "success": False,
                                             "stdout": "", "stderr": "cancelled"})
        self.transcribe(3, "after-cancel")
        self.assertTrue(self.receive()["success"])
        events = self.logged()
        self.assertEqual(events.count("load:cpu"), 1)
        self.assertNotIn("begin:must-not-run", events)
        self.assertIn("abort:compute", events)
        self.assertIn("begin:after-cancel", events)

    # 見込み: 親のstdinが閉じたら計算中でもabortを通知しモデルを解放する。
    # 合格: EOF後5秒以内にexit 0、abortとfreeが残る。OS processとstub callbackのみ。
    def test_eof_aborts_inference_and_frees_model(self):
        self.transcribe(1, "hold")
        self.wait_event("waiting:hold")
        self.process.stdin.close()
        self.assertEqual(self.process.wait(timeout=5), 0)
        self.assertEqual(self.logged(), ["load:cpu", "begin:hold", "waiting:hold", "abort:compute", "free"])

    # 見込み: CLIと同じく、失敗前に得た本文はRustの既存処理へ渡せる。
    # 合格: 非ゼロ結果と部分本文が同じ応答に残り、次要求も同じモデルで成功。実推論失敗はstub。
    def test_partial_output_survives_nonzero_inference_result(self):
        self.transcribe(1, "partial-error")
        reply = self.receive()
        self.assertFalse(reply["success"])
        self.assertIn("partial-error:0.600000", reply["stdout"])
        self.transcribe(2, "next")
        self.assertTrue(self.receive()["success"])
        self.assertEqual(self.logged().count("load:cpu"), 1)


if __name__ == "__main__":
    unittest.main()
