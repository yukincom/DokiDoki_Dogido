from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

try:
    import numpy as np
except ImportError:
    np = None

from dogido_server.config import Settings
from dogido_server.models import VoiceInputDiagnosticRequest
from dogido_server.echo_input import (
    FRAME_SAMPLES, HEADER, INPUT_BYTES, MAGIC, RATE, WebRTCEchoProcessor,
    read_exact, validate_header,
    main as echo_main,
)
from dogido_server.voice_capture import CaptureProcess, echo_command, spawn_capture


@unittest.skipUnless(np is not None, 'optional echo DSP tests require NumPy')
class EchoProcessorTests(unittest.TestCase):
    def test_preserves_stereo_reference_and_duplicates_mono_capture(self):
        engine = Mock()
        engine.process.side_effect = lambda near, far: near
        factory = Mock(return_value=engine)
        processor = WebRTCEchoProcessor(factory=factory)
        samples = np.tile([1234, 15000, -15000], (FRAME_SAMPLES, 1)).astype('<i2')
        output = processor.process(samples.tobytes())
        near, far = engine.process.call_args.args
        np.testing.assert_array_equal(near, np.full(320, 1234, dtype=np.int16))
        np.testing.assert_array_equal(far.reshape(-1, 2), samples[:, 1:])
        np.testing.assert_array_equal(np.frombuffer(output, '<i2'), samples[:, 0])
        self.assertEqual(15000, processor.levels()['reference_rms'])
        self.assertEqual({}, processor.levels())
        self.assertEqual(2, factory.call_args.kwargs['num_channels'])
        self.assertTrue(factory.call_args.kwargs['echo_cancellation'])
        self.assertFalse(factory.call_args.kwargs['noise_suppression'])
        self.assertFalse(factory.call_args.kwargs['auto_gain_control'])

    def test_output_average_does_not_overflow(self):
        engine = Mock()
        engine.process.return_value = np.full(320, 30000, dtype=np.int16)
        output = WebRTCEchoProcessor(factory=lambda **_: engine).process(bytes(INPUT_BYTES))
        self.assertTrue(np.all(np.frombuffer(output, '<i2') == 30000))

    def test_bad_frame_or_changed_engine_output_is_rejected(self):
        for result in (np.zeros(160, np.int16), np.zeros(320, np.float32), np.zeros((160, 2), np.int16)):
            with self.subTest(shape=result.shape, dtype=result.dtype):
                engine = Mock(process=Mock(return_value=result))
                processor = WebRTCEchoProcessor(factory=lambda **_: engine)
                with self.assertRaises(ValueError):
                    processor.process(bytes(INPUT_BYTES))
        processor = WebRTCEchoProcessor(factory=Mock())
        with self.assertRaises(ValueError):
            processor.process(b'partial')
        for delay in (-1, 501, 1.5):
            with self.assertRaises(ValueError):
                WebRTCEchoProcessor(delay_ms=delay, factory=Mock())

    def test_wrong_installed_version_is_rejected(self):
        with patch('dogido_server.echo_input.version', return_value='99.0'):
            with self.assertRaises(RuntimeError):
                WebRTCEchoProcessor()


