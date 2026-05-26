from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from dogido_server import __version__


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="DOGIDO_",
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = "dogido-server"
    service_version: str = __version__

    bind_host: str = "127.0.0.1"
    bind_port: int = 5055
    allow_non_local_bind: bool = False
    auth_token: str | None = None

    accepted_schema_version: str = "2026-05-24"
    max_batch_size: int = 25
    max_body_kb: int = 256
    heartbeat_interval_ms: int = 5000

    audio_enabled: bool = True
    decision_policy: Literal["py_trees", "legacy"] = "py_trees"
    llm_enabled: bool = True
    llm_backend: Literal["mlx", "noop"] = "mlx"
    tts_backend: Literal["voicevox", "say", "noop"] = "voicevox"
    cue_backend: Literal["afplay", "say", "noop"] = "afplay"
    cue_audio_dir: Path | None = Path("cue_voice")
    say_voice: str | None = None

    voicevox_url: str = "http://127.0.0.1:50021"
    voicevox_speaker: int = 21
    voicevox_speed_scale: float = 1.0
    voicevox_pitch_scale: float = 0.0
    voicevox_volume_scale: float = 1.0
    voicevox_output_sampling_rate: int | None = None
    voicevox_temp_dir: Path = Field(default_factory=lambda: Path(".dogido_tmp") / "voicevox")

    mlx_model_id: str | None = None
    llm_max_tokens: int = 72

    rear_warning_distance: float = 8.0
    panic_distance: float = 7.0
    multi_hostile_distance: float = 10.0
    combat_clear_distance: float = 10.0
    combat_clear_time_ms: int = 5000
    suppression_time_ms: int = 7000
    aftermath_time_ms: int = 8000
    panic_scream_cooldown_ms: int = 1200
    recent_damage_window_ms: int = 3000
    darkness_alert_threshold: float = 0.65
    occluded_entry_darkness_threshold: float = 0.45
    darkness_llm_comment_cooldown_ms: int = 20000
    dark_push_comment_cooldown_ms: int = 20000
    hostile_comment_cooldown_ms: int = 60000
    hostile_count_surge_threshold: int = 3
    hostile_count_surge_min_total: int = 4

    @property
    def is_local_only(self) -> bool:
        return not self.allow_non_local_bind


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
