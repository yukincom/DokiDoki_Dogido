"""実機マイクの音量判定と発話区切りを、音声を保存せず診断する。

12秒程度の短い測定を想定する。PCMはプロセス内だけに保持し、Whisperが
一時作成するWAVも ``voice_input.transcribe`` が必ず削除する。
"""

from __future__ import annotations

import argparse
import math
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

# ``python dev_tools/voice/diagnose_voice_capture.py`` でも、editable install の別checkout
# ではなく、このスクリプトが属する作業ツリーを必ず診断する。
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import dogido_server.voice_input as voice_input_module
from dogido_server.config import get_settings
from dogido_server.voice_input import (
    FRAME_BYTES,
    FRAME_MS,
    PRE_ROLL_FRAMES,
    SAMPLE_RATE,
    frame_rms,
    resolve_whisper_paths,
    spawn_ffmpeg,
    stt_prompt,
    transcribe,
    write_wav,
)


@dataclass(slots=True)
class Segment:
    frames: list[bytes]
    rms_values: list[int]
    voiced_frames: int
    end_reason: str


def percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def pad_pcm_to_seconds(pcm: bytes, seconds: float) -> bytes:
    """音声を中央に保ったまま、前後へ無音を足す。"""

    target_samples = max(0, round(seconds * SAMPLE_RATE))
    current_samples = len(pcm) // 2
    if current_samples >= target_samples:
        return pcm
    missing_samples = target_samples - current_samples
    leading_samples = missing_samples // 2
    trailing_samples = missing_samples - leading_samples
    silence_sample = b"\x00\x00"
    return (
        silence_sample * leading_samples
        + pcm
        + silence_sample * trailing_samples
    )


