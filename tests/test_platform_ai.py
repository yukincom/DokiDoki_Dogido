from __future__ import annotations

import unittest
from types import SimpleNamespace
import threading
import time
from unittest.mock import patch

from dogido_server.config import Settings
from dogido_server.llm.client import STRUCTURED_STATUS_KEY
from dogido_server.llm.types import StructuredGenerationRequest
from dogido_server.platform_ai import (
    FoundryLocalProvider,
    PLATFORM_AI_PROVIDER_KEY,
    PlatformAIProbe,
    PlatformStructuredAIRouter,
    ProviderBusyError,
    _json_schema_for,
)


class _FoundryClient:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(temperature=None, max_tokens=None)
        self.calls = 0

    def complete_chat(self, messages):  # type: ignore[no-untyped-def]
        self.calls += 1
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=(
                            '{"intent":"critique_gibberish","confidence":0.94,'
                            '"repair_requested":false,"findings":[]}'
                        )
                    )
                )
            ]
        )


class _FoundryModel:
    def __init__(self, model_id: str, *, cached: bool) -> None:
        self.id = model_id
        self.is_cached = cached
        self.client = _FoundryClient()
        self.download_calls = 0
        self.load_calls = 0
        self.unload_calls = 0

    def download(self) -> None:
        self.download_calls += 1
        self.is_cached = True

    def load(self) -> None:
        self.load_calls += 1

    def unload(self) -> None:
        self.unload_calls += 1

    def get_chat_client(self) -> _FoundryClient:
        return self.client


class _FoundryCatalog:
    def __init__(self, model: _FoundryModel) -> None:
        self.model = model

    def get_model(self, alias: str) -> _FoundryModel:
        del alias
        return self.model


class _FoundryManager:
    def __init__(self, model: _FoundryModel) -> None:
        self.catalog = _FoundryCatalog(model)


class _Provider:
    def __init__(self, *, name: str = "test_os", fail: bool = False, busy: bool = False) -> None:
        self.name = name
        self.fail = fail
        self.busy = busy
        self.calls = 0

    def probe(self) -> PlatformAIProbe:
        return PlatformAIProbe(self.name, True, f"{self.name}:v1")

    def generate(self, request: StructuredGenerationRequest) -> dict[str, object]:
        self.calls += 1
        if self.busy:
            raise ProviderBusyError("still working")
        if self.fail:
            raise RuntimeError("provider failed")
        return {"intent": "critique_gibberish", "confidence": 0.94}


class _Fallback:
    def __init__(self) -> None:
        self.calls = 0

    def preload(self) -> bool:
        return True

    def generate_leaf_text(self, request):  # type: ignore[no-untyped-def]
        return request.fallback_text

    def generate_structured_json(self, request):  # type: ignore[no-untyped-def]
        self.calls += 1
        return {"intent": "soft_default", "confidence": 0.0}


def _request() -> StructuredGenerationRequest:
    return StructuredGenerationRequest(
        kind="haiku_workshop_intent",
        fallback_value={"intent": "soft_default", "confidence": 0.0},
        details={
            "verse": "そらまぶし くさむらにうかぶ くろいせきたん",
            "materials_speech": "平原",
            "player_text": "日本語としておかしい",
            "allowed_intents": ["critique_gibberish", "soft_default"],
            "allowed_problem_types": ["unreadable", "other"],
        },
        temperature=0.0,
        route="chat",
        max_tokens=64,
    )


