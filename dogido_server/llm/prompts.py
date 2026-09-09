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

from dogido_server.language_dialogue.prompts import (
    build_participation_assessment_messages,
    build_participation_forecast_messages,
    build_web_consent_messages,
    build_research_intent_messages,
    build_research_reading_messages,
    build_grounded_reply_messages,
    build_interpretation_messages,
)

from .assist_prompts import build_select_sword_intent_messages
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
    build_haiku_draft_messages,
    build_haiku_irony_messages,
    build_haiku_line_grounding_messages,
    build_haiku_line_regeneration_messages,
    build_haiku_scene_messages,
)
from .player_chat_prompts import build_player_chat_messages
from .prompt_common import dialog_messages, leaf_dialog
from .workshop_prompts import (
    build_haiku_workshop_combat_input_messages,
    build_haiku_workshop_evaluation_messages,
    build_haiku_workshop_intent_messages,
    build_haiku_workshop_pending_decision_messages,
    build_haiku_workshop_reply_messages,
    build_haiku_workshop_revision_messages,
)
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
from .structured_contracts import (
    STRUCTURED_CONTRACT_RETRY_KEY,
    structured_contract_retry_instruction,
)

# 後方互換: 旧コードが _dialog_messages / _leaf_dialog を参照しても動くように
_dialog_messages = dialog_messages
_leaf_dialog = leaf_dialog


def build_messages(request: Any) -> list[dict[str, str]]:
    builders = {
        "language_dialogue_interpretation": build_interpretation_messages,
        "language_dialogue_reply": build_grounded_reply_messages,
        "language_participation_forecast": build_participation_forecast_messages,
        "language_participation_assessment": build_participation_assessment_messages,
        "language_research_intent": build_research_intent_messages,
        "language_research_reading": build_research_reading_messages,
        "language_web_consent": build_web_consent_messages,
        "haiku_draft": _build_haiku_draft_messages,
        "haiku_line_grounding": _build_haiku_line_grounding_messages,
        "haiku_line_regeneration": _build_haiku_line_regeneration_messages,
        "haiku_irony": _build_haiku_irony_messages,
        "haiku_scene": _build_haiku_scene_messages,
        "haiku_workshop_combat_input": _build_haiku_workshop_combat_input_messages,
        "haiku_workshop_evaluation": _build_haiku_workshop_evaluation_messages,
        "haiku_workshop_intent": _build_haiku_workshop_intent_messages,
        "haiku_workshop_pending_decision": _build_haiku_workshop_pending_decision_messages,
        "haiku_workshop_reply": _build_haiku_workshop_reply_messages,
        "haiku_workshop_revision": _build_haiku_workshop_revision_messages,
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
        "assist_select_sword_intent": _build_select_sword_intent_messages,
    }
    builder = builders.get(request.kind)
    if builder is None:
        return []
    messages = builder(request)
    retry = request.details.get(STRUCTURED_CONTRACT_RETRY_KEY)
    if isinstance(retry, dict):
        errors = retry.get("errors")
        error_text = "、".join(
            str(value) for value in errors if value
        ) if isinstance(errors, list) else "現行JSON契約との不一致"
        previous = str(retry.get("previous_payload") or "")[:2400]
        contract_instruction = structured_contract_retry_instruction(
            request.kind,
            details=request.details,
        )
        messages.append(
            {
                "role": "user",
                "content": (
                    "前回の返答は、内容の採否以前に現行JSON契約へ一致しなかった。\n"
                    f"契約不一致: {error_text}\n"
                    f"前回のJSON: {previous}\n"
                    f"{contract_instruction}\n"
                    "最初に示した同じ入力をもう一度判定し、プロンプトに記載された現行の形で、"
                    "JSONオブジェクトを1つだけ返す。旧形式、説明文、コードフェンスは禁止。"
                    "候補にないIDや値を補作しない。"
                ),
            }
        )
    return messages


def _build_haiku_draft_messages(request: Any) -> list[dict[str, str]]:
    return build_haiku_draft_messages(request.details)


def _build_select_sword_intent_messages(request: Any) -> list[dict[str, str]]:
    return build_select_sword_intent_messages(request.details)


def _build_haiku_line_grounding_messages(request: Any) -> list[dict[str, str]]:
    return build_haiku_line_grounding_messages(request.details)


def _build_haiku_line_regeneration_messages(request: Any) -> list[dict[str, str]]:
    return build_haiku_line_regeneration_messages(request.details)


def _build_haiku_irony_messages(request: Any) -> list[dict[str, str]]:
    return build_haiku_irony_messages(request.details)


def _build_haiku_scene_messages(request: Any) -> list[dict[str, str]]:
    return build_haiku_scene_messages(request.details)


def _build_haiku_workshop_intent_messages(request: Any) -> list[dict[str, str]]:
    return build_haiku_workshop_intent_messages(request.details)


def _build_haiku_workshop_evaluation_messages(request: Any) -> list[dict[str, str]]:
    return build_haiku_workshop_evaluation_messages(request.details)


def _build_haiku_workshop_combat_input_messages(request: Any) -> list[dict[str, str]]:
    return build_haiku_workshop_combat_input_messages(request.details)


def _build_haiku_workshop_pending_decision_messages(request: Any) -> list[dict[str, str]]:
    return build_haiku_workshop_pending_decision_messages(request.details)


def _build_haiku_workshop_reply_messages(request: Any) -> list[dict[str, str]]:
    return build_haiku_workshop_reply_messages(request)


def _build_haiku_workshop_revision_messages(request: Any) -> list[dict[str, str]]:
    return build_haiku_workshop_revision_messages(request.details)


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