def capture(seconds: float, threshold: int) -> tuple[list[int], list[Segment]]:
    settings = get_settings()
    silence_frames_to_end = max(1, settings.voice_silence_ms // FRAME_MS)
    max_speech_frames = int(settings.voice_max_speech_sec * 1000 // FRAME_MS)
    process = spawn_ffmpeg(settings.voice_input_device)
    assert process.stdout is not None

    from collections import deque

    pre_roll: deque[tuple[bytes, int]] = deque(maxlen=PRE_ROLL_FRAMES)
    speech: list[bytes] = []
    speech_rms: list[int] = []
    all_rms: list[int] = []
    segments: list[Segment] = []
    voiced_frames = 0
    silent_frames = 0
    recording = False
    started = time.monotonic()
    next_report = started + 1.0

    try:
        while time.monotonic() - started < seconds:
            frame = process.stdout.read(FRAME_BYTES)
            if not frame or len(frame) < FRAME_BYTES:
                raise RuntimeError("マイク入力が測定中に停止しました")
            rms = frame_rms(frame)
            all_rms.append(rms)
            loud = rms >= threshold

            if not recording:
                pre_roll.append((frame, rms))
                if loud:
                    recording = True
                    speech = [item[0] for item in pre_roll]
                    speech_rms = [item[1] for item in pre_roll]
                    voiced_frames = 1
                    silent_frames = 0
            else:
                speech.append(frame)
                speech_rms.append(rms)
                if loud:
                    voiced_frames += 1
                    silent_frames = 0
                else:
                    silent_frames += 1

                end_reason = None
                if silent_frames >= silence_frames_to_end:
                    end_reason = "silence"
                elif len(speech) >= max_speech_frames:
                    end_reason = "max_duration"
                if end_reason is not None:
                    segments.append(
                        Segment(
                            frames=speech,
                            rms_values=speech_rms,
                            voiced_frames=voiced_frames,
                            end_reason=end_reason,
                        )
                    )
                    recording = False
                    pre_roll.clear()
                    speech = []
                    speech_rms = []
                    voiced_frames = 0
                    silent_frames = 0

            now = time.monotonic()
            if now >= next_report:
                recent_count = max(1, round(1000 / FRAME_MS))
                recent = all_rms[-recent_count:]
                above = sum(value >= threshold for value in recent)
                print(
                    "[VOICE-DIAG] "
                    f"rms p50={percentile(recent, 0.50)} "
                    f"p90={percentile(recent, 0.90)} "
                    f"max={max(recent, default=0)} "
                    f"threshold以上={above}/{len(recent)} "
                    f"recording={recording}",
                    flush=True,
                )
                next_report = now + 1.0
    finally:
        process.terminate()
        try:
            process.wait(timeout=1.0)
        except Exception:  # noqa: BLE001 - 診断終了時のbest effort
            process.kill()

    if recording and speech:
        segments.append(
            Segment(
                frames=speech,
                rms_values=speech_rms,
                voiced_frames=voiced_frames,
                end_reason="capture_end",
            )
        )
    return all_rms, segments


def inspect_silero_vad(cli: Path, model: Path, pcm: bytes) -> None:
    vad_cli = cli.with_name("whisper-vad-speech-segments")
    if not vad_cli.exists():
        print(f"[VOICE-DIAG] silero_probe=SKIP executable_missing={vad_cli}")
        return
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
        path = Path(handle.name)
    try:
        write_wav(str(path), pcm)
        result = subprocess.run(
            [
                str(vad_cli),
                "--vad-model", str(model),
                "--file", str(path),
                "--vad-min-speech-duration-ms", "250",
                "--vad-min-silence-duration-ms", "500",
                "--vad-speech-pad-ms", "200",
                "--no-prints",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        print(
            "[VOICE-DIAG] silero_probe "
            f"exit={result.returncode} stdout={result.stdout.strip()!r} "
            f"stderr={result.stderr.strip()[-600:]!r}"
        )
    finally:
        path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=12.0)
    parser.add_argument("--threshold", type=int)
    parser.add_argument("--transcribe", action="store_true")
    parser.add_argument("--vad-model", type=Path)
    parser.add_argument("--compare-without-vad", action="store_true")
    parser.add_argument("--compare-cpu", action="store_true")
    parser.add_argument("--compare-without-prompt", action="store_true")
    parser.add_argument("--pad-to-seconds", type=float)
    parser.add_argument("--no-speech-thold", type=float)
    parser.add_argument("--retry-no-speech-thold", type=float)
    args = parser.parse_args()

    settings = get_settings()
    threshold = args.threshold or settings.voice_rms_threshold
    no_speech_thold = (
        settings.voice_no_speech_thold
        if args.no_speech_thold is None
        else args.no_speech_thold
    )
    print(
        f"[VOICE-DIAG] {args.seconds:g}秒測定します。"
        "最初の2秒は黙り、その後『ドギド、聞こえますか』と普通に話してください。"
    )
    print(f"[VOICE-DIAG] implementation={voice_input_module.__file__}")
    print(
        f"[VOICE-DIAG] device={settings.voice_input_device} "
        f"threshold={threshold} silence_ms={settings.voice_silence_ms} "
        f"silero_vad={args.vad_model or 'off'} "
        f"no_speech_thold={no_speech_thold}"
    )
    all_rms, segments = capture(args.seconds, threshold)
    print(
        "[VOICE-DIAG] 全体 "
        f"p50={percentile(all_rms, 0.50)} "
        f"p90={percentile(all_rms, 0.90)} "
        f"p99={percentile(all_rms, 0.99)} "
        f"max={max(all_rms, default=0)} "
        f"mean={round(statistics.fmean(all_rms), 1) if all_rms else 0}"
    )

    minimum_frames = max(1, settings.voice_min_speech_ms // FRAME_MS)
    cli = model = None
    if args.transcribe:
        cli, model = resolve_whisper_paths(settings)
    for index, segment in enumerate(segments, start=1):
        duration_ms = len(segment.frames) * FRAME_MS
        voiced_ms = segment.voiced_frames * FRAME_MS
        print(
            f"[VOICE-DIAG] segment={index} end={segment.end_reason} "
            f"duration_ms={duration_ms} voiced_ms={voiced_ms} "
            f"rms_p50={percentile(segment.rms_values, 0.50)} "
            f"rms_p90={percentile(segment.rms_values, 0.90)} "
            f"rms_max={max(segment.rms_values, default=0)}"
        )
        if args.transcribe and segment.voiced_frames >= minimum_frames:
            assert cli is not None and model is not None
            pcm = b"".join(segment.frames)
            if args.vad_model is not None:
                inspect_silero_vad(cli, args.vad_model, pcm)

            def show_diagnostic(**fields: object) -> None:
                print(
                    "[VOICE-DIAG] whisper_event "
                    f"event={fields.get('event')} reason={fields.get('reason')} "
                    f"detail={fields.get('detail')!r}"
                )

            text = transcribe(
                cli,
                model,
                pcm,
                no_speech_thold=no_speech_thold,
                prompt=stt_prompt("normal"),
                vad_model=args.vad_model,
                diagnostic=show_diagnostic,
            )
            print(f"[VOICE-DIAG] transcript={text!r}")
            if args.vad_model is not None and args.compare_without_vad:
                plain_text = transcribe(
                    cli,
                    model,
                    pcm,
                    no_speech_thold=no_speech_thold,
                    prompt=stt_prompt("normal"),
                    diagnostic=show_diagnostic,
                )
                print(f"[VOICE-DIAG] without_silero transcript={plain_text!r}")
            if args.compare_without_prompt:
                no_prompt_text = transcribe(
                    cli,
                    model,
                    pcm,
                    no_speech_thold=no_speech_thold,
                    prompt="",
                    diagnostic=show_diagnostic,
                )
                print(f"[VOICE-DIAG] without_prompt transcript={no_prompt_text!r}")
            if args.pad_to_seconds is not None:
                padded_pcm = pad_pcm_to_seconds(pcm, args.pad_to_seconds)
                padded_text = transcribe(
                    cli,
                    model,
                    padded_pcm,
                    no_speech_thold=no_speech_thold,
                    prompt=stt_prompt("normal"),
                    diagnostic=show_diagnostic,
                )
                print(
                    f"[VOICE-DIAG] padded_to={args.pad_to_seconds:g}s "
                    f"transcript={padded_text!r}"
                )
                if args.compare_without_prompt:
                    padded_no_prompt_text = transcribe(
                        cli,
                        model,
                        padded_pcm,
                        no_speech_thold=no_speech_thold,
                        prompt="",
                        diagnostic=show_diagnostic,
                    )
                    print(
                        f"[VOICE-DIAG] padded_to={args.pad_to_seconds:g}s "
                        f"without_prompt transcript={padded_no_prompt_text!r}"
                    )
            if args.compare_cpu:
                cpu_text = transcribe(
                    cli,
                    model,
                    pcm,
                    no_speech_thold=no_speech_thold,
                    prompt=stt_prompt("normal"),
                    use_gpu=False,
                    diagnostic=show_diagnostic,
                )
                print(f"[VOICE-DIAG] cpu transcript={cpu_text!r}")
            if text is None and args.retry_no_speech_thold is not None:
                retry_text = transcribe(
                    cli,
                    model,
                    pcm,
                    no_speech_thold=args.retry_no_speech_thold,
                    prompt=stt_prompt("normal"),
                    vad_model=args.vad_model,
                    diagnostic=show_diagnostic,
                )
                print(
                    "[VOICE-DIAG] "
                    f"retry_no_speech_thold={args.retry_no_speech_thold} "
                    f"transcript={retry_text!r}"
                )
        elif args.transcribe:
            print(
                f"[VOICE-DIAG] transcript=SKIP "
                f"reason=below_min_speech required_frames={minimum_frames}"
            )

    if not segments:
        print("[VOICE-DIAG] segment=NONE（しきい値を超えたフレームがありません）")


if __name__ == "__main__":
    main()