class PlatformAIRouterTests(unittest.TestCase):
    def test_platform_provider_is_used_before_chat_fallback(self) -> None:
        router = PlatformStructuredAIRouter(
            Settings(platform_ai_provider="apple", platform_ai_refresh_sec=300)
        )
        provider = _Provider()
        router._providers["apple"] = provider  # type: ignore[assignment]
        fallback = _Fallback()

        payload = router.generate_structured_json(_request(), fallback=fallback)

        self.assertEqual(payload["intent"], "critique_gibberish")
        self.assertEqual(payload[STRUCTURED_STATUS_KEY], "accepted")
        self.assertEqual(payload[PLATFORM_AI_PROVIDER_KEY], "test_os")
        self.assertEqual(provider.calls, 1)
        self.assertEqual(fallback.calls, 0)

    def test_provider_failure_falls_back_to_configured_chat_model(self) -> None:
        router = PlatformStructuredAIRouter(
            Settings(
                platform_ai_provider="apple",
                platform_ai_refresh_sec=300,
                platform_ai_failure_cooldown_sec=60,
            )
        )
        provider = _Provider(fail=True)
        router._providers["apple"] = provider  # type: ignore[assignment]
        fallback = _Fallback()

        payload = router.generate_structured_json(_request(), fallback=fallback)

        self.assertEqual(payload["intent"], "soft_default")
        self.assertEqual(payload[PLATFORM_AI_PROVIDER_KEY], "chat_fallback")
        self.assertEqual(provider.calls, 1)
        self.assertEqual(fallback.calls, 1)

    def test_chat_configuration_skips_platform_probe(self) -> None:
        router = PlatformStructuredAIRouter(Settings(platform_ai_provider="chat"))
        provider = _Provider()
        router._providers["apple"] = provider  # type: ignore[assignment]
        fallback = _Fallback()

        payload = router.generate_structured_json(_request(), fallback=fallback)

        self.assertEqual(payload[PLATFORM_AI_PROVIDER_KEY], "chat_fallback")
        self.assertEqual(provider.calls, 0)
        self.assertEqual(fallback.calls, 1)

    def test_auto_tries_foundry_after_apple_generation_failure(self) -> None:
        router = PlatformStructuredAIRouter(
            Settings(platform_ai_provider="auto", platform_ai_failure_cooldown_sec=60)
        )
        apple = _Provider(name="apple_foundation_models", fail=True)
        foundry = _Provider(name="foundry_local")
        router._providers = {"apple": apple, "foundry": foundry}  # type: ignore[assignment]
        fallback = _Fallback()

        with patch("dogido_server.platform_ai.platform.system", return_value="Darwin"):
            payload = router.generate_structured_json(_request(), fallback=fallback)

        self.assertEqual(payload[PLATFORM_AI_PROVIDER_KEY], "foundry_local")
        self.assertEqual((apple.calls, foundry.calls, fallback.calls), (1, 1, 0))

    def test_busy_provider_falls_through_without_failure_cooldown(self) -> None:
        router = PlatformStructuredAIRouter(
            Settings(platform_ai_provider="auto", platform_ai_failure_cooldown_sec=60)
        )
        apple = _Provider(name="apple_foundation_models", busy=True)
        foundry = _Provider(name="foundry_local")
        router._providers = {"apple": apple, "foundry": foundry}  # type: ignore[assignment]

        with patch("dogido_server.platform_ai.platform.system", return_value="Darwin"):
            payload = router.generate_structured_json(_request(), fallback=_Fallback())

        self.assertEqual(payload[PLATFORM_AI_PROVIDER_KEY], "foundry_local")
        self.assertNotIn("apple_foundation_models", router._failed_until)

    def test_apple_json_schema_has_required_generation_order(self) -> None:
        schema = _json_schema_for(_request())

        self.assertEqual(
            schema["x-order"],
            ["intent", "confidence", "repair_requested", "findings", "line_proposal"],
        )
        finding_schema = schema["properties"]["findings"]["items"]
        self.assertIn("x-order", finding_schema)
        self.assertNotIn("line_index", finding_schema["required"])
        proposal_schema = schema["properties"]["line_proposal"]
        self.assertIn("x-order", proposal_schema)
        self.assertNotIn("line_index", proposal_schema["required"])

    def test_pending_decision_has_a_separate_closed_schema(self) -> None:
        request = StructuredGenerationRequest(
            kind="haiku_workshop_pending_decision",
            fallback_value={"action": "uncertain", "confidence": 0.0, "evidence": ""},
            details={
                "current_verse": "はるのかぜ\nひつじがあるく\nよるのつき",
                "pending_verse": "はるのかぜ\nあめつよくふる\nよるのつき",
                "player_text": "よし、それで完成にしよう",
                "allowed_actions": ["accept_pending", "uncertain"],
            },
            temperature=0.0,
            route="chat",
            max_tokens=96,
        )

        schema = _json_schema_for(request)

        self.assertEqual(schema["x-order"], ["action", "confidence", "evidence"])
        self.assertEqual(
            schema["properties"]["action"]["enum"],
            ["accept_pending", "uncertain"],
        )

    def test_provider_is_reprobed_when_failure_cooldown_expires_before_refresh(self) -> None:
        router = PlatformStructuredAIRouter(
            Settings(platform_ai_provider="apple", platform_ai_refresh_sec=300)
        )
        provider = _Provider()
        router._providers["apple"] = provider  # type: ignore[assignment]
        router._active = None
        router._last_probe_at = 100.0
        router._failed_until = {provider.name: 101.0}

        with patch("dogido_server.platform_ai.time.monotonic", return_value=102.0):
            selected = router._select_provider()

        self.assertIs(selected, provider)


