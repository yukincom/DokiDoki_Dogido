"""Generate fictional Japanese TTS to temporary files and test AEC without playback/recording.

Requires macOS say and ffmpeg. No personal recordings or existing voice files are read.
Temporary audio is deleted on exit; only aggregate metrics go to stdout.
"""
from pathlib import Path
import json
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from dogido_server.echo_input import FRAME_SAMPLES, RATE, WebRTCEchoProcessor
from check_echo_offline import db, delayed, rms


def synthesized(folder, name, voice, text):
    path = folder / (name + '.aiff')
    subprocess.run(['say', '-v', voice, '-r', '175', '-o', str(path), text], check=True, timeout=45)
    pcm = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-i', str(path),
                          '-f', 's16le', '-ar', str(RATE), '-ac', '1', 'pipe:1'],
                         check=True, capture_output=True, timeout=15).stdout
    signal = np.frombuffer(pcm, '<i2').astype(float)
    if not len(signal) or not np.all(np.isfinite(signal)) or rms(signal) < 1:
        raise ValueError('synthetic speech was empty')
    return signal / rms(signal) * 1600


def run():
    with tempfile.TemporaryDirectory(prefix='dogido-aec-synthetic-') as folder:
        root = Path(folder)
        far = synthesized(root, 'far', 'Kyoko',
            'これはドギドの音声処理を確認する架空の文章です。山の向こうには小さな村があります。'
            '川のそばを歩いて、お花を眺めています。夜になったら明るい家に帰りましょう。')
        near = synthesized(root, 'near', 'Reed (日本語（日本）)',
            '石炭。いいね。うん。ちょっと待って。私はこっちの道を進みたいです。'
            '今の言葉はどういう意味ですか。一緒に考えてみよう。そろそろ家へ帰ろう。')
    count = RATE * 30
    far = np.resize(np.concatenate([far, np.zeros(RATE)]), count)
    near = np.resize(np.concatenate([near, np.zeros(RATE)]), count)
    near[:RATE * 6] = 0
    t = np.arange(count) / RATE
    game = np.sin(2 * np.pi * (420 * t + np.sin(t) * 20)) * 350 * (np.sin(t * 1.7) > .5)
    echo = .5 * delayed(far, .045) + .15 * delayed(far, .080) + .2 * delayed(game, .065)
    zero = np.zeros(count)
    results = []
    for name, mic, left, right, target in [
        ('tts_echo_only', echo, far, game, zero),
        ('tts_near_only', near, zero, zero, near),
        ('tts_double_talk', echo + near, far, game, near),
    ]:
        samples = np.clip(np.column_stack([mic, left, right]), -32768, 32767).astype('<i2')
        processor = WebRTCEchoProcessor()
        output = np.concatenate([np.frombuffer(processor.process(samples[i:i + FRAME_SAMPLES].tobytes()), '<i2')
                                 for i in range(0, count, FRAME_SAMPLES)])
        start, end = RATE * 10, RATE * 29
        result = {'case': name, 'output_vs_mic_db': db(rms(output[start:end]) / rms(mic[start:end]))}
        if np.any(target):
            expected = target[start:end]
            gains = [float(np.dot(output[start + lag:end + lag].astype(float), expected) /
                           np.dot(expected, expected)) for lag in range(-160, 161)]
            lag = int(np.argmax(gains) - 160)
            result['near_projection_gain_db'] = db(max(gains))
            result['scoring_lag_samples'] = lag
            clean = output[start + lag:end + lag].astype(float)
            result['residual_vs_near_db'] = db(rms(clean - expected * max(gains)) / rms(expected))
        results.append(result)
    passed = (results[0]['output_vs_mic_db'] < -10 and results[1]['near_projection_gain_db'] > -6
              and results[2]['near_projection_gain_db'] > -12)
    print(json.dumps({'status': 'passed' if passed else 'failed', 'source': 'fictional_local_tts',
                      'audio_devices_opened': 0, 'real_room_or_human_speech_verified': False,
                      'cases': results}, indent=2))
    return 0 if passed else 2


if __name__ == '__main__':
    raise SystemExit(run())
