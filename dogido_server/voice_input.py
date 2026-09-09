"""マイク音声入力プロセス。

マイク → ffmpeg または Core Audio + 任意AEC (16kHz mono) → 無音区切り VAD → whisper.cpp →
POST /api/v1/player-input で dogido_server に届ける。
チャット入力と同じ user_text 経路に合流するので、キーワード質問も雑談返事も同じ扱いになる。

whisper.cpp の呼び出しとノイズ除去は yuno-chan-api の speech_service.py を参考にしている。

使い方:
    .venv/bin/python -m dogido_server.voice_input

設定（.env / 環境変数 DOGIDO_*）:
    DOGIDO_VOICE_WHISPER_CLI / DOGIDO_VOICE_WHISPER_MODEL  … 未設定なら自動検出
    DOGIDO_VOICE_INPUT_DEVICE=":0"   … ffmpeg avfoundation のマイク指定
    DOGIDO_VOICE_RMS_THRESHOLD=700  … 反応しすぎる/しなさすぎる時に調整
    DOGIDO_VOICE_WAKE_WORD="ドギド" … 設定すると呼びかけを含む発話だけ届ける

任意AEC: DOGIDO_VOICE_ECHO_CANCELLATION=webrtc。導入・権限は docs/voice-echo-cancellation.md。
AECを使わないスピーカー再生では自己音声を拾うため、ヘッドホン推奨。
"""
from __future__ import annotations

import queue
import re
import subprocess
import tempfile
import threading
import time
import wave
from array import array
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

import httpx

from dogido_server.config import get_settings
from dogido_server.player_input.normalize import is_known_voice_noise_text
from dogido_server.voice_capture import spawn_capture

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
FRAME_BYTES = FRAME_SAMPLES * 2  # s16le mono
PRE_ROLL_FRAMES = 10  # 発話開始前 300ms を含める

# 参考: yuno-chan-api の誤認識ノイズパターン
NOISE_PATTERNS = ("ごおおお", "ごーーー", "ざーーー", "うおおお")

VoicePromptMode = Literal["normal", "haiku_workshop"]
VoiceDiagnosticCallback = Callable[..., None]


@dataclass(frozen=True, slots=True)
class CapturedSpeech:
    """録音ループからSTT workerへ渡す、上限付きの一発話。"""

    pcm: bytes
    duration_ms: int
    voiced_ms: int
    captured_at: float


class SpeechRecognitionWorker:
    """マイク読取をWhisperから分離し、古い発話をためない。"""

    _STOP = object()

    def __init__(
        self,
        processor: Callable[[CapturedSpeech], None],
        *,
        diagnostic: VoiceDiagnosticCallback | None = None,
        max_pending: int = 1,
        max_age_sec: float = 8.0,
    ) -> None:
        self._processor = processor
        self._diagnostic = diagnostic
        self._max_age_sec = max_age_sec
        self._pending: queue.Queue[CapturedSpeech | object] = queue.Queue(
            maxsize=max(1, max_pending)
        )
        self._closed = False
        self._thread = threading.Thread(
            target=self._run,
            name="dogido-stt",
            daemon=True,
        )
        self._thread.start()

    def submit(self, segment: CapturedSpeech) -> bool:
        if self._closed:
            return False
        try:
            self._pending.put_nowait(segment)
            return True
        except queue.Full:
            pass

        # 会話では古い未処理音声より新しい発話を優先する。処理中の1件は
        # 触らず、待ち列の1件だけを明示的に置き換える。
        try:
            replaced = self._pending.get_nowait()
        except queue.Empty:
            return False
        self._pending.task_done()
        if replaced is self._STOP:
            return False
        try:
            self._pending.put_nowait(segment)
        except queue.Full:
            return False
        if self._diagnostic is not None:
            self._diagnostic(
                event="stt_queue",
                level="warning",
                reason="pending_segment_replaced",
                duration_ms=segment.duration_ms,
            )
        return True

    def _run(self) -> None:
        while True:
            item = self._pending.get()
            try:
                if item is self._STOP:
                    return
                assert isinstance(item, CapturedSpeech)
                age_sec = time.monotonic() - item.captured_at
                if age_sec > self._max_age_sec:
                    if self._diagnostic is not None:
                        self._diagnostic(
                            event="stt_queue",
                            level="warning",
                            reason="stale_segment_dropped",
                            duration_ms=item.duration_ms,
                            detail=f"age_ms={round(age_sec * 1000)}",
                        )
                    continue
                try:
                    self._processor(item)
                except Exception as exc:  # noqa: BLE001 - 次発話のworkerを守る
                    if self._diagnostic is not None:
                        self._diagnostic(
                            event="stt_error",
                            level="error",
                            reason="worker_processing_error",
                            detail=f"{type(exc).__name__}: {exc}",
                        )
            finally:
                self._pending.task_done()

    def close(self, timeout: float = 2.0) -> None:
        self._closed = True
        while True:
            try:
                self._pending.get_nowait()
            except queue.Empty:
                break
            else:
                self._pending.task_done()
        self._pending.put_nowait(self._STOP)
        self._thread.join(timeout)


