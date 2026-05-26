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


@dataclass(slots=True)
class RunningAudio:
    process: subprocess.Popen[bytes]
    cleanup_path: Path | None = None
    cue_id: str | None = None


class SpeechBackend:
    def start(self, text: str) -> RunningAudio:
        raise NotImplementedError


class CueBackend:
    def start(self, cue_id: str) -> RunningAudio | None:
        raise NotImplementedError


class NoopSpeechBackend(SpeechBackend):
    def start(self, text: str) -> RunningAudio:
        process = subprocess.Popen(["/usr/bin/true"])
        return RunningAudio(process=process)


class SaySpeechBackend(SpeechBackend):
    def __init__(self, voice: str | None = None) -> None:
        self.voice = voice

    def start(self, text: str) -> RunningAudio:
        command = ["say"]
        if self.voice:
            command.extend(["-v", self.voice])
        command.append(text)
        return RunningAudio(process=subprocess.Popen(command))


class VoicevoxSpeechBackend(SpeechBackend):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.settings.voicevox_temp_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir = self.settings.voicevox_temp_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def start(self, text: str) -> RunningAudio:
        cached = self._cached_path_for(text)
        if cached.exists():
            return RunningAudio(process=subprocess.Popen(["afplay", str(cached)]), cleanup_path=None)

        query_url = f"{self.settings.voicevox_url}/audio_query"
        synth_url = f"{self.settings.voicevox_url}/synthesis"
        params = {"speaker": self.settings.voicevox_speaker, "text": text}

        with httpx.Client(timeout=15.0) as client:
            query = client.post(query_url, params=params)
            query.raise_for_status()
            payload = query.json()
            payload["speedScale"] = self.settings.voicevox_speed_scale
            payload["pitchScale"] = self.settings.voicevox_pitch_scale
            payload["volumeScale"] = self.settings.voicevox_volume_scale
            if self.settings.voicevox_output_sampling_rate is not None:
                payload["outputSamplingRate"] = self.settings.voicevox_output_sampling_rate

            synth = client.post(
                synth_url,
                params={"speaker": self.settings.voicevox_speaker},
                json=payload,
            )
            synth.raise_for_status()

        cached.write_bytes(synth.content)
        return RunningAudio(process=subprocess.Popen(["afplay", str(cached)]), cleanup_path=None)

    def _cached_path_for(self, text: str) -> Path:
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


class NoopCueBackend(CueBackend):
    def start(self, cue_id: str) -> RunningAudio | None:
        return None


class AfplayCueBackend(CueBackend):
    def __init__(self, cue_dir: Path | None, cache_dir: Path) -> None:
        self.cue_dir = cue_dir
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def start(self, cue_id: str) -> RunningAudio | None:
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
        if cue_id == "suppressed_breath_fadeout":
            candidate = self._ensure_fade_variant("suppressed_breath")
            if candidate is not None:
                return RunningAudio(process=subprocess.Popen(["afplay", str(candidate)]), cue_id=cue_id)
            return None
        candidate = resolve_cue_path(self.cue_dir, cue_id)
        if candidate is not None:
            return RunningAudio(process=subprocess.Popen(["afplay", str(candidate)]), cue_id=cue_id)
        return None

    def _ensure_fade_variant(self, source_cue_id: str) -> Path | None:
        source = resolve_cue_path(self.cue_dir, source_cue_id)
        if source is None:
            return None
        output = self.cache_dir / f"{source_cue_id}_fadeout_3_8s.wav"
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
                    "3.8",
                    "-af",
                    "afade=t=out:st=0:d=3.8",
                    str(output),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
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
        source = resolve_cue_path(self.cue_dir, source_cue_id)
        if source is None:
            return None
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
            return source
        return output


class SayCueBackend(CueBackend):
    def start(self, cue_id: str) -> RunningAudio | None:
        return RunningAudio(process=subprocess.Popen(["say", cue_id]), cue_id=cue_id)


