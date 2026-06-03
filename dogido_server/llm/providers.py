# llm/providers.py
from __future__ import annotations

from typing import Any

import httpx

from dogido_server.config import Settings


def generate_chat_completions_text(
    settings: Settings,
    messages: list[dict[str, str]],
    *,
    temperature: float,
) -> str:
    resolved_base_url = settings.llm_resolved_base_url
    if not resolved_base_url:
        raise RuntimeError(f"llm_base_url is not configured for provider={settings.llm_provider}")
    if not settings.llm_model:
        raise RuntimeError("llm_model is not configured")

    payload: dict[str, Any] = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": settings.llm_max_tokens,
        "stream": False,
    }
    if settings.llm_provider == "local":
        payload["chat_template_kwargs"] = {"enable_thinking": False}

    with httpx.Client(
        base_url=resolved_base_url.rstrip("/") + "/",
        timeout=settings.llm_timeout_sec,
        headers=settings.llm_request_headers() or None,
    ) as client:
        response = client.post("chat/completions", json=payload)
        response.raise_for_status()
        body = response.json()

    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError("chat_completions response has no choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        content = "\n".join(part for part in parts if part)
    if (not isinstance(content, str) or not content.strip()) and isinstance(message.get("reasoning"), str):
        content = message.get("reasoning")
    if (not isinstance(content, str) or not content.strip()) and isinstance(message.get("reasoning_content"), str):
        content = message.get("reasoning_content")
    if not isinstance(content, str) or not content.strip():
        content = choices[0].get("text")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("chat_completions response content is empty")
    return content


def generate_anthropic_text(
    settings: Settings,
    messages: list[dict[str, str]],
    *,
    temperature: float,
) -> str:
    resolved_base_url = settings.llm_resolved_base_url
    if not resolved_base_url:
        raise RuntimeError("anthropic base_url is not configured")
    if not settings.llm_model:
        raise RuntimeError("llm_model is not configured")

    system_parts: list[str] = []
    anthropic_messages: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        content = str(message.get("content", ""))
        if role in {"system", "developer"}:
            system_parts.append(content)
            continue
        anthropic_role = "assistant" if role == "assistant" else "user"
        anthropic_messages.append(
            {
                "role": anthropic_role,
                "content": [{"type": "text", "text": content}],
            }
        )

    payload: dict[str, Any] = {
        "model": settings.llm_model,
        "max_tokens": settings.llm_max_tokens,
        "temperature": temperature,
        "messages": anthropic_messages,
    }
    if system_parts:
        payload["system"] = "\n\n".join(system_parts)

    with httpx.Client(
        base_url=resolved_base_url.rstrip("/") + "/",
        timeout=settings.llm_timeout_sec,
        headers=settings.llm_request_headers() or None,
    ) as client:
        response = client.post("messages", json=payload)
        response.raise_for_status()
        body = response.json()

    content = body.get("content") or []
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
    text = "\n".join(part for part in parts if part).strip()
    if not text:
        raise RuntimeError("anthropic response content is empty")
    return text


def generate_gemini_text(
    settings: Settings,
    messages: list[dict[str, str]],
    *,
    temperature: float,
) -> str:
    resolved_base_url = settings.llm_resolved_base_url
    if not resolved_base_url:
        raise RuntimeError("gemini base_url is not configured")
    if not settings.llm_model:
        raise RuntimeError("llm_model is not configured")

    system_parts: list[dict[str, str]] = []
    contents: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        content = str(message.get("content", ""))
        if role in {"system", "developer"}:
            system_parts.append({"text": content})
            continue
        gemini_role = "model" if role == "assistant" else "user"
        contents.append({"role": gemini_role, "parts": [{"text": content}]})

    payload: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": settings.llm_max_tokens,
        },
    }
    if system_parts:
        payload["systemInstruction"] = {"parts": system_parts}

    endpoint = f"models/{settings.llm_model}:generateContent"
    with httpx.Client(
        base_url=resolved_base_url.rstrip("/") + "/",
        timeout=settings.llm_timeout_sec,
        headers=settings.llm_request_headers() or None,
    ) as client:
        response = client.post(endpoint, json=payload)
        response.raise_for_status()
        body = response.json()

    candidates = body.get("candidates") or []
    if not candidates:
        raise RuntimeError("gemini response has no candidates")
    content = (candidates[0].get("content") or {}).get("parts") or []
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("text"):
            parts.append(str(item.get("text", "")))
    text = "\n".join(part for part in parts if part).strip()
    if not text:
        raise RuntimeError("gemini response content is empty")
    return text
