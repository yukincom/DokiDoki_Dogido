"""Generated-signal AEC smoke test. No microphone, speaker, audio file, STT, or network.

Run with the isolated echo Python. These metrics do not prove speech intelligibility
or cancellation in a real room. In particular double-talk needs a human voice test.
"""
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from dogido_server.echo_input import FRAME_SAMPLES, RATE, WebRTCEchoProcessor


def delayed(signal, seconds):
    count = int(seconds * RATE)
    return np.concatenate([np.zeros(count), signal[:-count]])


def rms(signal):
    return float(np.sqrt(np.mean(np.square(signal.astype(float)))))


def db(ratio):
    return round(float(20 * np.log10(max(ratio, 1e-8))), 2)


def run():
    rng = np.random.default_rng(260908)
    t = np.arange(RATE * 16) / RATE
    # Independent nonstationary broad-band sources, plus different voiced harmonics.
    left = np.convolve(rng.normal(size=len(t)), np.ones(5) / 5, mode='same') * 3000
    left += 1200 * np.sin(2 * np.pi * (170 * t + 8 * np.sin(t)))
    right = np.convolve(rng.normal(size=len(t)), np.ones(7) / 7, mode='same') * 3000
    right += 1400 * np.sin(2 * np.pi * (310 * t + 12 * np.sin(.8 * t)))
    near = np.convolve(rng.normal(size=len(t)), np.ones(4) / 4, mode='same') * 1800
    near += 1000 * np.sin(2 * np.pi * (230 * t + 6 * np.sin(1.1 * t)))
    near *= .5 + .5 * np.sin(2 * np.pi * 2.1 * t) ** 2
    echo = .5 * delayed(left, .045) + .25 * delayed(right, .065)
    echo += .12 * delayed(left, .080)
    zero = np.zeros(len(t))
    cases = [
        ('echo_only', echo, left, right, zero),
        ('near_only', near, zero, zero, near),
        ('double_talk', echo + near * (t > 6), left, right, near * (t > 6)),
        # Averaging this render before AEC would create a false all-zero reference.
        ('opposite_stereo', .5 * delayed(left, .045), left, -left, zero),
    ]
    results = []
    for name, mic, l, r, target in cases:
        samples = np.clip(np.column_stack([mic, l, r]), -32768, 32767).astype('<i2')
        processor = WebRTCEchoProcessor()
        output = np.concatenate([np.frombuffer(processor.process(samples[i:i + FRAME_SAMPLES].tobytes()), '<i2')
                                 for i in range(0, len(samples), FRAME_SAMPLES)])
        tail = slice(RATE * 10, RATE * 15)
        result = {'case': name, 'output_vs_mic_db': db(rms(output[tail]) / rms(samples[tail, 0]))}
        if np.any(target):
            # Best lag is scoring only, never an adjustment to the live PCM stream.
            gains = []
            expected = target[tail]
            for lag in range(-160, 161):
                observed = output[RATE * 10 + lag:RATE * 15 + lag].astype(float)
                gains.append(float(np.dot(observed, expected) / np.dot(expected, expected)))
            result['near_projection_gain_db'] = db(max(gains))
            result['scoring_lag_samples'] = int(np.argmax(gains) - 160)
        results.append(result)
    # Gross regressions only; these bounds are not a product quality threshold.
    passed = (results[0]['output_vs_mic_db'] < -10 and results[3]['output_vs_mic_db'] < -10
              and results[1]['near_projection_gain_db'] > -6 and results[2]['near_projection_gain_db'] > -18)
    print(json.dumps({'status': 'passed' if passed else 'failed', 'source': 'generated_signals',
                      'audio_devices_opened': 0, 'real_speech_verified': False, 'cases': results}, indent=2))
    return 0 if passed else 2


if __name__ == '__main__':
    raise SystemExit(run())