class FoundryLocalProviderTests(unittest.TestCase):
    def _provider_with_model(
        self,
        model: _FoundryModel,
        *,
        allow_download: bool = False,
    ) -> FoundryLocalProvider:
        provider = FoundryLocalProvider(
            model_alias="qwen2.5-7b",
            allow_download=allow_download,
            timeout_sec=1.0,
        )
        provider._manager = _FoundryManager(model)
        return provider

    def test_uncached_model_is_not_available_when_download_is_disabled(self) -> None:
        model = _FoundryModel("variant-v1", cached=False)
        provider = self._provider_with_model(model)
        try:
            with patch("dogido_server.platform_ai._package_version", return_value="1.0.0"):
                probe = provider.probe()

            self.assertFalse(probe.available)
            self.assertEqual(probe.reason, "model_not_cached")
            self.assertEqual(model.download_calls, 0)
        finally:
            provider.close()

    def test_uncached_catalog_update_keeps_current_cached_variant(self) -> None:
        current = _FoundryModel("variant-v1", cached=True)
        fresh = _FoundryModel("variant-v2", cached=False)
        provider = self._provider_with_model(current)
        provider._model = current
        provider._variant_fingerprint = provider._model_fingerprint(current)
        provider._manager.catalog.model = fresh
        try:
            with patch("dogido_server.platform_ai._package_version", return_value="1.0.0"):
                probe = provider.probe()

            self.assertTrue(probe.available)
            self.assertIn("update_pending_not_cached", probe.reason)
            self.assertIs(provider._model, current)
            self.assertIsNone(provider._pending_model)
            self.assertEqual(current.unload_calls, 0)
            self.assertEqual(fresh.download_calls, 0)
        finally:
            provider.close()

    def test_cached_catalog_update_is_staged_and_swapped_on_generation(self) -> None:
        current = _FoundryModel("variant-v1", cached=True)
        fresh = _FoundryModel("variant-v2", cached=True)
        provider = self._provider_with_model(current)
        provider._model = current
        provider._client = current.client
        provider._variant_fingerprint = provider._model_fingerprint(current)
        provider._manager.catalog.model = fresh
        try:
            with patch("dogido_server.platform_ai._package_version", return_value="1.0.0"):
                probe = provider.probe()
            payload = provider.generate(_request())

            self.assertTrue(probe.available)
            self.assertIn("update_ready", probe.reason)
            self.assertEqual(payload["intent"], "critique_gibberish")
            self.assertIs(provider._model, fresh)
            self.assertEqual(fresh.load_calls, 1)
            self.assertEqual(current.unload_calls, 1)
            self.assertEqual(fresh.client.settings.temperature, 0.0)
            self.assertEqual(fresh.client.settings.max_tokens, 64)
        finally:
            provider.close()

    def test_catalog_reversion_clears_previously_staged_update(self) -> None:
        current = _FoundryModel("variant-v1", cached=True)
        fresh = _FoundryModel("variant-v2", cached=True)
        provider = self._provider_with_model(current)
        provider._model = current
        provider._variant_fingerprint = provider._model_fingerprint(current)
        provider._manager.catalog.model = fresh
        try:
            with patch("dogido_server.platform_ai._package_version", return_value="1.0.0"):
                provider.probe()
                self.assertIs(provider._pending_model, fresh)
                provider._manager.catalog.model = current
                provider.probe()

            self.assertIsNone(provider._pending_model)
            self.assertIs(provider._model, current)
        finally:
            provider.close()

    def test_close_does_not_wait_for_inflight_native_completion(self) -> None:
        model = _FoundryModel("variant-v1", cached=True)
        started = threading.Event()
        release = threading.Event()
        original_complete = model.client.complete_chat

        def blocking_complete(messages):  # type: ignore[no-untyped-def]
            started.set()
            release.wait(timeout=2.0)
            return original_complete(messages)

        model.client.complete_chat = blocking_complete  # type: ignore[method-assign]
        provider = self._provider_with_model(model)
        errors: list[Exception] = []

        def generate() -> None:
            try:
                provider.generate(_request())
            except Exception as exc:  # pragma: no cover - assertion below reports it
                errors.append(exc)

        worker = threading.Thread(target=generate)
        worker.start()
        self.assertTrue(started.wait(timeout=1.0))
        before = time.monotonic()
        provider.close()
        elapsed = time.monotonic() - before
        release.set()
        worker.join(timeout=1.0)

        self.assertLess(elapsed, 0.2)
        self.assertEqual(errors, [])
        self.assertEqual(model.unload_calls, 1)

    def test_close_racing_with_submit_still_unloads_model(self) -> None:
        model = _FoundryModel("variant-v1", cached=True)
        provider = self._provider_with_model(model)
        provider._model = model
        provider._client = model.client

        def close_then_fail(*_args, **_kwargs):  # type: ignore[no-untyped-def]
            provider.close()
            raise RuntimeError("executor already closed")

        with patch.object(provider._executor, "submit", side_effect=close_then_fail):
            with self.assertRaisesRegex(RuntimeError, "executor already closed"):
                provider.generate(_request())

        self.assertFalse(provider._busy.locked())
        self.assertEqual(model.unload_calls, 1)


if __name__ == "__main__":
    unittest.main()
