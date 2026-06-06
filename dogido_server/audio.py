# audio.py
from __future__ import annotations

from collections import deque
import hashlib
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
import time

import httpx

from dogido_server.config import Settings
from dogido_server.cues import resolve_cue_path
from dogido_server.state_machine import AudioAction


# ---- データクラス ----

@dataclass(slots=True)
class RunningAudio:
    """再生中プロセスのハンドル。

    cleanup_path: 再生後に削除すべき一時ファイルのパス
    cue_id: 割り込み制御で「今何を再生しているか」を識別するための ID
    """
    process: subprocess.Popen[bytes]
    cleanup_path: Path | None = None
    cue_id: str | None = None


# ---- バックエンド基底クラス ----

class SpeechBackend:
    """TTS（テキスト→音声）バックエンドの基底クラス。

    通常発話・助言・雑談など、通常 TTS が担う発話に使う。
    緊急悲鳴は CueBackend 側で処理する。
    """
    def start(self, text: str) -> RunningAudio:
        raise NotImplementedError

    def prewarm_texts(self, texts: list[str]) -> None:
        # サブクラスで上書きする。起動時に定型文を事前合成してキャッシュしておくためのフック
        return None


class CueBackend:
    """キャッシュ済み音声ファイルの再生バックエンドの基底クラス。

    悲鳴・警告など低遅延が必要な緊急音声を担う。
    ファイルが見つからない場合は None を返してスキップできる。
    """
    def start(self, cue_id: str) -> RunningAudio | None:
        raise NotImplementedError


# ---- SpeechBackend 実装 ----

class NoopSpeechBackend(SpeechBackend):
    """/usr/bin/true を即時終了させるだけのダミー実装。

    CI・テスト時など音声出力が不要な環境で使う。
    """
    def start(self, text: str) -> RunningAudio:
        process = subprocess.Popen(["/usr/bin/true"])
        return RunningAudio(process=process)


class SaySpeechBackend(SpeechBackend):
    """macOS の say コマンドを使う TTS バックエンド。

    VoiceVox が使えない環境でのフォールバック、または開発用として使う。
    """
    def __init__(self, voice: str | None = None) -> None:
        self.voice = voice

    def start(self, text: str) -> RunningAudio:
        command = ["say"]
        if self.voice:
            command.extend(["-v", self.voice])
        command.append(text)
        return RunningAudio(process=subprocess.Popen(command))


