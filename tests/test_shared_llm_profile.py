"""Rust起動へ渡す共有設定。旧Pythonの生成・shutdown統合は終了。"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from dogido_server.config import get_settings


ROOT = Path(__file__).resolve().parents[1]
SHARED_ENV_EXAMPLE = ROOT / ".env.shared.example"


@pytest.fixture(autouse=True)
def _reset_cached_settings() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _clear_runtime_llm_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOGIDO_LLM_") or key in {
            "DOGIDO_ENV_PROFILE",
            "DOGIDO_MLX_MODEL_ID",
            "DOGIDO_PLATFORM_AI_PROVIDER",
        }:
            monkeypatch.delenv(key, raising=False)


def test_standalone_profile_keeps_existing_dotenv_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_runtime_llm_environment(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DOGIDO_ENV_PROFILE", "standalone")
    (tmp_path / ".env").write_text(
        "DOGIDO_LLM_BACKEND=mlx\n"
        "DOGIDO_LLM_PROVIDER=local\n"
        "DOGIDO_MLX_MODEL_ID=synthetic-standalone-model\n"
        "DOGIDO_LLM_MAX_TOKENS=87\n",
        encoding="utf-8",
    )

    settings = get_settings()

    assert settings.llm_backend == "mlx"
    assert settings.mlx_model_id == "synthetic-standalone-model"
    assert settings.llm_max_tokens == 87
    assert settings.platform_ai_provider == "auto"


def test_shared_profile_overlays_and_validates_both_routes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_runtime_llm_environment(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DOGIDO_ENV_PROFILE", "shared")
    (tmp_path / ".env").write_text(
        "DOGIDO_LLM_BACKEND=mlx\n"
        "DOGIDO_LLM_PROVIDER=local\n"
        "DOGIDO_MLX_MODEL_ID=synthetic-standalone-model\n"
        "DOGIDO_LLM_CHAT_PROVIDER=openai\n"
        "DOGIDO_LLM_CHAT_MODEL=synthetic-old-chat-model\n"
        "DOGIDO_LLM_CHAT_MAX_TOKENS=321\n"
        "DOGIDO_LLM_HAIKU_PROVIDER=claude\n"
        "DOGIDO_LLM_HAIKU_MODEL=synthetic-old-haiku-model\n"
        "DOGIDO_LLM_HAIKU_MAX_TOKENS=193\n"
        "DOGIDO_LLM_TIMEOUT_SEC=23\n",
        encoding="utf-8",
    )
    (tmp_path / ".env.shared").write_text(
        SHARED_ENV_EXAMPLE.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    settings = get_settings()

    assert settings.llm_timeout_sec == 23
    assert settings.platform_ai_provider == "chat"
    assert settings.mlx_model_id == "synthetic-standalone-model"
    for route, expected_max_tokens in (("chat", 321), ("haiku", 193)):
        route_settings = settings.llm_route_settings(route)
        assert route_settings.llm_effective_backend == "chat_completions"
        assert route_settings.llm_provider == "local"
        assert route_settings.llm_resolved_base_url == "http://127.0.0.1:8080/v1"
        assert route_settings.llm_model == "default_model"
        assert route_settings.llm_api_key == ""
        assert route_settings.llm_max_tokens == expected_max_tokens


def test_shared_profile_fails_closed_when_overlay_is_missing_or_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_runtime_llm_environment(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DOGIDO_ENV_PROFILE", "shared")
    (tmp_path / ".env").write_text("DOGIDO_LLM_BACKEND=mlx\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match=r"requires .*\.env\.shared"):
        get_settings()

    get_settings.cache_clear()
    invalid_overlay = SHARED_ENV_EXAMPLE.read_text(encoding="utf-8").replace(
        "DOGIDO_LLM_HAIKU_MODEL=default_model",
        "DOGIDO_LLM_HAIKU_MODEL=synthetic-standalone-model",
    )
    (tmp_path / ".env.shared").write_text(invalid_overlay, encoding="utf-8")

    with pytest.raises(RuntimeError, match="DOGIDO_LLM_HAIKU_MODEL"):
        get_settings()