class EchoTransportTests(unittest.TestCase):
    def test_header_contract(self):
        validate_header(HEADER.pack(MAGIC, RATE, 3, 16))
        for bad in (b'', HEADER.pack(MAGIC, 48000, 3, 16), HEADER.pack(MAGIC, RATE, 1, 16)):
            with self.assertRaises(ValueError):
                validate_header(bad)

    def test_partial_pipe_reads_are_reassembled(self):
        reader, writer = os.pipe()
        def send():
            try:
                for part in (b'ab', b'cde', b'fgh'):
                    os.write(writer, part)
            finally:
                os.close(writer)
        sender = threading.Thread(target=send)
        sender.start()
        with os.fdopen(reader, 'rb', buffering=0) as stream:
            self.assertEqual(b'abcdefgh', read_exact(stream, 8, timeout=1))
            with self.assertRaises(EOFError):
                read_exact(stream, 1, timeout=1)
        sender.join(1)

    def test_timeout_does_not_fabricate_silence(self):
        reader, writer = os.pipe()
        try:
            with os.fdopen(reader, 'rb', buffering=0) as stream:
                with self.assertRaises(TimeoutError):
                    read_exact(stream, 10, timeout=.01)
        finally:
            os.close(writer)

    def test_capture_combines_ten_ms_into_thirty_ms(self):
        capture = CaptureProcess.__new__(CaptureProcess)
        capture.stdout = Mock(read=Mock(side_effect=[b'a' * 320, b'b' * 320, b'c' * 320]))
        self.assertEqual(b'a' * 320 + b'b' * 320 + b'c' * 320, capture.read_frame(960))
        capture.stdout.read.side_effect = [b'd' * 320, b'']
        self.assertEqual(b'', capture.read_frame(960))

    def test_stderr_is_drained_bounded_and_uses_existing_diagnostic_event(self):
        events = []
        process = subprocess.Popen([sys.executable, '-c',
            'import os; os.write(2, b"x"*10000+b"\\n"); '
            'os.write(2, b\'{"reason":"aec_started"}\\n\'); os.write(1,b"abc")'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        capture = CaptureProcess(process, diagnostic=lambda **event: events.append(event))
        try:
            self.assertEqual(b'abc', capture.read_frame(3))
            process.wait(timeout=3)
            self.assertLessEqual(len(capture.error_tail()), 800)
            VoiceInputDiagnosticRequest(event='capture', level='error',
                                        reason='microphone_stopped', detail=capture.error_tail())
            self.assertEqual('capture', events[-1]['event'])
        finally:
            capture.close()

    def test_exited_sidecar_group_is_still_reaped(self):
        capture = CaptureProcess.__new__(CaptureProcess)
        capture.process = Mock(pid=123, poll=Mock(return_value=2))
        capture.stdout = io.BytesIO()
        capture.own_group = True
        capture._reader = Mock()
        with patch('dogido_server.voice_capture.os.killpg') as kill:
            capture.close()
        self.assertEqual(123, kill.call_args.args[0])


class EchoSelectionTests(unittest.TestCase):
    def test_opt_in_default_and_off_does_not_load_aec(self):
        settings = Settings(_env_file=None)
        self.assertEqual('off', settings.voice_echo_cancellation)
        raw = Mock(return_value=SimpleNamespace())
        with patch('dogido_server.voice_capture.CaptureProcess') as wrapped:
            spawn_capture(settings, raw_factory=raw)
        raw.assert_called_once_with(settings.voice_input_device)
        wrapped.assert_called_once()

    def test_unprepared_aec_never_calls_raw_factory(self):
        settings = Settings(_env_file=None, voice_echo_cancellation='webrtc',
                            voice_echo_helper='/nonexistent/dogido-helper')
        raw = Mock()
        with self.assertRaises(RuntimeError):
            spawn_capture(settings, raw_factory=raw)
        raw.assert_not_called()

    def test_aec_passes_uid_not_ffmpeg_device_index(self):
        settings = Settings(_env_file=None, voice_echo_cancellation='webrtc',
                            voice_echo_input_uid='USB Mic UID', voice_input_device=':7')
        with patch('dogido_server.voice_capture.Path.is_file', return_value=True), \
             patch('dogido_server.voice_capture.os.access', return_value=True):
            command = echo_command(settings)
        self.assertEqual(['--input-uid', 'USB Mic UID'], command[-2:])
        self.assertNotIn(':7', command)

    def test_relative_overrides_resolve_against_checkout(self):
        settings = Settings(_env_file=None, voice_echo_python='echo-env/bin/python',
                            voice_echo_helper='echo-bin/capture')
        with patch('dogido_server.voice_capture.Path.is_file', return_value=True), \
             patch('dogido_server.voice_capture.os.access', return_value=True):
            command = echo_command(settings)
        self.assertTrue(command[0].endswith('/echo-env/bin/python'))
        self.assertTrue(os.path.isabs(command[0]))
        self.assertTrue(os.path.isabs(command[command.index('--helper') + 1]))

    def test_check_does_not_start_capture(self):
        checked = SimpleNamespace(stdout=json.dumps({'status': 'passed', 'audio_devices_opened': 0}))
        with patch('dogido_server.echo_input.sys.platform', 'darwin'), \
             patch('dogido_server.echo_input.Path.is_file', return_value=True), \
             patch('dogido_server.echo_input.os.access', return_value=True), \
             patch('dogido_server.echo_input.WebRTCEchoProcessor'), \
             patch('dogido_server.echo_input.subprocess.run', return_value=checked) as run, \
             patch('dogido_server.echo_input.subprocess.Popen') as capture, \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(0, echo_main(['--check']))
        capture.assert_not_called()
        self.assertEqual('--self-test', run.call_args.args[0][-1])
        self.assertFalse(json.loads(output.getvalue())['live_audio_verified'])

    def test_probe_never_outputs_pcm_and_always_stops_helper(self):
        processor = Mock(process=Mock(return_value=b'PCM must not appear'), levels=Mock(return_value={}))
        frames = [HEADER.pack(MAGIC, RATE, 3, 16)] + [bytes(INPUT_BYTES)] * 100
        with patch('dogido_server.echo_input.sys.platform', 'darwin'), \
             patch('dogido_server.echo_input.Path.is_file', return_value=True), \
             patch('dogido_server.echo_input.os.access', return_value=True), \
             patch('dogido_server.echo_input.WebRTCEchoProcessor', return_value=processor), \
             patch('dogido_server.echo_input.subprocess.Popen') as capture, \
             patch('dogido_server.echo_input.read_exact', side_effect=frames), \
             patch('dogido_server.echo_input.stop_child') as stop, \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(0, echo_main(['--probe-seconds', '1']))
        stop.assert_called_once_with(capture.return_value)
        result = json.loads(output.getvalue())
        self.assertEqual('capture_completed', result['status'])
        self.assertFalse(result['audio_saved'])
        self.assertFalse(result['stt_called'])


if __name__ == '__main__':
    unittest.main()
