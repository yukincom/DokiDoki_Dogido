from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from dogido_server import __version__

LLM_PROVIDER = Literal["local", "openai", "openrouter", "claude", "grok", "gemini", "custom"]
LLM_BACKEND = Literal["mlx", "openai_compatible", "chat_completions", "noop"]

PROVIDER_DEFAULT_BASE_URLS = {
    "local": "http://127.0.0.1:8080/v1",
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "claude": "https://api.anthropic.com/v1",
    "grok": "https://api.x.ai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "custom": None,
}

PROVIDER_API_FAMILIES = {
    "local": "chat_completions",
    "openai": "chat_completions",
    "openrouter": "chat_completions",
    "claude": "anthropic_messages",
    "grok": "chat_completions",
    "gemini": "gemini_generate_content",
    "custom": "chat_completions",
}


@dataclass(frozen=True, slots=True)
class LLMRouteSignature:
    backend: str
    provider: str
    mlx_model_id: str | None
    base_url: str | None
    model: str | None
    api_key: str | None
    timeout_sec: float
    max_tokens: int
    anthropic_version: str
    http_referer: str | None
    application_name: str | None


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
    default_call_name: str = "プレイヤー"

    audio_enabled: bool = True
    decision_policy: Literal["py_trees", "legacy"] = "py_trees"
    llm_enabled: bool = True
    llm_backend: LLM_BACKEND = "mlx"
    llm_provider: LLM_PROVIDER = "local"
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
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    llm_http_referer: str | None = None
    llm_application_name: str | None = None
    llm_anthropic_version: str = "2023-06-01"
    llm_timeout_sec: float = 20.0
    llm_max_tokens: int = 72

    llm_chat_backend: LLM_BACKEND | None = None
    llm_chat_provider: LLM_PROVIDER | None = None
    llm_chat_mlx_model_id: str | None = None
    llm_chat_base_url: str | None = None
    llm_chat_model: str | None = None
    llm_chat_api_key: str | None = None
    llm_chat_http_referer: str | None = None
    llm_chat_application_name: str | None = None
    llm_chat_anthropic_version: str | None = None
    llm_chat_timeout_sec: float | None = None
    llm_chat_max_tokens: int | None = None

    llm_haiku_backend: LLM_BACKEND | None = None
    llm_haiku_provider: LLM_PROVIDER | None = None
    llm_haiku_mlx_model_id: str | None = None
    llm_haiku_base_url: str | None = None
    llm_haiku_model: str | None = None
    llm_haiku_api_key: str | None = None
    llm_haiku_http_referer: str | None = None
    llm_haiku_application_name: str | None = None
    llm_haiku_anthropic_version: str | None = None
    llm_haiku_timeout_sec: float | None = None
    llm_haiku_max_tokens: int | None = None

    rear_warning_distance: float = 8.0
    panic_distance: float = 7.0
    multi_hostile_distance: float = 10.0
    combat_clear_distance: float = 10.0
    combat_clear_time_ms: int = 5000
    suppression_time_ms: int = 7000
    aftermath_time_ms: int = 8000
    pending_safe_aftermath_window_ms: int = 20000
    panic_scream_cooldown_ms: int = 1200
    recent_damage_window_ms: int = 3000
    darkness_advice_light_threshold: int = 3
    darkness_alert_threshold: float = 0.72
    darkness_advice_cooldown_ms: int = 60000
    occluded_entry_darkness_threshold: float = 0.9
    occluded_entry_light_threshold: int = 3
    lit_interior_safe_light_threshold: int = 9
    lit_interior_safe_max_connected_volume: int = 24
    lit_interior_safe_min_spawn_distance: float = 4.0
    lit_interior_safe_max_ceiling_height: float = 5.0
    lit_interior_safe_light_source_distance: float = 4.0
    cramped_dark_burrow_max_connected_volume: int = 12
    cramped_dark_burrow_max_ceiling_height: float = 3.0
    cramped_dark_burrow_min_wall_count: int = 3
    cramped_dark_burrow_min_enclosure_score: float = 0.85
    darkness_llm_comment_cooldown_ms: int = 300000
    foliage_darkness_comment_cooldown_ms: int = 600000
    submerged_darkness_comment_cooldown_ms: int = 600000
    submerged_darkness_depth_threshold: int = 5
    dark_push_comment_cooldown_ms: int = 20000
    dark_push_breath_loop_ms: int = 3800
    dark_push_breath_delay_ms: int = 5000
    dark_push_after_breath_defer_ms: int = 8000
    dark_push_progress_distance: float = 1.0
    dark_push_escalation_light_threshold: int = 1
    dark_push_escalation_darkness_threshold: float = 0.7
    dark_push_worse_light_delta: int = 2
    dark_push_worse_darkness_delta: float = 0.12
    dark_push_recover_darkness_margin: float = 0.02
    emergency_shelter_night_start: int = 13000
    emergency_shelter_morning_cutoff: int = 2000
    emergency_shelter_respawn_distance: float = 50.0
    emergency_shelter_max_ceiling_height: float = 3.0
    home_bed_prompt_distance: float = 10.0
    sleep_prompt_cooldown_ms: int = 60000
    sleeping_neighbor_comment_cooldown_ms: int = 60000
    special_biome_comment_cooldown_ms: int = 600000
    haiku_silence_time_ms: int = 300000
    haiku_structured_max_tokens: int = 192
    hostile_comment_cooldown_ms: int = 60000
    occluded_hostile_presence_comment_cooldown_ms: int = 300000
    daylight_water_comment_cooldown_ms: int = 120000
    burning_visual_comment_cooldown_ms: int = 10000
    multi_hostile_comment_cooldown_ms: int = 30000
    stalled_visual_comment_delay_ms: int = 60000
    stalled_visual_comment_cooldown_ms: int = 60000
    hostile_count_surge_threshold: int = 3
    hostile_count_surge_min_total: int = 4
    auditory_ignore_distance: float = 4.5

    @property
    def is_local_only(self) -> bool:
        return not self.allow_non_local_bind

    @property
    def llm_uses_remote_api(self) -> bool:
        return self.llm_effective_backend in {
            "chat_completions",
            "anthropic_messages",
            "gemini_generate_content",
        }

    @property
    def llm_uses_chat_completions(self) -> bool:
        return self.llm_uses_remote_api and self.llm_api_family == "chat_completions"

    @property
    def llm_api_family(self) -> str | None:
        resolved_backend = self._resolve_llm_backend(self.llm_backend, self.llm_provider)
        if resolved_backend not in {"openai_compatible", "chat_completions"}:
            return None
        return PROVIDER_API_FAMILIES.get(self.llm_provider, "chat_completions")

    @property
    def llm_effective_backend(self) -> str:
        if self.llm_backend == "noop":
            return "noop"
        resolved_backend = self._resolve_llm_backend(self.llm_backend, self.llm_provider)
        if resolved_backend in {"openai_compatible", "chat_completions"}:
            return self.llm_api_family or "chat_completions"
        return resolved_backend

    @property
    def llm_resolved_base_url(self) -> str | None:
        if not self.llm_uses_remote_api:
            return self.llm_base_url
        if self.llm_base_url:
            return self.llm_base_url
        return PROVIDER_DEFAULT_BASE_URLS.get(self.llm_provider)

    def llm_request_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.llm_provider == "claude":
            if self.llm_api_key:
                headers["x-api-key"] = self.llm_api_key
            headers["anthropic-version"] = self.llm_anthropic_version
            return headers
        if self.llm_provider == "gemini":
            if self.llm_api_key:
                headers["x-goog-api-key"] = self.llm_api_key
            return headers
        if self.llm_api_key:
            headers["Authorization"] = f"Bearer {self.llm_api_key}"
        if self.llm_provider == "openrouter":
            if self.llm_http_referer:
                headers["HTTP-Referer"] = self.llm_http_referer
            if self.llm_application_name:
                headers["X-Title"] = self.llm_application_name
        return headers

    def llm_route_settings(self, route: Literal["chat", "haiku"]) -> Settings:
        provider = self._llm_route_value(route, "provider", self.llm_provider)
        backend = self._resolve_llm_backend(
            self._llm_route_value(route, "backend", self.llm_backend),
            provider,
        )
        return self.model_copy(
            update={
                "llm_backend": backend,
                "llm_provider": provider,
                "mlx_model_id": self._llm_route_value(route, "mlx_model_id", self.mlx_model_id),
                "llm_base_url": self._llm_route_value(route, "base_url", self.llm_base_url),
                "llm_model": self._llm_route_value(route, "model", self.llm_model),
                "llm_api_key": self._llm_route_value(route, "api_key", self.llm_api_key),
                "llm_http_referer": self._llm_route_value(route, "http_referer", self.llm_http_referer),
                "llm_application_name": self._llm_route_value(route, "application_name", self.llm_application_name),
                "llm_anthropic_version": self._llm_route_value(route, "anthropic_version", self.llm_anthropic_version),
                "llm_timeout_sec": self._llm_route_value(route, "timeout_sec", self.llm_timeout_sec),
                "llm_max_tokens": self._llm_route_value(route, "max_tokens", self.llm_max_tokens),
            }
        )

    def llm_route_signature(self, route: Literal["chat", "haiku"]) -> LLMRouteSignature:
        route_settings = self.llm_route_settings(route)
        return LLMRouteSignature(
            backend=route_settings.llm_backend,
            provider=route_settings.llm_provider,
            mlx_model_id=route_settings.mlx_model_id,
            base_url=route_settings.llm_resolved_base_url,
            model=route_settings.llm_model,
            api_key=route_settings.llm_api_key,
            timeout_sec=route_settings.llm_timeout_sec,
            max_tokens=route_settings.llm_max_tokens,
            anthropic_version=route_settings.llm_anthropic_version,
            http_referer=route_settings.llm_http_referer,
            application_name=route_settings.llm_application_name,
        )

    def _llm_route_value(self, route: Literal["chat", "haiku"], name: str, default: object) -> object:
        route_value = getattr(self, f"llm_{route}_{name}")
        return default if route_value is None else route_value

    def _resolve_llm_backend(self, backend: object, provider: object) -> str:
        normalized_backend = str(backend or "noop")
        normalized_provider = str(provider or "local")
        if normalized_backend == "noop":
            return "noop"
        if normalized_backend == "mlx" and normalized_provider != "local":
            return "chat_completions"
        return normalized_backend


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
