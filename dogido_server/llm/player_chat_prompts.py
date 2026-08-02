"""player_chat 用プロンプト組み立て。

役割分担:
  - spirit / character_mode: 相棒像・口調
  - reply_policy: スタンスごとの答え方（肯定ガイド）
  - 【材料】節: 事実・ヒントの提示（ここが根拠）
  - 種名範囲・危険助言の執行: sanitize / 白リスト（プロンプトに禁止を再掲しない）
"""

from __future__ import annotations

from typing import Any

from .character_mode import CharacterMode, character_mode_for_request
from .prompt_common import as_str_list, detail_str, leaf_dialog, player_name
from .types import LeafGenerationRequest


def _dogido_chat_spirit() -> str:
    """相棒像だけ。スタンス別の答え方は reply_policy に任せる。"""
    return (
        "あなたはドギド。怖がりだけどやさしい関西の相棒や。\n"
        "一人称はオレ。\n"
        "プレイヤーの一言に短く乗る。自分が主役の実況にはしない。\n"
        "セリフだけ返す。"
    )


def build_player_chat_messages(request: LeafGenerationRequest) -> list[dict[str, str]]:
    details = dict(request.details or {})
    character_mode = character_mode_for_request("player_chat", details)
    user_text = detail_str(details, "user_text") or "（聞き取れなかった）"
    place = _resolve_place_line(details)
    threat_summary = detail_str(details, "threat_summary") or "とくになし"
    stance = detail_str(details, "reply_stance", "none") or "none"
    policy = detail_str(details, "reply_policy")
    if not policy:
        from dogido_server.player_chat_policy import reply_policy_line

        policy = reply_policy_line(stance)

    inventory_rules, inventory_block = _inventory_section(details)
    hearing_block = _hearing_block(details)
    topic_block = _topic_block(details)
    plausibility_block = _plausibility_block(details)
    observation_block = _observation_block(details, threat_summary)
    history_rules, history_block = _history_section(details)
    digest_rules, digest_block = _digest_section(details)
    combat_safety_rules = _combat_safety_rules(details, character_mode)

    user_prompt = (
        f"{_dogido_chat_spirit()}\n"
        "\n"
        f"いまの答え方: {policy}\n"
        f"{inventory_rules}"
        f"{history_rules}"
        f"{digest_rules}"
        f"{combat_safety_rules}"
        "\n"
        "/no_think\n"
        "【材料】\n"
        f"{history_block}"
        f"{digest_block}"
        f"プレイヤー:「{user_text}」\n"
        f"呼び名: {player_name(details)}（自然なら一度だけ）\n"
        f"場所: {place}\n"
        f"時間: {detail_str(details, 'time_phase', 'unknown') or 'unknown'}\n"
        f"{_weather_block(details)}"
        f"スタンス: {stance}\n"
        f"{_haiku_workshop_block(details)}"
        f"{observation_block}"
        f"{topic_block}"
        f"{plausibility_block}"
        f"{hearing_block}"
        f"{inventory_block}"
        "\n"
        "プレイヤーの言葉に噛み合った一言だけ（12〜42字くらい）。"
    )
    return leaf_dialog("player_chat", request, user_prompt)


def _weather_block(details: dict[str, Any]) -> str:
    """天気は world 状態の事実。hearing とは別レイヤ。"""
    label = detail_str(details, "weather_label") or detail_str(details, "weather", "不明") or "不明"
    fact = detail_str(details, "weather_fact")
    lines = [
        f"天気は{label}"
        "（ワールドの天気状態。雨の話の根拠。モブ音メモとは別）。"
    ]
    if fact:
        lines.append(f"天気の事実: {fact}")
    return "\n".join(lines) + "\n"


def _haiku_workshop_block(details: dict[str, Any]) -> str:
    if not detail_str(details, "haiku_workshop_open"):
        return ""
    verse = detail_str(details, "haiku_workshop_text")
    materials = detail_str(details, "haiku_workshop_materials")
    lines = [
        "【いまの句（ワークショップ中）】",
        "句の話なら、言葉・響き・字数に乗ってよい。",
    ]
    if verse:
        lines.append(f"句: {verse}")
    if materials:
        lines.append(f"材料: {materials}")
    return "\n".join(lines) + "\n"


def _resolve_place_line(details: dict[str, Any]) -> str:
    place_context = detail_str(details, "place_context")
    if place_context:
        return place_context
    structure_label = detail_str(details, "structure_label")
    if structure_label:
        return structure_label
    return detail_str(details, "biome", "そのへん") or "そのへん"


