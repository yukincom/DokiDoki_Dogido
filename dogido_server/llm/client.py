# llm/client.py
from __future__ import annotations

import importlib
import json
import logging
import threading
import time
from dataclasses import replace
from typing import Any

from dogido_server.config import Settings

from .providers import (
    generate_anthropic_text,
    generate_chat_completions_text,
    generate_gemini_text,
)
from .prompts import build_messages
from .sanitize import (
    clean_output,
    has_excessive_repetition,
    has_kansai_marker,
    has_suffix_chain_noise,
    is_japanese_like_char,
    is_style_acceptable,
    is_usable_output,
    looks_japanese_forward,
    strip_allowed_ascii_tokens,
    summarize_for_log,
)
from .structured_contracts import (
    STRUCTURED_CONTRACT_RETRY_KEY,
    validate_structured_payload,
)
from .types import LeafGenerationRequest, StructuredGenerationRequest

LOGGER = logging.getLogger("uvicorn.error")
STRUCTURED_STATUS_KEY = "__dogido_status"

# 自動川柳は、各 consumer が内容・行番号・atom ID・音数を検証し、欠けた行だけ
# 最大6回の生成経路で直す。共通schemaで外形だけを理由に再生成・打切りしない。
_DOMAIN_VALIDATED_HAIKU_GENERATION_KINDS = frozenset(
    {
        "haiku_draft",
        "haiku_irony",
        "haiku_scene",
        "haiku_line_grounding",
        "haiku_line_regeneration",
    }
)


