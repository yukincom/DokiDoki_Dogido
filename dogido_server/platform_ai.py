"""OS 管理・端末内モデルを、小さな structured task へ使う共通入口。

優先順位と fallback はコードで固定し、各モデルには状態変更を任せない。
Apple は OS の ``SystemLanguageModel``、Windows / Linux は任意導入の
Microsoft Foundry Local を同じ契約で扱う。どちらも使えなければ呼び出し元が
渡した既存 chat LLM へ戻る。
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
import importlib
from importlib import metadata
import json
import logging
import platform
import threading
import time
from typing import Any, Protocol

from dogido_server.config import Settings
from dogido_server.llm.client import STRUCTURED_STATUS_KEY
from dogido_server.llm.prompts import build_messages
from dogido_server.llm.types import LLMFrontend, StructuredGenerationRequest

LOGGER = logging.getLogger("uvicorn.error")

PLATFORM_AI_PROVIDER_KEY = "__dogido_platform_ai_provider"


class ProviderBusyError(RuntimeError):
    """端末モデルは正常だが、直前の推論がまだ終わっていない。"""


@dataclass(frozen=True, slots=True)
class PlatformAIProbe:
    provider: str
    available: bool
    fingerprint: str
    reason: str = ""


class _StructuredProvider(Protocol):
    name: str

    def probe(self) -> PlatformAIProbe:
        ...

    def generate(self, request: StructuredGenerationRequest) -> dict[str, Any]:
        ...

    def close(self) -> None:
        ...


def _package_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "not-installed"


def _os_fingerprint() -> str:
    return ":".join(
        (
            platform.system() or "unknown",
            platform.release() or "unknown",
            platform.version() or "unknown",
            platform.machine() or "unknown",
        )
    )


def _reason_text(value: object | None) -> str:
    if value is None:
        return ""
    name = getattr(value, "name", None)
    return str(name or value)


def _json_schema_for(request: StructuredGenerationRequest) -> dict[str, object]:
    """端末内 guided generation 用 schema。

    現在 OS AI に渡すのは workshop 講評抽出だけ。未知 kind は閉じて fallback
    させ、汎用エージェント経路へ育てない。
    """

    if request.kind == "haiku_workshop_intent":
        allowed = [str(value) for value in request.details.get("allowed_intents", []) if value]
        if not allowed:
            allowed = ["soft_default"]
        problem_types = [
            str(value) for value in request.details.get("allowed_problem_types", []) if value
        ] or ["other"]
        return {
            "title": "DogidoWorkshopAnalysis",
            "type": "object",
            # Foundation Models の raw JSON Schema は、生成順を明示する
            # ``x-order`` を object ごとに要求する。標準 JSON Schema の
            # properties 順へ暗黙依存しない。
            "x-order": ["intent", "confidence", "repair_requested", "findings"],
            "additionalProperties": False,
            "required": ["intent", "confidence", "repair_requested", "findings"],
            "properties": {
                "intent": {"type": "string", "enum": allowed},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "repair_requested": {"type": "boolean"},
                "findings": {
                    "type": "array",
                    "maxItems": 3,
                    "items": {
                        "title": "DogidoWorkshopFinding",
                        "type": "object",
                        "x-order": [
                            "line_index",
                            "fragment",
                            "problem",
                            "note",
                            "confidence",
                        ],
                        "additionalProperties": False,
                        "required": [
                            "fragment",
                            "problem",
                            "note",
                            "confidence",
                        ],
                        "properties": {
                            "line_index": {
                                "type": "integer",
                                "minimum": 0,
                                "maximum": 2,
                            },
                            "fragment": {"type": "string"},
                            "problem": {"type": "string", "enum": problem_types},
                            "note": {"type": "string"},
                            "confidence": {
                                "type": "number",
                                "minimum": 0.0,
                                "maximum": 1.0,
                            },
                        },
                    },
                },
            },
        }
    raise ValueError(f"unsupported platform AI task: {request.kind}")


def _prompt_parts(request: StructuredGenerationRequest) -> tuple[str, str]:
    messages = build_messages(request)
    if not messages:
        raise ValueError(f"empty platform AI prompt: {request.kind}")
    system = "\n".join(
        str(message.get("content") or "")
        for message in messages
        if message.get("role") == "system"
    ).strip()
    prompt = "\n".join(
        str(message.get("content") or "")
        for message in messages
        if message.get("role") != "system"
    ).strip()
    if not prompt:
        raise ValueError(f"empty platform AI user prompt: {request.kind}")
    return system, prompt


class AppleFoundationModelsProvider:
    """Apple Intelligence の現在の既定オンデバイスモデルを使う。

    モデル名を保存しないことが更新追随の要点。毎回新しい
    ``SystemLanguageModel`` を取得するので、OS が既定モデルを更新したあとも
    アプリ側のモデル ID 書き換えなしで追随する。
    """

    name = "apple_foundation_models"

    def __init__(self, *, timeout_sec: float) -> None:
        self.timeout_sec = max(0.5, float(timeout_sec))
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dogido-apple-fm")
        self._busy = threading.Lock()

    def probe(self) -> PlatformAIProbe:
        base = f"{_os_fingerprint()}:apple-fm-sdk={_package_version('apple-fm-sdk')}"
        if platform.system() != "Darwin":
            return PlatformAIProbe(self.name, False, base, "not_macos")
        try:
            fm = importlib.import_module("apple_fm_sdk")
            model = fm.SystemLanguageModel()
            available, reason = model.is_available()
        except Exception as exc:  # optional SDK / Xcode / assets
            return PlatformAIProbe(self.name, False, base, str(exc))
        reason_text = _reason_text(reason)
        return PlatformAIProbe(
            self.name,
            bool(available),
            f"{base}:availability={available}:{reason_text or 'ready'}",
            reason_text,
        )

    def generate(self, request: StructuredGenerationRequest) -> dict[str, Any]:
        if not self._busy.acquire(blocking=False):
            raise ProviderBusyError("Apple Foundation Models is still handling the previous request")
        try:
            future = self._executor.submit(self._generate_and_release, request)
        except Exception:
            self._busy.release()
            raise
        try:
            return future.result(timeout=self.timeout_sec + 0.5)
        except FutureTimeoutError as exc:
            future.cancel()
            raise TimeoutError(f"Apple Foundation Models timeout ({self.timeout_sec:.1f}s)") from exc

    def _generate_and_release(self, request: StructuredGenerationRequest) -> dict[str, Any]:
        try:
            return self._generate_in_thread(request)
        finally:
            self._busy.release()

    def _generate_in_thread(self, request: StructuredGenerationRequest) -> dict[str, Any]:
        # FastAPI の event loop 上から呼ばれるため、Apple の async API は専用 thread
        # 内の短命 loop で実行する。モデル実行自体は同時に一件だけ。
        return asyncio.run(asyncio.wait_for(self._generate_async(request), timeout=self.timeout_sec))

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def _generate_async(self, request: StructuredGenerationRequest) -> dict[str, Any]:
        fm = importlib.import_module("apple_fm_sdk")
        model = fm.SystemLanguageModel()
        available, reason = model.is_available()
        if not available:
            raise RuntimeError(f"Apple Foundation Models unavailable: {_reason_text(reason)}")
        instructions, prompt = _prompt_parts(request)
        session = fm.LanguageModelSession(model=model, instructions=instructions or None)
        options = fm.GenerationOptions(
            temperature=float(request.temperature),
            maximum_response_tokens=int(request.max_tokens or 96),
        )
        response = await session.respond(
            prompt=prompt,
            json_schema=_json_schema_for(request),
            options=options,
        )
        if isinstance(response, dict):
            return dict(response)
        if hasattr(response, "to_json"):
            payload = json.loads(response.to_json())
            if isinstance(payload, dict):
                return payload
        if hasattr(response, "value"):
            payload = response.value(dict)
            if isinstance(payload, dict):
                return dict(payload)
        raise ValueError("Apple Foundation Models returned no JSON object")


class FoundryLocalProvider:
    """Microsoft Foundry Local の alias を使う cross-platform provider。

    alias は特定の量子化・実行デバイスを固定しない。SDK が更新された catalog と
    端末能力から CPU / GPU / NPU 向け variant を選ぶ。
    """

    name = "foundry_local"
    _init_lock = threading.Lock()
    _shared_manager: Any | None = None

    def __init__(self, *, model_alias: str, allow_download: bool, timeout_sec: float) -> None:
        self.model_alias = model_alias.strip() or "qwen2.5-7b"
        self.allow_download = bool(allow_download)
        self.timeout_sec = max(0.5, float(timeout_sec))
        self._manager: Any | None = None
        self._model: Any | None = None
        self._client: Any | None = None
        self._variant_fingerprint = ""
        self._pending_model: Any | None = None
        self._runtime_lock = threading.RLock()
        self._busy = threading.Lock()
        self._closing = threading.Event()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dogido-foundry")

    def probe(self) -> PlatformAIProbe:
        version = _package_version("foundry-local-sdk-winml")
        if version == "not-installed":
            version = _package_version("foundry-local-sdk")
        base = f"{_os_fingerprint()}:foundry={version}:alias={self.model_alias}"
        if version == "not-installed":
            return PlatformAIProbe(self.name, False, base, "sdk_not_installed")
        try:
            with self._runtime_lock:
                model, reason = self._refresh_catalog_model()
                variant = self._model_fingerprint(model)
        except Exception as exc:
            return PlatformAIProbe(self.name, False, base, str(exc))
        available = self._client is not None or self.allow_download or self._is_cached(model)
        return PlatformAIProbe(
            self.name,
            available,
            f"{base}:variant={variant}",
            reason if available else "model_not_cached",
        )

    def generate(self, request: StructuredGenerationRequest) -> dict[str, Any]:
        if self._closing.is_set():
            raise RuntimeError("Foundry Local provider is closed")
        if not self._busy.acquire(blocking=False):
            raise ProviderBusyError("Foundry Local is still handling the previous request")
        try:
            future = self._executor.submit(self._generate_and_release, request)
        except Exception:
            self._busy.release()
            # close が busy 確認後にexecutorを閉じた競合では callback が作られない。
            # submit失敗側で、実行中でないモデルを確実にunloadする。
            if self._closing.is_set():
                self._unload_current_model()
            raise
        future.add_done_callback(self._finish_close_after_inference)
        try:
            return future.result(timeout=self.timeout_sec)
        except FutureTimeoutError as exc:
            future.cancel()
            raise TimeoutError(f"Foundry Local timeout ({self.timeout_sec:.1f}s)") from exc

    def _generate_and_release(self, request: StructuredGenerationRequest) -> dict[str, Any]:
        try:
            return self._generate_sync(request)
        finally:
            self._busy.release()

    def _generate_sync(self, request: StructuredGenerationRequest) -> dict[str, Any]:
        with self._runtime_lock:
            self._activate_pending_model()
            model = self._get_model()
            if self._client is None:
                self._load_if_allowed(model)
                self._client = model.get_chat_client()
            settings = getattr(self._client, "settings", None)
            if settings is not None:
                if hasattr(settings, "temperature"):
                    settings.temperature = float(request.temperature)
                if hasattr(settings, "max_tokens"):
                    settings.max_tokens = int(request.max_tokens or 96)
            system, prompt = _prompt_parts(request)
            messages: list[dict[str, str]] = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            # Foundry Local の native client は OpenAI 互換応答を返す。guided JSON の
            # API 差異を避け、既存 structured parser と同じ JSON-only prompt を使う。
            response = self._client.complete_chat(messages)
            choices = getattr(response, "choices", None) or []
            if not choices:
                raise ValueError("Foundry Local returned no choices")
            message = getattr(choices[0], "message", None)
            text = getattr(message, "content", None)
            if not isinstance(text, str):
                raise ValueError("Foundry Local returned no text")
            payload = _extract_json_object(text)
            if payload is None:
                raise ValueError("Foundry Local returned invalid JSON")
            return payload

    def _get_model(self) -> Any:
        if self._manager is None:
            module = importlib.import_module("foundry_local_sdk")
            with self._init_lock:
                if self.__class__._shared_manager is None:
                    config = module.Configuration(app_name="DokiDoki-Dogido")
                    module.FoundryLocalManager.initialize(config)
                    self.__class__._shared_manager = module.FoundryLocalManager.instance
                self._manager = self.__class__._shared_manager
        if self._model is None:
            self._model = self._manager.catalog.get_model(self.model_alias)
            self._variant_fingerprint = self._model_fingerprint(self._model)
        return self._model

    def _refresh_catalog_model(self) -> tuple[Any, str]:
        """alias の現在の解決先を取り直し、variant 更新へ追随する。

        catalog の更新頻度は SDK 側へ任せる。Dogido は定期 probe のたびに
        alias を再解決し、ID / version / variant が変わったときだけ client を
        捨てる。ダウンロード許可は別設定のままなので、更新検知が勝手な大容量
        download にはならない。
        """

        self._get_model()
        fresh = self._manager.catalog.get_model(self.model_alias)
        fresh_fingerprint = self._model_fingerprint(fresh)
        if fresh_fingerprint != self._variant_fingerprint:
            if self.allow_download or self._is_cached(fresh):
                self._pending_model = fresh
                return self._model, f"update_ready:{fresh_fingerprint}"
            # 前回見つけた更新候補がcatalogから撤回・置換された場合に、古い
            # pendingへ後から切り替えない。
            self._pending_model = None
            return self._model, f"update_pending_not_cached:{fresh_fingerprint}"
        self._pending_model = None
        return self._model, ""

    def _activate_pending_model(self) -> None:
        fresh = self._pending_model
        if fresh is None:
            return
        old_model = self._model
        old_client = self._client
        try:
            self._load_if_allowed(fresh)
            fresh_client = fresh.get_chat_client()
        except Exception:
            unload = getattr(fresh, "unload", None)
            if callable(unload):
                try:
                    unload()
                except Exception:
                    pass
            raise
        self._model = fresh
        self._client = fresh_client
        self._variant_fingerprint = self._model_fingerprint(fresh)
        self._pending_model = None
        if old_model is not None and old_model is not fresh:
            unload = getattr(old_model, "unload", None)
            if callable(unload):
                try:
                    unload()
                except Exception:
                    pass
        del old_client

    def _load_if_allowed(self, model: Any) -> None:
        if not self._is_cached(model):
            if not self.allow_download:
                raise RuntimeError("Foundry Local model is not cached")
            model.download()
        model.load()

    @staticmethod
    def _is_cached(model: Any) -> bool:
        value = getattr(model, "is_cached", False)
        if callable(value):
            value = value()
        return bool(value)

    def close(self) -> None:
        self._closing.set()
        self._executor.shutdown(wait=False, cancel_futures=True)
        # native inference は thread から強制停止できない。実行中ならここでは
        # 待たず、完了callbackが同じロックで安全にunloadする。
        if self._busy.locked():
            return
        self._unload_current_model()

    def _finish_close_after_inference(self, _future: object) -> None:
        if self._closing.is_set():
            self._unload_current_model()

    def _unload_current_model(self) -> None:
        with self._runtime_lock:
            model = self._model
            self._model = None
            self._client = None
            self._pending_model = None
            self._variant_fingerprint = ""
            unload = getattr(model, "unload", None)
            if callable(unload):
                try:
                    unload()
                except Exception:
                    pass

    @staticmethod
    def _model_fingerprint(model: Any) -> str:
        values = [
            getattr(model, "id", ""),
            getattr(model, "model_id", ""),
            getattr(model, "variant_id", ""),
            getattr(model, "version", ""),
        ]
        fingerprint = ":".join(str(value) for value in values if value)
        return fingerprint or "unknown"


def _extract_json_object(text: str) -> dict[str, Any] | None:
    source = (text or "").strip()
    if source.startswith("```"):
        source = source.strip("`").strip()
        if source.lower().startswith("json"):
            source = source[4:].lstrip()
    try:
        payload = json.loads(source)
    except json.JSONDecodeError:
        start = source.find("{")
        end = source.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            payload = json.loads(source[start : end + 1])
        except json.JSONDecodeError:
            return None
    return payload if isinstance(payload, dict) else None


class PlatformStructuredAIRouter:
    """利用可能な端末内 structured provider を定期再発見する。

    provider の選択・fallback は deterministic。生成結果から provider を選ばない。
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._active: _StructuredProvider | None = None
        self._last_probe_at = 0.0
        self._last_probe: PlatformAIProbe | None = None
        self._failed_until: dict[str, float] = {}
        self._providers: dict[str, _StructuredProvider] = {
            "apple": AppleFoundationModelsProvider(timeout_sec=settings.platform_ai_timeout_sec),
            "foundry": FoundryLocalProvider(
                model_alias=settings.platform_ai_foundry_model_alias,
                allow_download=settings.platform_ai_allow_model_download,
                timeout_sec=settings.platform_ai_timeout_sec,
            ),
        }

    def preload(self) -> bool:
        return self._select_provider(force=True) is not None

    def generate_structured_json(
        self,
        request: StructuredGenerationRequest,
        *,
        fallback: LLMFrontend,
    ) -> dict[str, Any]:
        provider = self._select_provider()
        attempted: set[str] = set()
        while provider is not None and provider.name not in attempted:
            attempted.add(provider.name)
            try:
                payload = provider.generate(request)
                if isinstance(payload, dict):
                    result = dict(payload)
                    result[STRUCTURED_STATUS_KEY] = "accepted"
                    result[PLATFORM_AI_PROVIDER_KEY] = provider.name
                    LOGGER.warning(
                        "platform_ai kind=%s provider=%s result=accepted",
                        request.kind,
                        provider.name,
                    )
                    return result
                raise ValueError("platform AI payload is not an object")
            except ProviderBusyError as exc:
                # 正常な推論中というだけなので、provider障害のcooldownには入れない。
                LOGGER.warning(
                    "platform_ai kind=%s provider=%s result=busy_fallback detail=%s",
                    request.kind,
                    provider.name,
                    exc,
                )
                provider = self._select_provider(force=True, exclude=attempted)
                continue
            except Exception as exc:  # optional provider must never break workshop
                self._failed_until[provider.name] = time.monotonic() + max(
                    1.0, self.settings.platform_ai_failure_cooldown_sec
                )
                LOGGER.warning(
                    "platform_ai kind=%s provider=%s result=fallback detail=%s",
                    request.kind,
                    provider.name,
                    exc,
                )
                provider = self._select_provider(force=True, exclude=attempted)
                continue

        result = dict(fallback.generate_structured_json(request))
        result.setdefault(PLATFORM_AI_PROVIDER_KEY, "chat_fallback")
        return result

    def close(self) -> None:
        for provider in self._providers.values():
            close = getattr(provider, "close", None)
            if callable(close):
                close()

    def active_probe(self) -> PlatformAIProbe | None:
        self._select_provider()
        return self._last_probe

    def _select_provider(
        self,
        *,
        force: bool = False,
        exclude: set[str] | None = None,
    ) -> _StructuredProvider | None:
        excluded = exclude or set()
        configured = self.settings.platform_ai_provider
        if configured == "chat":
            self._active = None
            self._last_probe = PlatformAIProbe("chat_fallback", False, _os_fingerprint(), "configured")
            return None

        now = time.monotonic()
        refresh = max(1.0, self.settings.platform_ai_refresh_sec)
        if not force and now - self._last_probe_at < refresh:
            if self._active is None:
                retry_times = [value for value in self._failed_until.values() if value > 0.0]
                if not retry_times or now < min(retry_times):
                    return None
            if (
                self._active is not None
                and self._active.name not in excluded
                and self._failed_until.get(self._active.name, 0.0) <= now
            ):
                return self._active

        with self._lock:
            self._last_probe_at = now
            names = self._provider_order(configured)
            previous = self._last_probe
            self._active = None
            selected_probe: PlatformAIProbe | None = None
            for name in names:
                provider = self._providers[name]
                if provider.name in excluded:
                    continue
                probe = provider.probe()
                selected_probe = probe
                if probe.available and self._failed_until.get(provider.name, 0.0) <= now:
                    self._active = provider
                    break
            self._last_probe = selected_probe
            if previous is None or (
                selected_probe is not None
                and (previous.provider, previous.fingerprint, previous.available)
                != (selected_probe.provider, selected_probe.fingerprint, selected_probe.available)
            ):
                LOGGER.warning(
                    "platform_ai_discovery selected=%s available=%s fingerprint=%s reason=%s",
                    self._active.name if self._active is not None else "chat_fallback",
                    bool(self._active),
                    selected_probe.fingerprint if selected_probe else _os_fingerprint(),
                    selected_probe.reason if selected_probe else "no_provider",
                )
            return self._active

    def _provider_order(self, configured: str) -> tuple[str, ...]:
        if configured in self._providers:
            return (configured,)
        # Apple の OS 管理モデルを最優先。Windows/Linuxには同じ契約の
        # Foundry Localを使う。macOSでもApple SDKが無ければFoundryへ進める。
        if platform.system() == "Darwin":
            return ("apple", "foundry")
        return ("foundry",)