class VoicevoxSpeechBackend(SpeechBackend):
    """VoiceVox エンジンを使う TTS バックエンド。

    同一テキスト・同一設定の音声は WAV ファイルとしてキャッシュし、
    2 回目以降は合成 API を呼ばずに再生する。
    キャッシュキーに話者 ID・速度・ピッチ・音量・サンプリングレートを含めるため、
    設定変更後に古いキャッシュが使われることはない。
    """
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.settings.voicevox_temp_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir = self.settings.voicevox_temp_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def start(self, text: str) -> RunningAudio:
        cached = self._ensure_cached(text)
        # cleanup_path=None: キャッシュファイルは再生後も残す
        return RunningAudio(process=subprocess.Popen(["afplay", str(cached)]), cleanup_path=None)

    def prewarm_texts(self, texts: list[str]) -> None:
        """起動時に定型文を事前合成してキャッシュする。

        合成失敗した文は無視して続行する（1 件のエラーで全部止めない）。
        """
        for text in texts:
            cleaned = text.strip()
            if not cleaned:
                continue
            try:
                self._ensure_cached(cleaned)
            except Exception:
                continue

    def _ensure_cached(self, text: str) -> Path:
        """キャッシュがあれば返す。なければ VoiceVox API で合成してキャッシュに保存する。"""
        cached = self._cached_path_for(text)
        if cached.exists():
            return cached

        query_url = f"{self.settings.voicevox_url}/audio_query"
        synth_url = f"{self.settings.voicevox_url}/synthesis"
        params = {"speaker": self.settings.voicevox_speaker, "text": text}

        with httpx.Client(timeout=15.0) as client:
            # Step1: audio_query でクエリオブジェクトを取得し、話速・ピッチなどを上書き
            query = client.post(query_url, params=params)
            query.raise_for_status()
            payload = query.json()
            payload["speedScale"] = self.settings.voicevox_speed_scale
            payload["pitchScale"] = self.settings.voicevox_pitch_scale
            payload["volumeScale"] = self.settings.voicevox_volume_scale
            if self.settings.voicevox_output_sampling_rate is not None:
                payload["outputSamplingRate"] = self.settings.voicevox_output_sampling_rate

            # Step2: synthesis で WAV バイナリを取得
            synth = client.post(
                synth_url,
                params={"speaker": self.settings.voicevox_speaker},
                json=payload,
            )
            synth.raise_for_status()

        cached.write_bytes(synth.content)
        return cached

    def _cached_path_for(self, text: str) -> Path:
        """テキストと全音声パラメータを結合して SHA-1 ハッシュを作りキャッシュパスを返す。

        パラメータが 1 つでも変われば別ファイルになるため、設定変更後の古いキャッシュ再利用を防ぐ。
        """
        cache_key = "|".join(
            [
                str(self.settings.voicevox_speaker),
                str(self.settings.voicevox_speed_scale),
                str(self.settings.voicevox_pitch_scale),
                str(self.settings.voicevox_volume_scale),
                str(self.settings.voicevox_output_sampling_rate),
                text,
            ]
        )
        digest = hashlib.sha1(cache_key.encode("utf-8")).hexdigest()[:20]
        return self.cache_dir / f"{digest}.wav"


# ---- CueBackend 実装 ----

class NoopCueBackend(CueBackend):
    """何もしないダミー実装。CI・テスト用。"""
    def start(self, cue_id: str) -> RunningAudio | None:
        return None


