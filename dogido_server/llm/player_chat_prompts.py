"""player_chat 用プロンプト組み立て。

方針の執行は reply_stance / reply_policy（状態機械）に寄せ、
ここは短い骨格＋口調用の薄い user 文にする。
"""

from __future__ import annotations

from typing import Any

from .character_mode import CharacterMode, character_mode_for_request
from .prompt_common import as_str_list, detail_str, leaf_dialog, player_name
from .types import LeafGenerationRequest


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
    history_rules, history_block = _history_section(details)
    digest_rules, digest_block = _digest_section(details)
    combat_safety_rules = _combat_safety_rules(details, character_mode)

    # system 側に character_mode トーンがあるので user では mode_hint を重ねない
    user_prompt = (
        "参考傾向:\n"
        "- プレイヤーへの相棒としての返事。実況口調や定型あいさつにしない\n"
        "- わからんことは『わからん』でよい。音声の誤認識っぽい文は軽く聞き返してよい\n"
        f"- 【答え方】{policy}\n"
        f"{inventory_rules}"
        f"{history_rules}"
        f"{digest_rules}"
        f"{combat_safety_rules}"
        "\n"
        "/no_think\n"
        "本番:\n"
        f"{history_block}"
        f"{digest_block}"
        f"プレイヤーが話しかけてきた:「{user_text}」\n"
        f"プレイヤーの呼び名は{player_name(details)}。"
        "自然なら一度だけ呼び名を入れてよい。\n"
        f"場所メモ: {place}。\n"
        f"時間帯は{detail_str(details, 'time_phase', 'unknown') or 'unknown'}。\n"
        f"答え方スタンス: {stance}。\n"
        f"周囲の脅威メモ: {threat_summary}。\n"
        f"{topic_block}"
        f"{plausibility_block}"
        f"{hearing_block}"
        f"{inventory_block}"
        "発言に噛み合った返事を、会話っぽく12〜42文字くらいで一言だけ返す。"
    )
    return leaf_dialog("player_chat", request, user_prompt)


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
    """戦闘安全は短く。tactics は観測（nearby_hostile_types）があるときだけ。"""
    rules = (
        "- プレイヤーを死なせる誤アドバイスは禁止。"
        "位置・種類の警告と短い応援はよいが、根拠の薄い作戦指示は控える\n"
    )
    nearby = as_str_list(details.get("nearby_hostile_types"))
    in_hostile = (
        character_mode == "battle"
        or details.get("has_visual_threats")
        or details.get("combat_active")
        or bool(nearby)
    )
    if in_hostile:
        rules += "- 敵対中は『じっとして』『止まって』等の静止指示は禁止\n"
    # tactics は SM が観測種だけ入れたときのみ（トピック仮説だけでは載せない）
    if not nearby:
        return rules
    tactics_notes = as_str_list(details.get("mob_tactics_notes"))
    forbidden_advice = as_str_list(details.get("forbidden_advice"))
    safe_hints = as_str_list(details.get("safe_hints"))
    if tactics_notes:
        joined = " / ".join(tactics_notes[:3])
        rules += f"- 周囲の敵の性質メモ: {joined}\n"
    if forbidden_advice:
        joined = "」「".join(forbidden_advice[:8])
        rules += f"- 追加の禁止助言: 「{joined}」\n"
    if safe_hints:
        joined = " / ".join(safe_hints[:5])
        rules += f"- 言ってよい短いヒント例: {joined}\n"
    return rules