class VoiceDiagnosticDispatcher:
    """診断HTTPを録音ループから分離し、未送信件数を有界に保つ。"""

    _STOP = object()

    def __init__(
        self,
        sender: VoiceDiagnosticCallback,
        *,
        max_pending: int = 32,
    ) -> None:
        self._sender = sender
        self._pending: queue.Queue[dict[str, object] | object] = queue.Queue(
            maxsize=max(1, max_pending)
        )
        self._closed = False
        self._thread = threading.Thread(
            target=self._run,
            name="dogido-voice-diagnostics",
            daemon=True,
        )
        self._thread.start()

    def submit(self, **fields: object) -> bool:
        if self._closed:
            return False
        try:
            self._pending.put_nowait(dict(fields))
        except queue.Full:
            return False
        return True

    def __call__(self, **fields: object) -> None:
        if not self.submit(**fields):
            print("[VOICE] 診断ログ待ち列が満杯のため、この診断だけ破棄します")

    def _run(self) -> None:
        while True:
            fields = self._pending.get()
            try:
                if fields is self._STOP:
                    return
                assert isinstance(fields, dict)
                self._sender(**fields)
            finally:
                self._pending.task_done()

    def close(self, timeout: float = 0.5) -> None:
        self._closed = True
        while True:
            try:
                self._pending.get_nowait()
            except queue.Empty:
                break
            else:
                self._pending.task_done()
        self._pending.put_nowait(self._STOP)
        self._thread.join(timeout)

NORMAL_STT_PROMPT = (
    "Minecraftのプレイ内容について日本語で会話しています。"
    "話題はブロック、アイテム、モブ、バイオーム、建築、戦闘です。"
    "国語や詩について質問することもあり、ワ行イ段、ゐ、枕詞、川柳、"
    "ソネットという語を使います。"
)
HAIKU_WORKSHOP_STT_PROMPT = (
    "Minecraftのプレイ内容について日本語で会話しています。"
    "話題はブロック、アイテム、モブ、バイオーム、建築、戦闘と、"
    "日本語の読み方、言い換え、川柳の上五・中七・下五の推敲です。"
)