class AudioDispatcher:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._current: RunningAudio | None = None
        self._current_protected_until: float = 0.0
        self._pending: deque[tuple[int, list[AudioAction]]] = deque()
        self._epoch = 0

        if settings.tts_backend == "voicevox":
            self.speech_backend: SpeechBackend = VoicevoxSpeechBackend(settings)
            self.fallback_speech_backend: SpeechBackend = SaySpeechBackend(settings.say_voice)
        elif settings.tts_backend == "say":
            self.speech_backend = SaySpeechBackend(settings.say_voice)
            self.fallback_speech_backend = self.speech_backend
        else:
            self.speech_backend = NoopSpeechBackend()
            self.fallback_speech_backend = self.speech_backend

        if settings.cue_backend == "afplay":
            self.cue_backend: CueBackend = AfplayCueBackend(
                settings.cue_audio_dir,
                settings.voicevox_temp_dir.parent / "cue_cache",
            )
        elif settings.cue_backend == "say":
            self.cue_backend = SayCueBackend()
        else:
            self.cue_backend = NoopCueBackend()

        self._worker = threading.Thread(target=self._worker_loop, name="dogido-audio", daemon=True)
        self._worker.start()

    def play_actions(self, actions: list[AudioAction]) -> None:
        if not actions:
            return
        with self._condition:
            if any(action.interrupt for action in actions):
                if self._is_current_protected_locked() and not self._has_hard_interrupt(actions):
                    self._pending.appendleft((self._epoch, list(actions)))
                    self._condition.notify()
                    return
                self._epoch += 1
                self._pending.clear()
                actions = self._prepare_interrupt_actions_locked(actions)
                self._stop_current_locked()
                if not actions:
                    return
            self._pending.append((self._epoch, list(actions)))
            self._condition.notify()

    def _prepare_interrupt_actions_locked(self, actions: list[AudioAction]) -> list[AudioAction]:
        if not actions:
            return actions
        first = actions[0]
        if first.layer != "control":
            return actions
        remaining = list(actions[1:])
        if self._current is not None and self._current.cue_id == "suppressed_breath":
            remaining.insert(0, AudioAction(layer="panic_cue", interrupt=False, cue_id="suppressed_breath_fadeout"))
        return remaining

    def _has_hard_interrupt(self, actions: list[AudioAction]) -> bool:
        return any(
            action.cue_id == "panic_scream_start" or (action.layer == "speech" and action.interrupt)
            for action in actions
        )

    def _is_current_protected_locked(self) -> bool:
        return self._current is not None and time.monotonic() < self._current_protected_until

    def _worker_loop(self) -> None:
        while True:
            with self._condition:
                while not self._pending:
                    self._condition.wait()
                epoch, actions = self._pending.popleft()

            for action in actions:
                with self._lock:
                    if epoch != self._epoch:
                        break
                handle = self._start_action(action)
                if handle is None:
                    continue
                self._wait_for(handle)

    def _start_action(self, action: AudioAction) -> RunningAudio | None:
        with self._lock:
            if action.interrupt:
                self._stop_current_locked()

            try:
                if action.cue_id:
                    handle = self.cue_backend.start(action.cue_id)
                    if handle is not None:
                        self._current = handle
                        return handle
                if action.text:
                    handle = self.speech_backend.start(action.text)
                else:
                    return None
            except Exception:
                if not action.text:
                    raise
                handle = self.fallback_speech_backend.start(action.text)

            self._current = handle
            if action.protect_ms > 0:
                self._current_protected_until = time.monotonic() + (action.protect_ms / 1000.0)
            else:
                self._current_protected_until = 0.0
            return handle

    def _wait_for(self, handle: RunningAudio) -> None:
        try:
            handle.process.wait()
        finally:
            with self._lock:
                if self._current is handle:
                    self._cleanup_handle_locked(handle)
                    self._current = None
                    self._current_protected_until = 0.0
                else:
                    self._cleanup_handle_locked(handle)

    def _stop_current_locked(self) -> None:
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
        if handle.cleanup_path and handle.cleanup_path.exists():
            handle.cleanup_path.unlink(missing_ok=True)
