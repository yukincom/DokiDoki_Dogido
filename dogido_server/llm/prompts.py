# llm/prompts.py
"""葉プロンプトの公開 facade。

実装はモジュール分割:
  - character_mode: モード解決・system プロンプト
  - prompt_common: dialog 共通ヘルパ
  - player_chat_prompts: player_chat
  - reaction_prompts: その他反応系
  - haiku_prompts: 川柳系
"""

from __future__ import annotations

from typing import Any

from .character_mode import (
    BASE_IDENTITY_PROMPT,
    BATTLE_TONE_PROMPT,
    PEACE_TONE_PROMPT,
    SYSTEM_PROMPT,
    TENSION_TONE_PROMPT,
    CharacterMode,
    character_mode_for_request,
    normalize_character_mode,
    resolve_character_mode_from_state,
    system_prompt_for_mode,
)
from .haiku_prompts import (
    build_haiku_irony_messages,
    build_haiku_messages,
    build_haiku_repair_messages,
    build_haiku_scene_messages,
)
from .player_chat_prompts import build_player_chat_messages
from .prompt_common import dialog_messages, leaf_dialog
from .reaction_prompts import (
    _build_aftermath_messages,
    _build_ambient_messages,
    _build_dark_push_after_breath_messages,
    _build_dark_push_no_light_messages,
    _build_darkness_escape_messages,
    _build_daylight_water_skeleton_messages,
    _build_death_messages,
    _build_deep_dark_ominous_sound_messages,
    _build_emergency_shelter_relief_messages,
    _build_ender_eye_throw_messages,
    _build_hostile_callout_messages,
    _build_light_crafted_messages,
    _build_newly_burning_visual_messages,
    _build_occluded_entry_no_light_messages,
    _build_occluded_entry_with_light_messages,
    _build_occluded_hostile_presence_messages,
    _build_portal_appearance_messages,
    _build_structure_entry_messages,
    _build_weather_transition_messages,
)

# 後方互換: 旧コードが _dialog_messages / _leaf_dialog を参照しても動くように
_dialog_messages = dialog_messages
_leaf_dialog = leaf_dialog


def build_messages(request: Any) -> list[dict[str, str]]:
    builders = {
        "haiku": _build_haiku_messages,
        "haiku_repair": _build_haiku_repair_messages,
        "haiku_irony": _build_haiku_irony_messages,
        "haiku_scene": _build_haiku_scene_messages,
        "aftermath": _build_aftermath_messages,
        "ambient": _build_ambient_messages,
        "death": _build_death_messages,
        "hostile_callout": _build_hostile_callout_messages,
        "occluded_hostile_presence": _build_occluded_hostile_presence_messages,
        "darkness_escape": _build_darkness_escape_messages,
        "occluded_entry_with_light": _build_occluded_entry_with_light_messages,
        "occluded_entry_no_light": _build_occluded_entry_no_light_messages,
        "dark_push_no_light": _build_dark_push_no_light_messages,
        "dark_push_after_breath": _build_dark_push_after_breath_messages,
        "emergency_shelter_relief": _build_emergency_shelter_relief_messages,
        "light_crafted": _build_light_crafted_messages,
        "daylight_water_skeleton": _build_daylight_water_skeleton_messages,
        "newly_burning_visual": _build_newly_burning_visual_messages,
        "weather_transition": _build_weather_transition_messages,
        "deep_dark_ominous_sound": _build_deep_dark_ominous_sound_messages,
        "structure_entry": _build_structure_entry_messages,
        "ender_eye_throw": _build_ender_eye_throw_messages,
        "portal_appearance": _build_portal_appearance_messages,
        "player_chat": build_player_chat_messages,
    }
    builder = builders.get(request.kind)
    if builder is None:
        return []
    return builder(request)


def _build_haiku_messages(request: Any) -> list[dict[str, str]]:
    return build_haiku_messages(request.details)


def _build_haiku_repair_messages(request: Any) -> list[dict[str, str]]:
    return build_haiku_repair_messages(request.details)


def _build_haiku_irony_messages(request: Any) -> list[dict[str, str]]:
    return build_haiku_irony_messages(request.details)


def _build_haiku_scene_messages(request: Any) -> list[dict[str, str]]:
    return build_haiku_scene_messages(request.details)


__all__ = [
    "BASE_IDENTITY_PROMPT",
    "BATTLE_TONE_PROMPT",
    "PEACE_TONE_PROMPT",
    "SYSTEM_PROMPT",
    "TENSION_TONE_PROMPT",
    "CharacterMode",
    "build_messages",
    "character_mode_for_request",
    "normalize_character_mode",
    "resolve_character_mode_from_state",
    "system_prompt_for_mode",
]