WHISPER_CLI_CANDIDATES = (
    Path.home() / "AI_assistant" / "whisper.cpp" / "build" / "bin" / "whisper-cli",
    Path.home() / "whisper.cpp" / "build" / "bin" / "whisper-cli",
)
WHISPER_MODEL_DIRS = (
    Path.home() / "AI_assistant" / "whisper.cpp" / "models",
    Path.home() / "whisper.cpp" / "models",
)
VAD_MODEL_CANDIDATES = (
    Path.home() / ".cache" / "dogido" / "models" / "ggml-silero-v6.2.0.bin",
    *(path / "ggml-silero-v6.2.0.bin" for path in WHISPER_MODEL_DIRS),
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


def resolve_vad_paths(settings, whisper_cli: Path) -> tuple[Path, Path] | None:
    """共有キャッシュか明示設定にSilero VADがあれば、その実行組を返す。"""

    if not settings.voice_vad_enabled:
        return None
    cli = settings.voice_vad_cli or whisper_cli.with_name(
        "whisper-vad-speech-segments"
    )
    model = settings.voice_vad_model
    if model is None:
        model = next((path for path in VAD_MODEL_CANDIDATES if path.exists()), None)
    if model is None or not Path(cli).exists() or not Path(model).exists():
        return None
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


def contains_speech(
    vad_cli: Path,
    vad_model: Path,
    pcm: bytes,
    *,
    threshold: float,
    diagnostic: VoiceDiagnosticCallback | None = None,
) -> bool:
    """Sileroで発話の有無だけ判定する。失敗時は実音声を失わないよう通す。"""

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
        tmp_path = handle.name
    try:
        write_wav(tmp_path, pcm)
        result = subprocess.run(
            [
                str(vad_cli),
                "--vad-model", str(vad_model),
                "--file", tmp_path,
                "--vad-threshold", str(threshold),
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
        match = re.search(r"Detected\s+(\d+)\s+speech segments", result.stdout)
        if result.returncode != 0 or match is None:
            if diagnostic is not None:
                detail = (result.stderr or result.stdout or "").strip()[-600:]
                diagnostic(
                    event="vad_error",
                    level="warning",
                    reason="silero_probe_failed_open",
                    detail=f"exit={result.returncode} {detail}".strip(),
                )
            return True
        count = int(match.group(1))
        if count == 0 and diagnostic is not None:
            diagnostic(
                event="vad_rejected",
                level="info",
                reason="no_speech_segment",
            )
        return count > 0
    except (OSError, subprocess.TimeoutExpired) as exc:
        if diagnostic is not None:
            diagnostic(
                event="vad_error",
                level="warning",
                reason="silero_probe_failed_open",
                detail=f"{type(exc).__name__}: {exc}",
            )
        return True
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def stt_prompt(mode: VoicePromptMode) -> str:
    """通常会話と川柳推敲で、Whisperへ渡す短い文脈だけを切り替える。"""

    if mode == "haiku_workshop":
        return HAIKU_WORKSHOP_STT_PROMPT
    return NORMAL_STT_PROMPT


def fetch_voice_prompt_mode(
    base_url: str,
    auth_token: str | None,
    *,
    diagnostic: VoiceDiagnosticCallback | None = None,
) -> VoicePromptMode:
    """直近セッションの文脈を取得する。失敗時も音声入力を止めず通常扱いにする。"""

    headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
    try:
        response = httpx.get(
            f"{base_url}/api/v1/voice-input/context",
            headers=headers,
            timeout=1.0,
        )
        response.raise_for_status()
        prompt_mode = response.json().get("prompt_mode")
        if prompt_mode == "haiku_workshop":
            return "haiku_workshop"
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        if diagnostic is not None:
            diagnostic(
                event="context",
                level="warning",
                reason="context_unavailable",
                detail=f"{type(exc).__name__}: {exc}",
            )
    return "normal"


def transcribe(
    cli: Path,
    model: Path,
    pcm: bytes,
    *,
    no_speech_thold: float,
    prompt: str = NORMAL_STT_PROMPT,
    vad_model: Path | None = None,
    use_gpu: bool = True,
    diagnostic: VoiceDiagnosticCallback | None = None,
) -> str | None:
    """whisper.cpp で書き起こす（yuno-chan-api の speech_service.py と同じ流儀）。"""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
        tmp_path = handle.name
    try:
        write_wav(tmp_path, pcm)
        command = [
            str(cli),
            "-m", str(model),
            "-f", tmp_path,
            "-l", "ja",
            "--prompt", prompt,
            "--no-speech-thold", str(no_speech_thold),
        ]
        if not use_gpu:
            command.append("--no-gpu")
        if vad_model is not None:
            command.extend(
                [
                    "--vad",
                    "--vad-model", str(vad_model),
                    "--vad-min-speech-duration-ms", "250",
                    "--vad-min-silence-duration-ms", "500",
                    "--vad-speech-pad-ms", "200",
                ]
            )
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        stderr_tail = (result.stderr or "").strip()[-600:]
        if result.returncode != 0 and diagnostic is not None:
            diagnostic(
                event="stt_error",
                level="error",
                reason="whisper_nonzero_exit",
                detail=f"exit={result.returncode} {stderr_tail}".strip(),
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
            if diagnostic is not None:
                diagnostic(
                    event="stt_rejected",
                    level="warning",
                    reason="empty_transcript",
                    detail=stderr_tail or None,
                )
            return None
        if any(noise in transcript for noise in NOISE_PATTERNS):
            print(f"[VOICE] ノイズ判定でスキップ: {transcript}")
            if diagnostic is not None:
                diagnostic(
                    event="stt_rejected",
                    level="warning",
                    reason="noise_pattern",
                    recognized_text=transcript,
                )
            return None
        if is_known_voice_noise_text(transcript):
            print(f"[VOICE] STT定型ノイズとしてスキップ: {transcript}")
            if diagnostic is not None:
                diagnostic(
                    event="stt_rejected",
                    level="warning",
                    reason="known_noise_text",
                    recognized_text=transcript,
                )
            return None
        if diagnostic is not None:
            diagnostic(
                event="stt_result",
                level="info",
                recognized_text=transcript,
            )
        return transcript
    except subprocess.TimeoutExpired:
        print("[VOICE] whisper がタイムアウト（60秒）")
        if diagnostic is not None:
            diagnostic(
                event="stt_error",
                level="error",
                reason="whisper_timeout",
                detail="timeout_sec=60",
            )
        return None
    except OSError as exc:
        print(f"[VOICE] whisper を実行できません: {exc}")
        if diagnostic is not None:
            diagnostic(
                event="stt_error",
                level="error",
                reason="whisper_process_error",
                detail=f"{type(exc).__name__}: {exc}",
            )
        return None
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def transcribe_with_empty_retry(
    cli: Path,
    model: Path,
    pcm: bytes,
    *,
    primary_no_speech_thold: float,
    retry_no_speech_thold: float | None,
    prompt: str,
    use_gpu: bool = True,
    diagnostic: VoiceDiagnosticCallback | None = None,
) -> str | None:
    """声あり区間の空結果だけを、文脈なし・緩い無音判定で一度再試行する。

    whisper.cpp は no_speech_prob がこの値を超えたときに無音と判定する。
    したがって、救済側は主試行より大きい値でなければならない。
    文脈プロンプトが短い発話を空結果にする場合があるため、救済時は外す。
    """

    rejection_reasons: list[str] = []

    def report(**fields: object) -> None:
        reason = fields.get("reason")
        if fields.get("event") == "stt_rejected" and isinstance(reason, str):
            rejection_reasons.append(reason)
        if diagnostic is not None:
            diagnostic(**fields)

    transcript = transcribe(
        cli,
        model,
        pcm,
        no_speech_thold=primary_no_speech_thold,
        prompt=prompt,
        use_gpu=use_gpu,
        diagnostic=report,
    )
    if not (
        transcript is None
        and rejection_reasons[-1:] == ["empty_transcript"]
        and retry_no_speech_thold is not None
        and retry_no_speech_thold > primary_no_speech_thold
    ):
        return transcript
    if diagnostic is not None:
        diagnostic(
            event="stt_started",
            level="info",
            reason="empty_transcript_retry",
            detail=(
                "attempt=retry prompt=none "
                f"no_speech_thold={retry_no_speech_thold}"
            ),
        )
    return transcribe(
        cli,
        model,
        pcm,
        no_speech_thold=retry_no_speech_thold,
        prompt="",
        use_gpu=use_gpu,
        diagnostic=diagnostic,
    )


def deliver(
    base_url: str,
    auth_token: str | None,
    text: str,
    *,
    diagnostic: VoiceDiagnosticCallback | None = None,
) -> None:
    headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
    try:
        response = httpx.post(
            f"{base_url}/api/v1/player-input",
            json={"text": text, "source": "voice"},
            headers=headers,
            timeout=3.0,
        )
        payload = response.json()
        if payload.get("accepted"):
            print(f"[VOICE] → 届けた (session={payload.get('session_id')})")
            if diagnostic is not None:
                diagnostic(
                    event="delivery",
                    level="info",
                    reason="accepted",
                    recognized_text=text,
                    detail=f"session={payload.get('session_id')}",
                )
        else:
            print(f"[VOICE] → サーバが受け取らず: {payload.get('reason')}（マイクラ接続待ち？）")
            if diagnostic is not None:
                diagnostic(
                    event="delivery",
                    level="warning",
                    reason=str(payload.get("reason") or "rejected"),
                    recognized_text=text,
                )
    except httpx.HTTPError as exc:
        print(f"[VOICE] → サーバに届かない: {exc}（dogido_server は起動してる？）")
        if diagnostic is not None:
            diagnostic(
                event="delivery",
                level="error",
                reason="server_unreachable",
                recognized_text=text,
                detail=f"{type(exc).__name__}: {exc}",
            )


def report_voice_diagnostic(
    base_url: str,
    auth_token: str | None,
    *,
    event: str,
    level: str = "info",
    recognized_text: str | None = None,
    reason: str | None = None,
    detail: str | None = None,
    prompt_mode: VoicePromptMode | None = None,
    duration_ms: int | None = None,
) -> None:
    """音声波形を送らず、STTの段階・文字列・失敗理由だけをサーバーへ送る。"""

    headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
    payload = {
        "schema_version": 1,
        "event": event,
        "level": level,
        "recognized_text": recognized_text,
        "reason": reason,
        "detail": detail,
        "prompt_mode": prompt_mode,
        "duration_ms": duration_ms,
    }
    try:
        response = httpx.post(
            f"{base_url}/api/v1/voice-input/diagnostics",
            json=payload,
            headers=headers,
            timeout=1.5,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        # サーバー停止中は診断画面も見られない。音声入力本体を止めず端末表示を正とする。
        return


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


def main(*, settings=None, on_transcript=None, diagnostic_sink=None, on_ready=None, on_stopped=None) -> None:
    """通常はHTTP配送。独立音声試験だけは明示callbackへ同じSTT結果を渡す。"""
    settings = settings or get_settings()
    cli, model = resolve_whisper_paths(settings)
    vad_paths = resolve_vad_paths(settings, cli)
    base_url = f"http://{settings.bind_host}:{settings.bind_port}"

    def send_diagnostic(**fields: object) -> None:
        if diagnostic_sink is not None:
            diagnostic_sink(**fields)
            return
        report_voice_diagnostic(
            base_url,
            settings.auth_token,
            **fields,
        )

    diagnostic = VoiceDiagnosticDispatcher(send_diagnostic)
    wake_word = settings.voice_wake_word.strip()
    last_prompt_mode: VoicePromptMode | None = None

    def process_segment(segment: CapturedSpeech) -> None:
        nonlocal last_prompt_mode
        if vad_paths is not None:
            vad_cli, vad_model = vad_paths
            if not contains_speech(
                vad_cli,
                vad_model,
                segment.pcm,
                threshold=settings.voice_vad_threshold,
                diagnostic=diagnostic,
            ):
                print("[VOICE] Silero VAD: 声なしとしてスキップ")
                return

        prompt_mode = "normal" if on_transcript is not None else fetch_voice_prompt_mode(
            base_url,
            settings.auth_token,
            diagnostic=diagnostic,
        )
        if prompt_mode != last_prompt_mode:
            label = (
                "川柳workshop"
                if prompt_mode == "haiku_workshop"
                else "通常会話"
            )
            print(f"[VOICE] STT文脈: {label}")
            diagnostic(
                event="context",
                level="info",
                prompt_mode=prompt_mode,
                reason="prompt_mode_selected",
            )
            last_prompt_mode = prompt_mode
        started_at = time.monotonic()
        diagnostic(
            event="stt_started",
            level="info",
            duration_ms=segment.duration_ms,
            detail=f"attempt=primary voiced_ms={segment.voiced_ms}",
        )
        transcript = transcribe_with_empty_retry(
            cli,
            model,
            segment.pcm,
            primary_no_speech_thold=settings.voice_no_speech_thold,
            retry_no_speech_thold=settings.voice_no_speech_retry_thold,
            prompt=stt_prompt(prompt_mode),
            diagnostic=diagnostic,
        )
        elapsed_ms = round((time.monotonic() - started_at) * 1000)
        diagnostic(
            event="stt_finished",
            level="info",
            recognized_text=transcript,
            reason="recognized" if transcript else "no_result",
            duration_ms=elapsed_ms,
        )
        if not transcript:
            return
        print(f"[VOICE] 認識: {transcript}")
        if wake_word and wake_word not in transcript:
            print("[VOICE] ウェイクワード無しのためスキップ")
            diagnostic(
                event="wake_word_rejected",
                level="warning",
                reason="wake_word_missing",
                recognized_text=transcript,
            )
            return
        if on_transcript is not None:
            on_transcript(transcript)
        else:
            deliver(base_url, settings.auth_token, transcript, diagnostic=diagnostic)

    print(f"[VOICE] whisper: {cli.name} / model: {model.name}")
    capture_label = (f"Core Audio + WebRTC AEC3 ({settings.voice_echo_input_uid or 'macOS既定マイク'})"
                     if settings.voice_echo_cancellation == "webrtc" else
                     f'avfoundation "{settings.voice_input_device}"')
    destination = "独立試験callback（本体HTTP配送なし）" if on_transcript is not None else base_url
    print(f"[VOICE] mic: {capture_label} / delivery: {destination}")
    print(f"[VOICE] 音量しきい値: {settings.voice_rms_threshold}（DOGIDO_VOICE_RMS_THRESHOLD で調整）")
    print(
        f"[VOICE] 発話区切り: 無音 {settings.voice_silence_ms}ms / "
        f"最長 {settings.voice_max_speech_sec:g}秒"
    )
    if vad_paths is None:
        print("[VOICE] Silero VAD: 未検出（RMS判定だけで継続）")
    else:
        print(f"[VOICE] Silero VAD: {vad_paths[1].name}（声の有無だけ判定）")
    if wake_word:
        print(f"[VOICE] ウェイクワード: 「{wake_word}」を含む発話だけ届けます")
    if settings.voice_echo_cancellation == "off":
        print("[VOICE] ※ドギドの声をマイクが拾うとループするので、ヘッドホン推奨やで")
    else:
        print("[VOICE] Macの再生音全体をエコー除去の参照に使います（参照音の保存・送信なし）。")

    try:
        process = spawn_capture(settings, raw_factory=spawn_ffmpeg, diagnostic=diagnostic)
    except Exception as exc:
        diagnostic.close()
        raise SystemExit(f"音声入力を開始できません: {exc}") from exc
    assert process.stdout is not None
    stt_worker = SpeechRecognitionWorker(
        process_segment,
        diagnostic=diagnostic,
        max_pending=settings.voice_stt_max_pending_segments,
        max_age_sec=settings.voice_stt_max_segment_age_sec,
    )

    silence_frames_to_end = max(1, settings.voice_silence_ms // FRAME_MS)
    min_speech_frames = max(1, settings.voice_min_speech_ms // FRAME_MS)
    max_speech_frames = int(settings.voice_max_speech_sec * 1000 // FRAME_MS)

    pre_roll: deque[bytes] = deque(maxlen=PRE_ROLL_FRAMES)
    speech: list[bytes] = []
    voiced_frames = 0
    silent_frames = 0
    recording = False

    print("[VOICE] 待機中…話しかけてや")
    ready = False
    try:
        while True:
            frame = process.read_frame(FRAME_BYTES)
            if not frame or len(frame) < FRAME_BYTES:
                if on_stopped is not None:
                    on_stopped()  # STT・captureの終了待ちより先にWeb起動権限を無効化する。
                stderr_tail = process.error_tail()
                print("[VOICE] マイク入力が止まりました。", stderr_tail)
                diagnostic(
                    event="capture",
                    level="error",
                    reason="microphone_stopped",
                    detail=stderr_tail or None,
                )
                if settings.voice_echo_cancellation == "webrtc":
                    print("[VOICE] AECを停止しました。音声キャプチャ権限・機器・docs/voice-echo-cancellation.mdを確認してください。")
                else:
                    print("[VOICE] マイク権限（システム設定→プライバシー→マイク→ターミナル）とデバイス番号を確認してください。")
                    print('[VOICE] デバイス一覧: ffmpeg -f avfoundation -list_devices true -i ""')
                break
            if not ready:
                ready = True
                if on_ready is not None:
                    on_ready()
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
                duration_ms = len(speech) * FRAME_MS
                voiced_ms = voiced_frames * FRAME_MS
                if voiced_frames >= min_speech_frames:
                    diagnostic(
                        event="capture",
                        level="info",
                        reason="speech_segment",
                        duration_ms=duration_ms,
                        detail=f"voiced_ms={voiced_ms}",
                    )
                    accepted = stt_worker.submit(
                        CapturedSpeech(
                            pcm=b"".join(speech),
                            duration_ms=duration_ms,
                            voiced_ms=voiced_ms,
                            captured_at=time.monotonic(),
                        )
                    )
                    if not accepted:
                        diagnostic(
                            event="stt_queue",
                            level="warning",
                            reason="segment_not_queued",
                            duration_ms=duration_ms,
                        )
                # 最低発話時間に届かない断片は、雨音などの定常環境音でも頻発する。
                # STTへ渡しておらず調査材料にもならないため、診断ログへは送らない。
                speech = []
                voiced_frames = 0
                silent_frames = 0
    except KeyboardInterrupt:
        print("\n[VOICE] 終了します")
    finally:
        process.close()
        stt_worker.close()
        diagnostic.close()


if __name__ == "__main__":
    main()