def _inventory_section(details: dict[str, Any]) -> tuple[str, str]:
    inventory_summary = detail_str(details, "inventory_summary")
    held_item_label = detail_str(details, "held_item_label")
    asks_inventory = bool(details.get("asks_inventory")) and bool(inventory_summary)
    if not asks_inventory:
        # リスト未提示時は節ごと省略（材料が無い）
        return "", ""
    block = (
        f"手持ち: {held_item_label or 'なし'}。\n"
        f"所持品（インベントリ要約）: {inventory_summary}。\n"
    )
    rules = "- 所持品は与えられた要約を根拠に、関係しそうな物を短く触れてよい\n"
    return rules, block


def _hearing_block(details: dict[str, Any]) -> str:
    """音メモがあるときだけ載せる。種名の範囲は白リスト側。"""
    hearing_summary = detail_str(details, "hearing_summary")
    hearing_named_mobs = as_str_list(details.get("hearing_named_mobs"))
    if not hearing_summary and not hearing_named_mobs:
        return ""
    named_line = "、".join(hearing_named_mobs) if hearing_named_mobs else "（なし）"
    summary_line = hearing_summary or "（なし）"
    return (
        f"いまドギドが拾っている音のメモ: {summary_line}。\n"
        f"音から触れてよい具体モブ名: {named_line}。\n"
    )


def _observation_block(details: dict[str, Any], threat_summary: str) -> str:
    """観測事実のみ。hypothesis 用 topic は別節。"""
    observation = detail_str(details, "observation_summary")
    look = detail_str(details, "look_target_label")
    parts: list[str] = []
    if observation:
        parts.append(f"観測メモ（短い事実）:\n{observation}")
    elif threat_summary and threat_summary != "とくになし":
        # 後方互換: observation 未設定時は threat だけ
        parts.append(f"周囲の脅威メモ: {threat_summary}。")
    # 指差し時だけ（呼び出し側が look_target_label を空にしている）
    if look and (not observation or "視線先" not in observation):
        parts.append(
            f"指差し（クロスヘア）: {look}。"
            "『これ何』系のときはこれを材料にしてよい。"
        )
    if not parts:
        return ""
    return "\n".join(parts) + "\n"


def _topic_block(details: dict[str, Any]) -> str:
    catalog_topic_hints = detail_str(details, "catalog_topic_hints")
    if not catalog_topic_hints:
        return ""
    return (
        "カタログからの話題ヒント（弱く触れてよい候補）:\n"
        f"{catalog_topic_hints}\n"
    )


def _plausibility_block(details: dict[str, Any]) -> str:
    """F′: SM が計算した structure×biome 行。雰囲気の参考メモ。"""
    hints = detail_str(details, "plausibility_hints")
    if not hints:
        return ""
    return f"知識リンク（雰囲気の参考）:\n{hints}\n"


def _history_section(details: dict[str, Any]) -> tuple[str, str]:
    conversation_history = detail_str(details, "conversation_history")
    if not conversation_history:
        return "", ""
    block = f"【直近の会話】\n{conversation_history}\n"
    rules = "- 直近の会話の続きとして自然に乗ってよい\n"
    return rules, block


def _digest_section(details: dict[str, Any]) -> tuple[str, str]:
    event_digest = detail_str(details, "event_digest")
    if not event_digest:
        return "", ""
    block = f"【直近の出来事メモ】\n{event_digest}\n"
    rules = "- 出来事メモは粗い要約として続きに使ってよい\n"
    return rules, block


def _combat_safety_rules(details: dict[str, Any], character_mode: CharacterMode) -> str:
    """戦況時だけ短い共有トーン＋安全ヒント材料。禁止助言の執行は sanitize 側。

    平和雑談（none）では空。saw policy と重複する一般論は載せない。
    """
    nearby = as_str_list(details.get("nearby_hostile_types"))
    in_hostile = (
        character_mode == "battle"
        or details.get("has_visual_threats")
        or details.get("combat_active")
        or bool(nearby)
        or detail_str(details, "reply_stance") == "saw"
    )
    if not in_hostile:
        return ""
    lines = ["いまは戦況寄り。方向・種類を短く共有してよい。"]
    if not nearby:
        return lines[0] + "\n"
    tactics_notes = as_str_list(details.get("mob_tactics_notes"))
    # forbidden_advice は prompt に再掲せず details 経由で sanitize が検査する
    safe_hints = as_str_list(details.get("safe_hints"))
    if tactics_notes:
        joined = " / ".join(tactics_notes[:3])
        lines.append(f"敵の性質メモ: {joined}")
    if safe_hints:
        joined = " / ".join(safe_hints[:5])
        lines.append(f"短い安全ヒント: {joined}")
    return "\n".join(lines) + "\n"
