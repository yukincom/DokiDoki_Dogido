# llm/client.py
from __future__ import annotations

import importlib
import json
import logging
import threading
from typing import Any

from dogido_server.config import Settings

from .providers import (
    generate_anthropic_text,
    generate_chat_completions_text,
    generate_gemini_text,
)
from .haiku import (
    clean_haiku_output,
    count_japanese_sounds,
    haiku_char_sound,
    is_haiku_usable_output,
    split_haiku_phrases,
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
from .types import LeafGenerationRequest, StructuredGenerationRequest

LOGGER = logging.getLogger("uvicorn.error")
STRUCTURED_STATUS_KEY = "__dogido_status"


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
                    model, tokenizer = self._ensure_model()
                    ok = model is not None and tokenizer is not None
                    if ok:
                        LOGGER.warning("llm_preload backend=mlx result=loaded model=%s", self.settings.mlx_model_id)
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
            2. 種別（haiku / 通常）に応じたクリーニング
            3. 使用可否チェック
            4. スタイルチェック
            5. 全チェック通過で採用、失敗なら fallback_text を返す
        """
        if not self.enabled():
            LOGGER.warning("llm_leaf kind=%s result=fallback reason=disabled", request.kind)
            return request.fallback_text

        with self._lock:
            try:
                text = self._generate_backend_text(request)
            except Exception as exc:
                self._disabled_reason = str(exc)
                LOGGER.warning(
                    "llm_leaf kind=%s result=fallback reason=generation_error detail=%s",
                    request.kind,
                    self._disabled_reason,
                )
                return request.fallback_text

        # ロックはバックエンド呼び出しだけを保護する。
        # 後処理は純粋関数なのでロック外で行うが、将来ここで _model / _tokenizer に触るなら要見直し。
        # kind によってクリーニング・判定ロジックを切り替える
        cleaned = self._clean_haiku_output(text) if request.kind == "haiku" else self._clean_output(text)
        is_usable = self._is_haiku_usable_output(cleaned, request.details) if request.kind == "haiku" else self._is_usable_output(cleaned, request.details)
        if not is_usable:
            LOGGER.warning(
                "llm_leaf kind=%s result=fallback reason=unusable_output raw=%s cleaned=%s",
                request.kind,
                self._summarize_for_log(text),
                self._summarize_for_log(cleaned),
            )
            return request.fallback_text
        # haiku はスタイルチェック不要（音節数チェックを is_haiku_usable_output 側で完結させている）
        if request.kind != "haiku" and not self._is_style_acceptable(request.kind, cleaned):
            LOGGER.warning(
                "llm_leaf kind=%s result=fallback reason=style_mismatch cleaned=%s",
                request.kind,
                self._summarize_for_log(cleaned),
            )
            return request.fallback_text
        LOGGER.warning(
            "llm_leaf kind=%s result=accepted text=%s",
            request.kind,
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

        with self._lock:
            try:
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
        payload[STRUCTURED_STATUS_KEY] = "accepted"
        LOGGER.warning(
            "llm_structured kind=%s result=accepted payload=%s",
            request.kind,
            self._summarize_for_log(json.dumps(payload, ensure_ascii=False)),
        )
        return payload

    def _generate_backend_text(self, request: LeafGenerationRequest | StructuredGenerationRequest) -> str:
        """バックエンドに応じてテキストを生成する内部メソッド。"""
        messages = self._build_messages(request)
        if not messages:
            raise ValueError("empty_prompt")
        settings = self._settings_for_request(request)

        if settings.llm_backend == "mlx":
            model, tokenizer = self._ensure_model()
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

    def _ensure_model(self) -> tuple[Any | None, Any | None]:
        """mlx モデルをロードして返す。ロード済みならキャッシュを返す。

        一度失敗したら _load_attempted=True にして再試行しない。
        ロード失敗はそのまま _disabled_reason に記録する。
        """
        if self._model is not None and self._tokenizer is not None:
            return self._model, self._tokenizer

        if self._load_attempted:
            # 失敗済みのため再試行しない
            return None, None

        self._load_attempted = True
        if not self.settings.mlx_model_id:
            self._disabled_reason = "mlx_model_id is not configured"
            return None, None

        try:
            mlx_lm = importlib.import_module("mlx_lm")
            self._model, self._tokenizer = mlx_lm.load(self.settings.mlx_model_id)
            return self._model, self._tokenizer
        except Exception as exc:
            self._disabled_reason = str(exc)
            self._model = None
            self._tokenizer = None
            return None, None

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

    # ---- 以下は sanitize / haiku モジュールへの委譲メソッド ----
    # DogidoLLM 自身がロジックを持たず、モジュール関数をインスタンスメソッドとして
    # 呼び出せるようにしているだけ（テスト時にサブクラスでオーバーライドしやすくするため）
    # 一部は現時点で generate_leaf_text から直接は使っていないが、
    # 将来の差し替え点とテスト用フックとして残している。

    def _clean_output(self, text: str | None) -> str:
        return clean_output(text)

    def _clean_haiku_output(self, text: str | None) -> str:
        return clean_haiku_output(text)

    def _is_haiku_usable_output(self, text: str, details: dict[str, Any] | None = None) -> bool:
        return is_haiku_usable_output(text, details)

    def _split_haiku_phrases(self, text: str) -> list[str] | None:
        return split_haiku_phrases(text)

    def _count_japanese_sounds(self, text: str) -> int:
        return count_japanese_sounds(text)

    def _haiku_char_sound(self, ch: str, index: int) -> int:
        return haiku_char_sound(ch, index)

    def _is_usable_output(self, text: str, details: dict[str, Any] | None = None) -> bool:
        return is_usable_output(text, details)

    def _strip_allowed_ascii_tokens(self, text: str, details: dict[str, Any]) -> str:
        return strip_allowed_ascii_tokens(text, details)

    def _looks_japanese_forward(self, text: str) -> bool:
        return looks_japanese_forward(text)

    def _is_style_acceptable(self, kind: str, text: str) -> bool:
        return is_style_acceptable(kind, text)

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
