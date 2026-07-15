"""葉プロンプト共通ヘルパ。"""

from __future__ import annotations

from typing import Any

from .character_mode import CharacterMode, character_mode_for_request, system_prompt_for_mode
from .types import LeafGenerationRequest


def as_str_list(value: object | None) -> list[str]:
    """details の list/str 混在フィールドを文字列リストに正規化。"""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    return [str(item).strip() for item in value if str(item).strip()]


def detail_str(details: dict[str, Any], key: str, default: str = "") -> str:
    return str(details.get(key, default) or default).strip() or default


def player_name(details: dict[str, Any], default: str = "プレイヤー") -> str:
    return detail_str(details, "player_name", default) or default


def player_call_line(
    details: dict[str, Any],
    *,
    once: bool = True,
    allow_omit: bool = False,
) -> str:
    """『プレイヤーの呼び名は…』の定型1行。"""
    name = player_name(details)
    if allow_omit:
        usage = "自然なら呼び名は入れなくてよい。"
    elif once:
        usage = "自然なら一度だけその呼び名を入れてよい。"
    else:
        usage = "呼び名は基本入れない。"
    return f"プレイヤーの呼び名は{name}。{usage}\n"


def place_time_lines(
    details: dict[str, Any],
    *,
    place_key: str = "biome",
    place_default: str = "そのへん",
    place_label: str = "場所",
) -> str:
    place = detail_str(details, place_key, place_default) or place_default
    time_phase = detail_str(details, "time_phase", "unknown") or "unknown"
    return f"{place_label}は{place}。\n時間帯は{time_phase}。\n"


def join_hostiles(details: dict[str, Any], *, empty: str = "敵") -> str:
    hostiles = details.get("hostiles") or []
    if isinstance(hostiles, str):
        return hostiles.strip() or empty
    joined = "、".join(str(item) for item in hostiles if str(item).strip())
    return joined or empty


def dialog_messages(
    user_prompt: str,
    *,
    character_mode: CharacterMode = "peace",
    kind: str | None = None,
    details: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    mode = character_mode
    if details is not None and kind is not None:
        mode = character_mode_for_request(kind, details)
    elif details is not None and "character_mode" in details:
        mode = character_mode_for_request(kind or "", details)
    return [
        {"role": "system", "content": system_prompt_for_mode(mode)},
        {"role": "user", "content": user_prompt},
    ]


def leaf_dialog(kind: str, request: LeafGenerationRequest, user_prompt: str) -> list[dict[str, str]]:
    return dialog_messages(user_prompt, kind=kind, details=dict(request.details or {}))
