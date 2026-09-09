"""独立音声試験のI/O。PCMは既存voice_input子プロセス内だけで扱う。"""

from collections import deque
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from uuid import uuid4

from dogido_server.voice_capture import CaptureProcess, ROOT


class Inbox:
    """認識待ちは既存STT設定に従って有界。制御・再生完了は捨てない。"""

    def __init__(self, max_voice, max_age, record, clock=time.monotonic):
        self.max_voice, self.max_age, self.record, self.clock = max_voice, max_age, record, clock
        self.items = deque()
        self.condition = threading.Condition()

    def put(self, event):
        event = dict(event, received_at=self.clock())
        removed = None
        with self.condition:
            if event['kind'] == 'recognized':
                voices = [item for item in self.items if item['kind'] == 'recognized']
                if len(voices) >= self.max_voice:
                    removed = voices[0]
                    self.items.remove(removed)
            self.items.append(event)
            self.condition.notify()
        if removed:
            self.record(dict(kind='input_dropped', reason='pending_replaced', text=removed['text']))

    def get(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: bool(self.items))
                item = self.items.popleft()
            if item['kind'] == 'recognized' and self.clock() - item['received_at'] > self.max_age:
                self.record(dict(kind='input_dropped', reason='stale', text=item['text']))
                continue
            return item


def capture_worker():
    """stdoutはJSONのみ。HTTPへ配送せず、録音・区切り・STTは本体と共通。"""
    from contextlib import redirect_stdout
    from dogido_server.config import Settings
    from dogido_server.voice_input import main

    output, lock = sys.stdout, threading.Lock()

    def emit(kind, **fields):
        with lock:
            output.write(json.dumps(dict(kind=kind, **fields), ensure_ascii=False) + '\n')
            output.flush()

    def stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    with redirect_stdout(sys.stderr):
        main(settings=Settings().model_copy(update={'voice_echo_cancellation': 'webrtc'}),
             on_transcript=lambda text: emit('recognized', text=text),
             diagnostic_sink=lambda **fields: emit('diagnostic', **fields),
             on_ready=lambda: emit('capture_ready'),
             on_stopped=lambda: emit('capture_stopped'))


class Microphone:
    def __init__(self, emit):
        self.emit, self.closed = emit, threading.Event()
        self.close_lock = threading.Lock()
        # STT workerが中断された場合も、親が所有groupの終了後に一時WAVを回収する。
        self.temporary = tempfile.TemporaryDirectory(prefix='dogido-voice-stt-')
        process = subprocess.Popen(
            [sys.executable, '-m', 'dogido_server.language_dialogue.voice_test', '--capture-worker'],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, bufsize=0, env={**os.environ, 'TMPDIR': self.temporary.name},
        )
        self.capture = CaptureProcess(process, own_group=True)
        self.reader = threading.Thread(target=self._read, name='voice-test-input', daemon=True)
        self.reader.start()

    def _read(self):
        try:
            while not self.closed.is_set():
                line = self.capture.stdout.readline(65537)
                if not line:
                    break
                if len(line) > 65536:
                    raise ValueError('capture protocol line too long')
                event = json.loads(line)
                if not isinstance(event, dict) or event.get('kind') not in {
                    'recognized', 'diagnostic', 'capture_ready', 'capture_stopped'
                }:
                    raise ValueError('invalid capture protocol')
                self.emit(event)
        except Exception as exc:
            if not self.closed.is_set():
                self.emit(dict(kind='capture_error', reason=type(exc).__name__))
        finally:
            if not self.closed.is_set():
                self.emit(dict(kind='capture_stopped', detail=self.capture.error_tail()))

    def close(self):
        with self.close_lock:
            if not self.closed.is_set():
                self.closed.set()
                self.capture.close()
                self.reader.join(timeout=2)
                self.temporary.cleanup()


class Playback:
    """合成と再生だけを別スレッドへ。マイクを停止・ミュートするAPIは持たない。"""

    def __init__(self, backend, emit, *, max_pending, speed_scale):
        self.backend, self.emit, self.speed_scale = backend, emit, speed_scale
        self.max_pending = max_pending
        self.condition = threading.Condition(threading.RLock())
        self.items, self.epoch, self.closed, self.running = deque(), 0, False, None
        self.thread = threading.Thread(target=self._run, name='voice-test-playback', daemon=True)
        self.thread.start()

    def submit(self, text, *, utterance_id=None, departure=False, refresh_token=None):
        item = dict(text=text, utterance_id=utterance_id or 'voice-test:' + uuid4().hex,
                    departure=departure, refresh_token=refresh_token)
        with self.condition:
            item['epoch'] = self.epoch
            if self.closed or len(self.items) >= self.max_pending:
                self.emit(dict(item, kind='playback_result', status='failed', reason='queue_unavailable'))
                return item['utterance_id']
            self.items.append(item)
            self.condition.notify()
            return item['utterance_id']

    def valid(self, event):
        with self.condition:
            return not self.closed and event.get('epoch') == self.epoch

    def cancel(self):
        with self.condition:
            self.epoch += 1
            pending = list(self.items)
            self.items.clear()
            if self.running and self.running.process.poll() is None:
                try:
                    self.running.process.terminate()
                except ProcessLookupError:
                    pass
            self.condition.notify_all()
        for item in pending:
            self.emit(dict(item, kind='playback_result', status='cancelled'))

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.items)
                if self.closed:
                    return
                item = self.items.popleft()
            running = None
            status, reason = 'failed', ''
            try:
                prepared = self.backend.prepare(item['text'], speed_scale=self.speed_scale)
                with self.condition:
                    if not self.valid(item):
                        status = 'cancelled'
                    else:
                        running = self.backend.start_prepared(prepared)
                        self.running = running
                if running:
                    self.emit(dict(item, kind='playback_started'))
                    while running.process.poll() is None:
                        try:
                            running.process.wait(timeout=.2)
                        except subprocess.TimeoutExpired:
                            if not self.valid(item):
                                running.process.kill()
                                running.process.wait(timeout=2)
                    status = ('completed' if running.process.returncode == 0 else 'failed')
                    if not self.valid(item):
                        status = 'cancelled'
            except Exception as exc:
                reason = type(exc).__name__
            finally:
                if running and running.process.poll() is None:
                    running.process.kill()
                    running.process.wait(timeout=2)
                if running and running.cleanup_path:
                    running.cleanup_path.unlink(missing_ok=True)
                with self.condition:
                    self.running = None
                self.emit(dict(item, kind='playback_result', status=status, reason=reason))

    def close(self):
        with self.condition:
            self.closed = True
        self.cancel()
        # VOICEVOXの既存APIタイムアウトは15秒×2。マイクは呼出元が先に停止。
        self.thread.join(timeout=35)
        return not self.thread.is_alive()
