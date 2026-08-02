"""player_chat 用プロンプト組み立て。

方針の執行は reply_stance / reply_policy（状態機械）に寄せ、
ここは短い骨格＋口調用の薄い user 文にする。
"""

from __future__ import annotations

from typing import Any

from .character_mode import CharacterMode, character_mode_for_request
from .prompt_common import as_str_list, detail_str, leaf_dialog, player_name
from .types import LeafGenerationRequest


def _dogido_chat_spirit() -> str:
    """禁止の羅列より、なりたい相棒像を先に置く。"""
    return (
        "あなたはドギド。怖がりだけどやさしい関西の相棒や。\n"
        "一人称はオレ。\n"
        "プレイヤーの一言に、短く乗る。自分が主役の実況にはしない。\n"
        "脅威メモがあれば、種類と方向を短く共有して、隣でびびる。\n"
        "逃げたいなら逃げ寄り。わからんなら素直に聞き返す。\n"
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
    """天気は状態。昼の雨・雷だけ短い事実（安全断定は details 側で持たない）。"""
    label = detail_str(details, "weather_label") or detail_str(details, "weather", "不明") or "不明"
    fact = detail_str(details, "weather_fact")
    lines = [
        f"天気は{label}"
        "（ワールドの天気状態。雨の話はここを根拠にする。"
        "hearing のモブ音と混同しない。晴れなのに雨を捏造しない）。"
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
        "プレイヤーが句の話をしていないときだけ、通常の雑談でよい。",
        "look や topic 仮説で句の添削をゲーム道具の話にすりかえない。",
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
        # リスト未提示時は節ごと省略（「断定するな」の長文規則も載せない）
        return "", ""
    block = (
        f"手持ち: {held_item_label or 'なし'}。\n"
        f"所持品（インベントリ要約）: {inventory_summary}。\n"
    )
    rules = (
        "- 所持品は与えられた要約だけを根拠にする。"
        "リストに無い物を『ある』と断定せず、関係しそうな物だけ短く触れる\n"
    )
    return rules, block


def _hearing_block(details: dict[str, Any]) -> str:
    """音メモがあるときだけ載せる（空行の常時2行は E′ で廃止。捏造防止は白リスト）。"""
    hearing_summary = detail_str(details, "hearing_summary")
    hearing_named_mobs = as_str_list(details.get("hearing_named_mobs"))
    if not hearing_summary and not hearing_named_mobs:
        return ""
    named_line = "、".join(hearing_named_mobs) if hearing_named_mobs else "（なし）"
    summary_line = hearing_summary or "（なし）"
    return (
        f"いまドギドが拾っている音のメモ: {summary_line}。\n"
        f"音から使ってよい具体モブ名: {named_line}。\n"
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
            "『これ何』系のときだけこれを根拠にする。"
        )
    if not parts:
        return ""
    return "\n".join(parts) + "\n"


def _topic_block(details: dict[str, Any]) -> str:
    catalog_topic_hints = detail_str(details, "catalog_topic_hints")
    if not catalog_topic_hints:
        return ""
    return (
        "カタログからの話題ヒント（断定材料ではない）:\n"
        f"{catalog_topic_hints}\n"
    )


def _plausibility_block(details: dict[str, Any]) -> str:
    """F′: SM が計算した structure×biome 行。推論ではなく事実メモ。"""
    hints = detail_str(details, "plausibility_hints")
    if not hints:
        return ""
    return (
        "知識リンク（断定ではない。生成しうる≠いま視界にある）:\n"
        f"{hints}\n"
    )


def _history_section(details: dict[str, Any]) -> tuple[str, str]:
    conversation_history = detail_str(details, "conversation_history")
    if not conversation_history:
        return "", ""
    block = f"【直近の会話】\n{conversation_history}\n"
    rules = "- 直近の会話の続きとして自然に。無理に蒸し返さない\n"
    return rules, block


def _digest_section(details: dict[str, Any]) -> tuple[str, str]:
    event_digest = detail_str(details, "event_digest")
    if not event_digest:
        return "", ""
    block = f"【直近の出来事メモ】\n{event_digest}\n"
    rules = "- 出来事メモは粗い要約。見えていないことは足さない\n"
    return rules, block


def _combat_safety_rules(details: dict[str, Any], character_mode: CharacterMode) -> str:
    """戦闘まわりは淡々と。tactics は観測種があるときだけ。"""
    rules = (
        "危ない助言はしない。"
        "種類と方向の共有が先。\n"
    )
    nearby = as_str_list(details.get("nearby_hostile_types"))
    in_hostile = (
        character_mode == "battle"
        or details.get("has_visual_threats")
        or details.get("combat_active")
        or bool(nearby)
    )
    if in_hostile:
        rules += "いまは落ち着いて動けそうな声で。静止しろ系は言わない。\n"
    if not nearby:
        return rules
    tactics_notes = as_str_list(details.get("mob_tactics_notes"))
    forbidden_advice = as_str_list(details.get("forbidden_advice"))
    safe_hints = as_str_list(details.get("safe_hints"))
    if tactics_notes:
        joined = " / ".join(tactics_notes[:3])
        rules += f"敵の性質メモ: {joined}\n"
    if forbidden_advice:
        joined = "」「".join(forbidden_advice[:8])
        rules += f"言わない方がよいこと: 「{joined}」\n"
    if safe_hints:
        joined = " / ".join(safe_hints[:5])
        rules += f"短い安全ヒント: {joined}\n"
    return rules