class DogidoLLM:
    """LLM バックエンドのフロントエンド。

    仕様方針: LLM には状態とイベントを注入して「発話テキストの生成」だけを担わせる。
    状態管理・優先制御はコード側（state_machine / service）が行う。

    対応バックエンド:
        - mlx: Apple Silicon 上でローカル実行（mlx-lm）
        - chat_completions / openai_compatible: Chat Completions API
          （LM Studio / llama.cpp / OpenAI / OpenRouter / xAI など）
        - anthropic_messages: Anthropic Messages API
        - gemini_generate_content: Gemini generateContent API
        - noop: 音声出力なし（テスト・CI 用）

    スレッド安全性:
        generate_leaf_text / preload は self._lock で排他制御している。
        複数スレッドから同時呼び出しされてもモデルの二重ロードは起きない。
        その代わり、生成リクエストも直列化される。
    """
    _shared_mlx_models: dict[str, tuple[Any, Any]] = {}
    _shared_mlx_lock = threading.Lock()

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._model: Any | None = None  # mlx バックエンド時のモデルオブジェクト
        self._tokenizer: Any | None = None  # mlx バックエンド時のトークナイザ
        self._load_attempted = False  # ロード失敗後に再試行しないためのフラグ
        self._disabled_reason: str | None = None  # 無効化されている理由（ログ・デバッグ用）

    def enabled(self) -> bool:
        """LLM が有効かどうかを返す。設定で llm_enabled=False か backend=noop なら False。"""
        return self.settings.llm_enabled and self.settings.llm_backend != "noop"

    def disabled_reason(self) -> str | None:
        """無効化されている理由文字列を返す。有効な場合は None。"""
        return self._disabled_reason

    def preload(self) -> bool:
        """起動時のウォームアップ。mlx の場合はモデルをメモリにロードする。

        HTTP API バックエンドはリクエスト時に接続するためスキップ。
        失敗しても例外を投げず False を返す（起動をブロックしない）。
        """
        if not self.enabled():
            return False

        with self._lock:
            try:
                if self.settings.llm_backend == "mlx":
                    model, tokenizer, reused = self._ensure_model()
                    ok = model is not None and tokenizer is not None
                    if ok:
                        LOGGER.warning(
                            "llm_preload backend=mlx result=%s model=%s",
                            "reused" if reused else "loaded",
                            self.settings.mlx_model_id,
                        )
                    else:
                        LOGGER.warning(
                            "llm_preload backend=mlx result=skipped reason=%s",
                            self._disabled_reason or "model unavailable",
                        )
                    return ok

                if self.settings.llm_uses_remote_api:
                    # API バックエンドはリクエスト時に接続するためプリロード不要
                    LOGGER.warning(
                        "llm_preload backend=%s provider=%s result=skipped base_url=%s",
                        self.settings.llm_effective_backend,
                        self.settings.llm_provider,
                        self.settings.llm_resolved_base_url or "unset",
                    )
                    return False
            except Exception as exc:
                self._disabled_reason = str(exc)
                LOGGER.warning(
                    "llm_preload backend=%s result=error detail=%s",
                    self.settings.llm_backend,
                    self._disabled_reason,
                )
                return False

        return False

    def generate_leaf_text(self, request: LeafGenerationRequest) -> str:
        """発話テキストを 1 件生成して返す。失敗時は fallback_text を返す。

        "leaf" はドギドの発話ツリーにおける末端ノード
        （実際に音声になるテキスト）を指す。

        処理フロー:
            1. バックエンドで生成
            2. 発話テキストをクリーニング
            3. 使用可否チェック
            4. スタイルチェック
            5. 全チェック通過で採用、失敗なら fallback_text を返す
        """
        if not self.enabled():
            LOGGER.warning("llm_leaf kind=%s result=fallback reason=disabled", request.kind)
            return request.fallback_text

        generation_started_at = time.monotonic()
        with self._lock:
            try:
                text = self._generate_backend_text(request)
            except Exception as exc:
                self._disabled_reason = str(exc)
                duration_ms = round((time.monotonic() - generation_started_at) * 1000)
                LOGGER.warning(
                    "llm_leaf kind=%s result=fallback reason=generation_error "
                    "duration_ms=%s detail=%s",
                    request.kind,
                    duration_ms,
                    self._disabled_reason,
                )
                return request.fallback_text
        duration_ms = round((time.monotonic() - generation_started_at) * 1000)

        # ロックはバックエンド呼び出しだけを保護する。
        # 後処理は純粋関数なのでロック外で行うが、将来ここで _model / _tokenizer に触るなら要見直し。
        cleaned = self._clean_output(text)
        if not self._is_usable_output(cleaned, request.details):
            LOGGER.warning(
                "llm_leaf kind=%s result=fallback reason=unusable_output "
                "duration_ms=%s raw=%s cleaned=%s",
                request.kind,
                duration_ms,
                self._summarize_for_log(text),
                self._summarize_for_log(cleaned),
            )
            return request.fallback_text
        if request.kind == "player_chat":
            # 観測済みの亜種を一般名にしてしまった場合は、白リスト判定より先に
            # カタログで許可された一意な正式名へ戻す。
            from dogido_server.player_chat_policy import rewrite_observed_speech_names

            cleaned, applied_name_corrections = rewrite_observed_speech_names(
                cleaned,
                request.details.get("speech_name_corrections"),
            )
            if applied_name_corrections:
                LOGGER.warning(
                    "llm_leaf kind=%s result=name_corrected corrections=%s text=%s",
                    request.kind,
                    ",".join(
                        f"{source}->{target}"
                        for source, target in applied_name_corrections
                    ),
                    self._summarize_for_log(cleaned),
                )
        if not self._is_style_acceptable(request.kind, cleaned, request.details):
            LOGGER.warning(
                "llm_leaf kind=%s result=fallback reason=style_mismatch "
                "duration_ms=%s cleaned=%s",
                request.kind,
                duration_ms,
                self._summarize_for_log(cleaned),
            )
            return request.fallback_text
        LOGGER.warning(
            "llm_leaf kind=%s result=accepted duration_ms=%s text=%s",
            request.kind,
            duration_ms,
            self._summarize_for_log(cleaned),
        )
        return cleaned or request.fallback_text

    def generate_structured_json(self, request: StructuredGenerationRequest) -> dict[str, Any]:
        """JSON オブジェクトを 1 件生成して返す。失敗時は fallback_value を返す。"""
        if not self.enabled():
            LOGGER.warning("llm_structured kind=%s result=fallback reason=disabled", request.kind)
            payload = dict(request.fallback_value)
            payload[STRUCTURED_STATUS_KEY] = "disabled"
            return payload

        try:
            with self._lock:
                text = self._generate_backend_text(request)
        except Exception as exc:
            self._disabled_reason = str(exc)
            LOGGER.warning(
                "llm_structured kind=%s result=fallback reason=generation_error detail=%s",
                request.kind,
                self._disabled_reason,
            )
            payload = dict(request.fallback_value)
            payload[STRUCTURED_STATUS_KEY] = "generation_error"
            return payload

        payload = self._extract_json_object(text)
        if payload is None:
            LOGGER.warning(
                "llm_structured kind=%s result=fallback reason=invalid_json raw=%s",
                request.kind,
                self._summarize_for_log(text),
            )
            payload = dict(request.fallback_value)
            payload[STRUCTURED_STATUS_KEY] = "invalid_json"
            return payload
        if request.kind in _DOMAIN_VALIDATED_HAIKU_GENERATION_KINDS:
            payload[STRUCTURED_STATUS_KEY] = "accepted"
            LOGGER.warning(
                "llm_structured kind=%s result=accepted validation=haiku_domain payload=%s",
                request.kind,
                self._summarize_for_log(json.dumps(payload, ensure_ascii=False)),
            )
            return payload
        contract = validate_structured_payload(
            request.kind,
            payload,
            details=request.details,
        )
        if not contract.accepted:
            LOGGER.warning(
                "llm_structured kind=%s result=retry reason=schema_contract_error "
                "errors=%s payload=%s",
                request.kind,
                contract.summary,
                self._summarize_for_log(json.dumps(payload, ensure_ascii=False)),
            )
            retry_details = dict(request.details)
            retry_details[STRUCTURED_CONTRACT_RETRY_KEY] = {
                "errors": list(contract.errors),
                "previous_payload": json.dumps(payload, ensure_ascii=False),
            }
            retry_request = replace(request, details=retry_details, temperature=0.0)
            try:
                with self._lock:
                    retry_text = self._generate_backend_text(retry_request)
            except Exception as exc:
                LOGGER.warning(
                    "llm_structured kind=%s result=fallback reason=schema_contract_error "
                    "retry=generation_error detail=%s",
                    request.kind,
                    exc,
                )
                return self._structured_fallback(request, "schema_contract_error")
            retry_payload = self._extract_json_object(retry_text)
            if retry_payload is None:
                LOGGER.warning(
                    "llm_structured kind=%s result=fallback reason=schema_contract_error "
                    "retry=invalid_json raw=%s",
                    request.kind,
                    self._summarize_for_log(retry_text),
                )
                return self._structured_fallback(request, "schema_contract_error")
            retry_contract = validate_structured_payload(
                request.kind,
                retry_payload,
                details=request.details,
            )
            if not retry_contract.accepted:
                LOGGER.warning(
                    "llm_structured kind=%s result=fallback reason=schema_contract_error "
                    "retry=schema_contract_error errors=%s payload=%s",
                    request.kind,
                    retry_contract.summary,
                    self._summarize_for_log(
                        json.dumps(retry_payload, ensure_ascii=False)
                    ),
                )
                return self._structured_fallback(request, "schema_contract_error")
            payload = retry_payload
            LOGGER.warning(
                "llm_structured kind=%s result=contract_retry_accepted",
                request.kind,
            )

        payload[STRUCTURED_STATUS_KEY] = "accepted"
        LOGGER.warning(
            "llm_structured kind=%s result=accepted payload=%s",
            request.kind,
            self._summarize_for_log(json.dumps(payload, ensure_ascii=False)),
        )
        return payload

    def _structured_fallback(
        self,
        request: StructuredGenerationRequest,
        status: str,
    ) -> dict[str, Any]:
        payload = dict(request.fallback_value)
        payload[STRUCTURED_STATUS_KEY] = status
        return payload

    def _generate_backend_text(self, request: LeafGenerationRequest | StructuredGenerationRequest) -> str:
        """バックエンドに応じてテキストを生成する内部メソッド。"""
        messages = self._build_messages(request)
        if not messages:
            raise ValueError("empty_prompt")
        settings = self._settings_for_request(request)

        if settings.llm_backend == "mlx":
            model, tokenizer, _ = self._ensure_model()
            if model is None or tokenizer is None:
                raise RuntimeError(self._disabled_reason or "mlx model unavailable")

            prompt = self._build_prompt(tokenizer, messages)
            if not prompt:
                raise ValueError("empty_prompt")

            mlx_lm = importlib.import_module("mlx_lm")
            sample_utils = importlib.import_module("mlx_lm.sample_utils")
            # temperature=0.0 はサンプラー不要（greedy decoding）
            sampler = None
            if request.temperature > 0.0:
                sampler = sample_utils.make_sampler(temp=request.temperature, top_p=0.92)
            return mlx_lm.generate(
                model,
                tokenizer,
                prompt,
                max_tokens=settings.llm_max_tokens,
                sampler=sampler,
                verbose=False,
            )

        if settings.llm_effective_backend == "chat_completions":
            return generate_chat_completions_text(
                settings,
                messages,
                temperature=request.temperature,
            )
        if settings.llm_effective_backend == "anthropic_messages":
            return generate_anthropic_text(
                settings,
                messages,
                temperature=request.temperature,
            )
        if settings.llm_effective_backend == "gemini_generate_content":
            return generate_gemini_text(
                settings,
                messages,
                temperature=request.temperature,
            )

        raise RuntimeError(f"unsupported llm_backend: {settings.llm_effective_backend}")

    def _ensure_model(self) -> tuple[Any | None, Any | None, bool]:
        """mlx モデルをロードして返す。ロード済みならキャッシュを返す。

        一度失敗したら _load_attempted=True にして再試行しない。
        ロード失敗はそのまま _disabled_reason に記録する。
        """
        if self._model is not None and self._tokenizer is not None:
            return self._model, self._tokenizer, True

        if self._load_attempted:
            # 失敗済みのため再試行しない
            return None, None, False

        self._load_attempted = True
        if not self.settings.mlx_model_id:
            self._disabled_reason = "mlx_model_id is not configured"
            return None, None, False

        try:
            with self._shared_mlx_lock:
                cached = self._shared_mlx_models.get(self.settings.mlx_model_id)
                if cached is not None:
                    self._model, self._tokenizer = cached
                    return self._model, self._tokenizer, True

                mlx_lm = importlib.import_module("mlx_lm")
                self._model, self._tokenizer = mlx_lm.load(self.settings.mlx_model_id)
                self._shared_mlx_models[self.settings.mlx_model_id] = (self._model, self._tokenizer)
                return self._model, self._tokenizer, False
        except Exception as exc:
            self._disabled_reason = str(exc)
            self._model = None
            self._tokenizer = None
            return None, None, False

    def _build_prompt(self, tokenizer: Any, messages: list[dict[str, str]]) -> str:
        """mlx 用プロンプト文字列を組み立てる。

        tokenizer.apply_chat_template が使えない場合は
        "role: content" の素朴な結合にフォールバックする。
        """
        try:
            return tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=False,  # 思考モード無効
            )
        except Exception:
            # chat_template 非対応トークナイザへのフォールバック
            return "\n".join(f"{message['role']}: {message['content']}" for message in messages)

    def _build_messages(self, request: LeafGenerationRequest | StructuredGenerationRequest) -> list[dict[str, str]]:
        """リクエストからチャット形式のメッセージリストを組み立てる。実装は prompts.py に委譲。"""
        return build_messages(request)

    def _settings_for_request(self, request: LeafGenerationRequest | StructuredGenerationRequest) -> Settings:
        if request.max_tokens is None or request.max_tokens <= 0:
            return self.settings
        return self.settings.model_copy(update={"llm_max_tokens": request.max_tokens})

    def _extract_json_object(self, text: str | None) -> dict[str, Any] | None:
        if not text:
            return None
        normalized = text.strip()
        if normalized.startswith("```"):
            normalized = self._strip_code_fence(normalized)
        parsed = self._parse_json_mapping(normalized)
        if parsed is not None:
            return parsed
        decoder = json.JSONDecoder()
        for index, char in enumerate(normalized):
            if char != "{":
                continue
            try:
                candidate, _ = decoder.raw_decode(normalized[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                return candidate
        return None

    def _strip_code_fence(self, text: str) -> str:
        stripped = text.strip()
        if not stripped.startswith("```"):
            return stripped
        lines = stripped.splitlines()
        if len(lines) >= 2 and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
        return stripped

    def _parse_json_mapping(self, text: str) -> dict[str, Any] | None:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
        if isinstance(payload, dict):
            return payload
        return None

    # ---- 以下は sanitize モジュールへの委譲メソッド ----
    # DogidoLLM 自身がロジックを持たず、モジュール関数をインスタンスメソッドとして
    # 呼び出せるようにしているだけ（テスト時にサブクラスでオーバーライドしやすくするため）

    def _clean_output(self, text: str | None) -> str:
        return clean_output(text)

    def _is_usable_output(self, text: str, details: dict[str, Any] | None = None) -> bool:
        return is_usable_output(text, details)

    def _strip_allowed_ascii_tokens(self, text: str, details: dict[str, Any]) -> str:
        return strip_allowed_ascii_tokens(text, details)

    def _looks_japanese_forward(self, text: str) -> bool:
        return looks_japanese_forward(text)

    def _is_style_acceptable(self, kind: str, text: str, details: dict[str, Any] | None = None) -> bool:
        return is_style_acceptable(kind, text, details)

    def _has_excessive_repetition(self, text: str) -> bool:
        return has_excessive_repetition(text)

    def _has_suffix_chain_noise(self, text: str) -> bool:
        return has_suffix_chain_noise(text)

    def _has_kansai_marker(self, text: str) -> bool:
        return has_kansai_marker(text)

    def _is_japanese_like_char(self, ch: str) -> bool:
        return is_japanese_like_char(ch)

    def _summarize_for_log(self, text: str | None) -> str:
        return summarize_for_log(text)
