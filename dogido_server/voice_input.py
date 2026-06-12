"""マイク音声入力プロセス。

マイク → ffmpeg(16kHz mono) → 無音区切り VAD → whisper.cpp →
POST /api/v1/player-input で dogido_server に届ける。
チャット入力と同じ user_text 経路に合流するので、キーワード質問も雑談返事も同じ扱いになる。

whisper.cpp の呼び出しとノイズ除去は yuno-chan-api の speech_service.py を参考にしている。

使い方:
    dogido-llm/bin/python -m dogido_server.voice_input

設定（.env / 環境変数 DOGIDO_*）:
    DOGIDO_VOICE_WHISPER_CLI / DOGIDO_VOICE_WHISPER_MODEL  … 未設定なら自動検出
    DOGIDO_VOICE_INPUT_DEVICE=":0"   … ffmpeg avfoundation のマイク指定
    DOGIDO_VOICE_RMS_THRESHOLD=700  … 反応しすぎる/しなさすぎる時に調整
    DOGIDO_VOICE_WAKE_WORD="ドギド" … 設定すると呼びかけを含む発話だけ届ける

注意: スピーカー再生だとドギド自身の声を拾ってループするので、ヘッドホン推奨。
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import wave
from array import array
from collections import deque
from pathlib import Path

import httpx

from dogido_server.config import get_settings

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
FRAME_BYTES = FRAME_SAMPLES * 2  # s16le mono
PRE_ROLL_FRAMES = 10  # 発話開始前 300ms を含める

# 参考: yuno-chan-api の誤認識ノイズパターン
NOISE_PATTERNS = ("ごおおお", "ごーーー", "ざーーー", "うおおお")
SHORT_ALLOWLIST = ("ドギド", "おい", "おーい", "うん", "はい", "おう", "なあ")

WHISPER_CLI_CANDIDATES = (
    Path.home() / "AI_assistant" / "whisper.cpp" / "build" / "bin" / "whisper-cli",
    Path.home() / "whisper.cpp" / "build" / "bin" / "whisper-cli",
)
WHISPER_MODEL_DIRS = (
    Path.home() / "AI_assistant" / "whisper.cpp" / "models",
    Path.home() / "whisper.cpp" / "models",
)


def resolve_whisper_paths(settings) -> tuple[Path, Path]:
    cli = settings.voice_whisper_cli
    if cli is None:
        cli = next((candidate for candidate in WHISPER_CLI_CANDIDATES if candidate.exists()), None)
    if cli is None or not Path(cli).exists():
        raise SystemExit(
            "whisper-cli が見つかりません。DOGIDO_VOICE_WHISPER_CLI にパスを設定してください。"
        )
    model = settings.voice_whisper_model
    if model is None:
        for models_dir in WHISPER_MODEL_DIRS:
            # 日本語特化の kotoba-whisper を最優先で拾う
            candidates = sorted(models_dir.glob("ggml-kotoba*.bin")) + sorted(
                path for path in models_dir.glob("ggml-*.bin") if ".en" not in path.name
            )
            if candidates:
                model = candidates[0]
                break
    if model is None or not Path(model).exists():
        raise SystemExit(
            "whisper モデル（ggml-*.bin）が見つかりません。DOGIDO_VOICE_WHISPER_MODEL を設定してください。"
        )
    return Path(cli), Path(model)


def frame_rms(frame: bytes) -> int:
    samples = array("h")
    samples.frombytes(frame)
    if not samples:
        return 0
    total = 0
    for sample in samples:
        total += sample * sample
    return int((total / len(samples)) ** 0.5)


def write_wav(path: str, pcm: bytes) -> None:
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm)


def transcribe(cli: Path, model: Path, pcm: bytes, *, no_speech_thold: float) -> str | None:
    """whisper.cpp で書き起こす（yuno-chan-api の speech_service.py と同じ流儀）。"""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
        tmp_path = handle.name
    try:
        write_wav(tmp_path, pcm)
        result = subprocess.run(
            [
                str(cli),
                "-m", str(model),
                "-f", tmp_path,
                "-l", "ja",
                "--prompt", "Minecraftを遊びながらの日本語の話しかけです。",
                "--no-speech-thold", str(no_speech_thold),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        transcript = ""
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("[") and "-->" in line:
                parts = line.split("]", 1)
                if len(parts) > 1:
                    text = re.sub(r"\[_[A-Z_0-9]+_\]", "", parts[1]).strip()
                    if text:
                        transcript += text + " "
        transcript = transcript.strip()
        if not transcript:
            return None
        if any(noise in transcript for noise in NOISE_PATTERNS):
            print(f"[VOICE] ノイズ判定でスキップ: {transcript}")
            return None
        compact = transcript.replace(" ", "")
        if len(compact) <= 3 and compact not in SHORT_ALLOWLIST:
            print(f"[VOICE] 短すぎるのでスキップ: {transcript}")
            return None
        return transcript
    except subprocess.TimeoutExpired:
        print("[VOICE] whisper がタイムアウト（60秒）")
        return None
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def deliver(base_url: str, auth_token: str | None, text: str) -> None:
    headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
    try:
        response = httpx.post(
            f"{base_url}/api/v1/player-input",
            json={"text": text},
            headers=headers,
            timeout=3.0,
        )
        payload = response.json()
        if payload.get("accepted"):
            print(f"[VOICE] → 届けた (session={payload.get('session_id')})")
        else:
            print(f"[VOICE] → サーバが受け取らず: {payload.get('reason')}（マイクラ接続待ち？）")
    except httpx.HTTPError as exc:
        print(f"[VOICE] → サーバに届かない: {exc}（dogido_server は起動してる？）")


def spawn_ffmpeg(device: str) -> subprocess.Popen:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-f", "avfoundation",
        "-i", device,
        "-ac", "1",
        "-ar", str(SAMPLE_RATE),
        "-f", "s16le",
        "-",
    ]
    try:
        return subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        raise SystemExit("ffmpeg が見つかりません（brew install ffmpeg）。")


def main() -> None:
    settings = get_settings()
    cli, model = resolve_whisper_paths(settings)
    base_url = f"http://{settings.bind_host}:{settings.bind_port}"
    wake_word = settings.voice_wake_word.strip()

    print(f"[VOICE] whisper: {cli.name} / model: {model.name}")
    print(f"[VOICE] mic: avfoundation \"{settings.voice_input_device}\" / server: {base_url}")
    print(f"[VOICE] 音量しきい値: {settings.voice_rms_threshold}（DOGIDO_VOICE_RMS_THRESHOLD で調整）")
    if wake_word:
        print(f"[VOICE] ウェイクワード: 「{wake_word}」を含む発話だけ届けます")
    print("[VOICE] ※ドギドの声をマイクが拾うとループするので、ヘッドホン推奨やで")

    process = spawn_ffmpeg(settings.voice_input_device)
    assert process.stdout is not None

    silence_frames_to_end = max(1, settings.voice_silence_ms // FRAME_MS)
    min_speech_frames = max(1, settings.voice_min_speech_ms // FRAME_MS)
    max_speech_frames = int(settings.voice_max_speech_sec * 1000 // FRAME_MS)

    pre_roll: deque[bytes] = deque(maxlen=PRE_ROLL_FRAMES)
    speech: list[bytes] = []
    voiced_frames = 0
    silent_frames = 0
    recording = False

    print("[VOICE] 待機中…話しかけてや")
    try:
        while True:
            frame = process.stdout.read(FRAME_BYTES)
            if not frame or len(frame) < FRAME_BYTES:
                stderr_tail = (process.stderr.read() or b"").decode("utf-8", "replace")[-400:] if process.stderr else ""
                print("[VOICE] マイク入力が止まりました。", stderr_tail)
                print("[VOICE] マイク権限（システム設定→プライバシー→マイク→ターミナル）とデバイス番号を確認してください。")
                print('[VOICE] デバイス一覧: ffmpeg -f avfoundation -list_devices true -i ""')
                break
            loud = frame_rms(frame) >= settings.voice_rms_threshold
            if not recording:
                pre_roll.append(frame)
                if loud:
                    recording = True
                    speech = list(pre_roll)
                    voiced_frames = 1
                    silent_frames = 0
                continue

            speech.append(frame)
            if loud:
                voiced_frames += 1
                silent_frames = 0
            else:
                silent_frames += 1

            if silent_frames >= silence_frames_to_end or len(speech) >= max_speech_frames:
                recording = False
                pre_roll.clear()
                if voiced_frames >= min_speech_frames:
                    transcript = transcribe(
                        cli, model, b"".join(speech), no_speech_thold=settings.voice_no_speech_thold
                    )
                    if transcript:
                        print(f"[VOICE] 認識: {transcript}")
                        if wake_word and wake_word not in transcript:
                            print("[VOICE] ウェイクワード無しのためスキップ")
                        else:
                            deliver(base_url, settings.auth_token, transcript)
                speech = []
                voiced_frames = 0
                silent_frames = 0
    except KeyboardInterrupt:
        print("\n[VOICE] 終了します")
    finally:
        process.terminate()


if __name__ == "__main__":
    main()