class AfplayCueBackend(CueBackend):
    """afplay を使って cue 音声ファイルや加工済みバリアントを再生するバックエンド。

    一部の cue_id は元ファイルをそのまま使わず、ffmpeg でトリム・フェード加工した
    バリアントファイルを生成してからキャッシュする。
    加工済みファイルが存在すれば ffmpeg は呼ばない。
    """
    def __init__(self, cue_dir: Path | None, cache_dir: Path) -> None:
        self.cue_dir = cue_dir
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def start(self, cue_id: str) -> RunningAudio | None:
        # suppressed_panic 状態で流す「ハァハァ」息遣い音声
        # 4.8 秒にトリムし、フェードイン・フェードアウトを付けてループ感を出す
        if cue_id == "suppressed_breath":
            candidate = self._ensure_trimmed_variant(
                "suppressed_breath",
                4.8,
                fade_in_duration_seconds=1.2,
                fade_out_start_seconds=3.2,
                fade_out_duration_seconds=1.6,
            )
            if candidate is not None:
                return RunningAudio(process=subprocess.Popen(["afplay", str(candidate)]), cue_id=cue_id)
            return None
        # suppressed_panic 解除時に息遣い音声を自然に終わらせるフェードアウト版（1.4 秒）
        if cue_id == "suppressed_breath_fadeout":
            candidate = self._ensure_fade_variant("suppressed_breath")
            if candidate is not None:
                return RunningAudio(process=subprocess.Popen(["afplay", str(candidate)]), cue_id=cue_id)
            return None
        # 上記以外は cue_dir からファイルをそのまま再生
        candidate = resolve_cue_path(self.cue_dir, cue_id)
        if candidate is not None:
            return RunningAudio(process=subprocess.Popen(["afplay", str(candidate)]), cue_id=cue_id)
        return None

    def _ensure_fade_variant(self, source_cue_id: str) -> Path | None:
        """元ファイルを 1.4 秒でフェードアウトしたバリアントを生成してキャッシュする。

        ffmpeg が失敗した場合は元ファイルをフォールバックとして返す。
        """
        source = resolve_cue_path(self.cue_dir, source_cue_id)
        if source is None:
            return None
        output = self.cache_dir / f"{source_cue_id}_fadeout_1_4s.wav"
        if output.exists():
            return output
        try:
            subprocess.run(
                [
                    "/opt/homebrew/bin/ffmpeg",
                    "-y",
                    "-i",
                    str(source),
                    "-t",
                    "1.4",
                    "-af",
                    "afade=t=out:st=0:d=1.4",
                    str(output),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            # 加工失敗時は元ファイルで代用
            return source
        return output

    def _ensure_trimmed_variant(
        self,
        source_cue_id: str,
        duration_seconds: float,
        fade_in_duration_seconds: float | None = None,
        fade_out_start_seconds: float | None = None,
        fade_out_duration_seconds: float | None = None,
    ) -> Path | None:
        """元ファイルをトリム・フェード加工したバリアントを生成してキャッシュする。

        出力ファイル名にトリム長・フェード設定を含めるため、
        パラメータを変えるたびに別ファイルが生成される。
        ffmpeg が失敗した場合は元ファイルをフォールバックとして返す。
        """
        source = resolve_cue_path(self.cue_dir, source_cue_id)
        if source is None:
            return None
        # ファイル名のラベルに小数点を含めないよう "." -> "_" に置換
        duration_label = str(duration_seconds).replace(".", "_")
        fade_label = ""
        if fade_in_duration_seconds is not None:
            fade_label += f"_in_{str(fade_in_duration_seconds).replace('.', '_')}"
        if fade_out_start_seconds is not None and fade_out_duration_seconds is not None:
            fade_label = (
                f"{fade_label}_out_{str(fade_out_start_seconds).replace('.', '_')}_{str(fade_out_duration_seconds).replace('.', '_')}"
            )
        output = self.cache_dir / f"{source_cue_id}_{duration_label}s{fade_label}.wav"
        if output.exists():
            return output
        try:
            command = [
                "/opt/homebrew/bin/ffmpeg",
                "-y",
                "-i",
                str(source),
                "-t",
                str(duration_seconds),
            ]
            # フェードフィルタをカンマ結合で afade チェーンにする
            filters: list[str] = []
            if fade_in_duration_seconds is not None:
                filters.append(f"afade=t=in:st=0:d={fade_in_duration_seconds}")
            if fade_out_start_seconds is not None and fade_out_duration_seconds is not None:
                filters.append(
                    f"afade=t=out:st={fade_out_start_seconds}:d={fade_out_duration_seconds}"
                )
            if filters:
                command.extend(
                    [
                        "-af",
                        ",".join(filters),
                    ]
                )
            command.append(str(output))
            subprocess.run(
                command,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            # 加工失敗時は元ファイルで代用
            return source
        return output


class SayCueBackend(CueBackend):
    """say コマンドで cue_id をそのまま読み上げるデバッグ用バックエンド。

    実機の音声ファイルがない開発環境で動作確認するときに使う。
    """
    def start(self, cue_id: str) -> RunningAudio | None:
        return RunningAudio(process=subprocess.Popen(["say", cue_id]), cue_id=cue_id)


# ---- メインディスパッチャ ----

class AudioDispatcher:
    """音声再生キューの管理と優先割り込みを担うクラス。

    ワーカースレッドが deque からアクションを取り出して順番に再生する。
    割り込みフラグが立ったアクションが来ると、現在の再生を止めて即座に差し替える。
    ただし protect_ms が有効な再生中は、ハード割り込みでない限りキューの先頭に差し戻す。

    優先順位そのものは主に上流の状態機械が決めており、
    このクラスは割り込みと protect_ms の制御を担当する。
    """
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._current: RunningAudio | None = None
        self._current_protected_until: float = 0.0  # monotonic 時刻。これを超えるまで通常割り込みをブロック
        self._pending: deque[tuple[int, list[AudioAction]]] = deque()
        self._epoch = 0  # 割り込み発生時にインクリメント。古いエポックのアクションはワーカーがスキップ

        # ---- TTS バックエンドの選択 ----
        if settings.tts_backend == "voicevox":
            self.speech_backend: SpeechBackend = VoicevoxSpeechBackend(settings)
            # VoiceVox がクラッシュ・タイムアウトしたときの保険として say を用意
            self.fallback_speech_backend: SpeechBackend = SaySpeechBackend(settings.say_voice)
        elif settings.tts_backend == "say":
            self.speech_backend = SaySpeechBackend(settings.say_voice)
            self.fallback_speech_backend = self.speech_backend
        else:
            self.speech_backend = NoopSpeechBackend()
            self.fallback_speech_backend = self.speech_backend

        # ---- キュー再生バックエンドの選択 ----
        if settings.cue_backend == "afplay":
            self.cue_backend: CueBackend = AfplayCueBackend(
                settings.cue_audio_dir,
                settings.voicevox_temp_dir.parent / "cue_cache",
            )
        elif settings.cue_backend == "say":
            self.cue_backend = SayCueBackend()
        else:
            self.cue_backend = NoopCueBackend()

        # デーモンスレッドにすることでメインプロセス終了時に自動で終わる
        self._worker = threading.Thread(target=self._worker_loop, name="dogido-audio", daemon=True)
        self._worker.start()

    def prewarm_speech_texts(self, texts: list[str]) -> None:
        """起動時に定型文を事前合成してキャッシュする（VoiceVox 用）。"""
        self.speech_backend.prewarm_texts(texts)

    def play_actions(self, actions: list[AudioAction]) -> None:
        """アクションリストをキューに積む。割り込みフラグがあれば現在再生を止める。

        protect_ms が有効な音声が再生中の場合、通常割り込みはキューの先頭に差し戻す。
        ただし悲鳴などのハード割り込みは protect_ms を無視して強制差し替えする。
        """
        if not actions:
            return
        with self._condition:
            if any(action.interrupt for action in actions):
                if self._is_current_protected_locked() and not self._has_hard_interrupt(actions):
                    # 保護中かつハード割り込みでない -> キュー先頭に差し戻してあとで再試行
                    self._pending.appendleft((self._epoch, list(actions)))
                    self._condition.notify()
                    return
                # 割り込み確定: エポックを進めて古いキューを全破棄し、現在の再生を停止
                self._epoch += 1
                self._pending.clear()
                actions = self._prepare_interrupt_actions_locked(actions)
                self._stop_current_locked()
                if not actions:
                    return
            self._pending.append((self._epoch, list(actions)))
            self._condition.notify()

    def _prepare_interrupt_actions_locked(self, actions: list[AudioAction]) -> list[AudioAction]:
        """割り込み発生時に、先頭が control レイヤーなら取り除き、息遣いフェードアウトを挿入する。

        suppressed_breath 再生中に割り込みが入ると、いきなり止まるより
        フェードアウトで終わらせたほうが自然なため先頭に追加する。
        """
        if not actions:
            return actions
        first = actions[0]
        if first.layer == "flush":
            return []
        if first.layer != "control":
            return actions
        remaining = list(actions[1:])
        # 息遣い中に割り込まれたらフェードアウト版に差し替えてから次の音声を流す
        if self._current is not None and self._current.cue_id == "suppressed_breath":
            remaining.insert(0, AudioAction(layer="panic_cue", interrupt=False, cue_id="suppressed_breath_fadeout"))
        return remaining

    def _has_hard_interrupt(self, actions: list[AudioAction]) -> bool:
        """protect_ms を無視して強制割り込みすべき悲鳴系アクションかどうかを判定する。"""
        return any(
            action.layer == "flush"
            or action.cue_id in {"panic_scream_start", "front_spawn_scream", "ushiro_scream"}
            or (action.layer == "speech" and action.interrupt)
            for action in actions
        )

    def _is_current_protected_locked(self) -> bool:
        """現在の再生が protect_ms の保護期間内かどうかを返す（ロック取得済み前提）。"""
        return self._current is not None and time.monotonic() < self._current_protected_until

    def _worker_loop(self) -> None:
        """バックグラウンドワーカー。キューからアクションを取り出して順番に再生する。

        エポックが変わっていたらそのバッチをスキップして次へ進む。
        """
        while True:
            with self._condition:
                while not self._pending:
                    self._condition.wait()
                epoch, actions = self._pending.popleft()

            for action in actions:
                handle, stale = self._start_action(action, expected_epoch=epoch)
                if stale:
                    # 割り込みで無効になったバッチはスキップ
                    break
                if handle is None:
                    continue
                # 再生が終わるまでここでブロック
                self._wait_for(handle)

    def _start_action(
        self,
        action: AudioAction,
        *,
        expected_epoch: int | None = None,
    ) -> tuple[RunningAudio | None, bool]:
        """アクションを実際に再生開始し RunningAudio を返す。

        cue_id が指定されていればキューバックエンドを優先する。
        text が指定されていれば TTS バックエンドを使い、失敗時はフォールバックを試みる。
        protect_ms が設定されていれば保護タイマーをセットする。
        expected_epoch が指定されていれば、再生開始直前に現在の epoch と照合する。
        """
        with self._lock:
            if expected_epoch is not None and expected_epoch != self._epoch:
                return None, True
            if action.interrupt:
                self._stop_current_locked()

            try:
                if action.layer == "speech" and action.text:
                    handle = self.speech_backend.start(action.text)
                elif action.cue_id:
                    handle = self.cue_backend.start(action.cue_id)
                    if handle is not None:
                        self._current = handle
                        return handle, False
                elif action.text:
                    handle = self.speech_backend.start(action.text)
                else:
                    # cue も text もない場合は何もしない
                    return None, False
            except Exception:
                if not action.text:
                    raise
                # TTS 失敗時は say にフォールバック
                handle = self.fallback_speech_backend.start(action.text)

            self._current = handle
            if action.protect_ms > 0:
                # 保護期間をセット（この間は通常割り込みをブロック）
                self._current_protected_until = time.monotonic() + (action.protect_ms / 1000.0)
            else:
                self._current_protected_until = 0.0
            return handle, False

    def _wait_for(self, handle: RunningAudio) -> None:
        """プロセス終了まで待機し、終了後にクリーンアップする。

        wait 中に別スレッドが _stop_current_locked() を呼んで _current を差し替えることがある。
        そのため、handle が _current と同一かどうかで後処理を分岐している。
        """
        try:
            handle.process.wait()
        finally:
            with self._lock:
                if self._current is handle:
                    self._cleanup_handle_locked(handle)
                    self._current = None
                    self._current_protected_until = 0.0
                else:
                    # 割り込みで差し替え済みの場合もクリーンアップは必要
                    self._cleanup_handle_locked(handle)

    def _stop_current_locked(self) -> None:
        """現在の再生プロセスを止める（ロック取得済み前提）。

        terminate -> 1 秒待ち -> kill の順で確実に終わらせる。
        """
        if self._current is None:
            return
        process = self._current.process
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1.0)
        self._cleanup_handle_locked(self._current)
        self._current = None
        self._current_protected_until = 0.0

    def _cleanup_handle_locked(self, handle: RunningAudio) -> None:
        """再生後に一時ファイルを削除する（ロック取得済み前提）。

        cleanup_path が None のキャッシュファイルは削除しない。
        """
        if handle.cleanup_path and handle.cleanup_path.exists():
            handle.cleanup_path.unlink(missing_ok=True)
