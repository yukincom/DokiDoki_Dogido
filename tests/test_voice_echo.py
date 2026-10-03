from __future__ import annotations

import io
import json
import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

try:
    import numpy as np
except ImportError:
    np = None

from dogido_server.config import Settings
from dogido_server.echo_input import (
    FRAME_SAMPLES, HEADER, INPUT_BYTES, MAGIC, RATE, WebRTCEchoProcessor,
    read_exact, validate_header,
    main as echo_main,
)
from dogido_server.voice_capture import capture_command, echo_command


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


class EchoSelectionTests(unittest.TestCase):
    def test_off_selects_existing_ffmpeg_without_loading_aec(self):
        settings = Settings(_env_file=None, voice_echo_cancellation='off', voice_input_device=':7')
        with patch('dogido_server.voice_capture.shutil.which', return_value='/fixture/ffmpeg'), \
             patch('dogido_server.voice_capture.echo_command') as aec:
            command = capture_command(settings)
        aec.assert_not_called()
        self.assertEqual('/fixture/ffmpeg', command[0])
        self.assertEqual(':7', command[command.index('-i') + 1])
        self.assertEqual(['-ac', '1', '-ar', '16000', '-f', 's16le', '-'], command[-7:])

    def test_unprepared_aec_never_selects_raw_capture(self):
        settings = Settings(_env_file=None, voice_echo_cancellation='webrtc',
                            voice_echo_helper='/nonexistent/dogido-helper')
        with patch('dogido_server.voice_capture.shutil.which') as raw:
            with self.assertRaises(RuntimeError):
                capture_command(settings)
        raw.assert_not_called()

    def test_missing_ffmpeg_is_reported_without_installing(self):
        settings = Settings(_env_file=None, voice_echo_cancellation='off')
        with patch('dogido_server.voice_capture.shutil.which', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'ffmpeg'):
                capture_command(settings)


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
