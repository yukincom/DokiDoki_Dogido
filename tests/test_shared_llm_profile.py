from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from dogido_server.config import Settings, get_settings
from dogido_server.llm import (
    DogidoLLMRouter,
    LeafGenerationRequest,
    StructuredGenerationRequest,
)
from dogido_server.service import DogidoService


ROOT = Path(__file__).resolve().parents[1]
SHARED_ENV_EXAMPLE = ROOT / ".env.shared.example"
REQUEST_FIXTURES = Path(__file__).parent / "fixtures" / "shared_llm_requests.json"


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


def _shared_settings(tmp_path: Path | None = None) -> Settings:
    overrides: dict[str, object] = {}
    if tmp_path is not None:
        overrides["voicevox_temp_dir"] = tmp_path / "voicevox"
        overrides["memory_dir"] = tmp_path / "memory"
    return Settings(
        audio_enabled=False,
        tts_backend="noop",
        cue_backend="noop",
        memory_enabled=False,
        main_language_dialogue_enabled=False,
        main_language_web_enabled=False,
        llm_enabled=True,
        llm_backend="chat_completions",
        llm_provider="local",
        llm_base_url="http://127.0.0.1:8080/v1",
        llm_model="default_model",
        llm_api_key="",
        llm_chat_backend="chat_completions",
        llm_chat_provider="local",
        llm_chat_base_url="http://127.0.0.1:8080/v1",
        llm_chat_model="default_model",
        llm_chat_api_key="",
        llm_haiku_backend="chat_completions",
        llm_haiku_provider="local",
        llm_haiku_base_url="http://127.0.0.1:8080/v1",
        llm_haiku_model="default_model",
        llm_haiku_api_key="",
        platform_ai_provider="chat",
        **overrides,
    )


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


def test_shared_requests_preserve_prompts_limits_json_and_model_contract() -> None:
    fixtures = json.loads(REQUEST_FIXTURES.read_text(encoding="utf-8"))
    assert len(fixtures) == 3
    responses = {case["id"]: case["response"] for case in fixtures}
    pending = list(fixtures)

    def handler(request: httpx.Request) -> httpx.Response:
        case = pending.pop(0)
        body = json.loads(request.content)
        assert request.url == httpx.URL("http://127.0.0.1:8080/v1/chat/completions")
        assert body["model"] == "default_model"
        assert body["stream"] is False
        assert body["temperature"] == case["temperature"]
        assert body["max_tokens"] == case["max_tokens"]
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        assert [message["role"] for message in body["messages"]] == ["system", "user"]
        assert case["prompt_fragment"] in body["messages"][1]["content"]
        response = case["response"]
        content = response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    real_httpx_client = httpx.Client
    transport = httpx.MockTransport(handler)

    def client_factory(*args: object, **kwargs: object) -> httpx.Client:
        return real_httpx_client(
            transport=transport,
            base_url=str(kwargs["base_url"]),
            timeout=kwargs["timeout"],
            headers=kwargs.get("headers"),
        )

    router = DogidoLLMRouter(_shared_settings())
    with patch("dogido_server.llm.providers.httpx.Client", side_effect=client_factory):
        for case in fixtures:
            if case["request_type"] == "leaf":
                result = router.generate_leaf_text(
                    LeafGenerationRequest(
                        kind=case["kind"],
                        fallback_text=case["fallback"],
                        details=case["details"],
                        temperature=case["temperature"],
                        route=case["route"],
                        max_tokens=case["max_tokens"],
                    )
                )
                assert result == responses[case["id"]]
            else:
                result = router.generate_structured_json(
                    StructuredGenerationRequest(
                        kind=case["kind"],
                        fallback_value=case["fallback"],
                        details=case["details"],
                        temperature=case["temperature"],
                        route=case["route"],
                        max_tokens=case["max_tokens"],
                    )
                )
                assert result["__dogido_status"] == "accepted"
                for key, value in responses[case["id"]].items():
                    assert result[key] == value

    assert not pending


def test_shared_profile_never_preloads_or_falls_back_to_internal_mlx() -> None:
    router = DogidoLLMRouter(_shared_settings())
    request = LeafGenerationRequest(
        kind="occluded_entry_no_light",
        fallback_text="shared endpoint unavailable",
        details={
            "player_name": "合成試験",
            "biome": "synthetic_cave",
            "time_phase": "night",
            "craftable": False,
            "local_light": 3,
        },
        route="chat",
    )

    with (
        patch("dogido_server.llm.client.DogidoLLM._ensure_model") as ensure_model,
        patch(
            "dogido_server.llm.providers.httpx.Client",
            side_effect=RuntimeError("synthetic shared endpoint failure"),
        ),
    ):
        assert router.preload() is False
        assert router.generate_leaf_text(request) == "shared endpoint unavailable"

    ensure_model.assert_not_called()


def test_service_shutdown_does_not_send_shared_endpoint_lifecycle_requests(
    tmp_path: Path,
) -> None:
    settings = _shared_settings(tmp_path)
    with (
        patch("dogido_server.llm.providers.httpx.Client") as http_client,
        patch("dogido_server.llm.client.DogidoLLM._ensure_model") as ensure_model,
    ):
        service = DogidoService(settings)
        service.shutdown()

    http_client.assert_not_called()
    ensure_model.assert_not_called()
