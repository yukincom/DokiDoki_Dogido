"""player_chat 用プロンプト組み立て。"""

from __future__ import annotations

from typing import Any

from .character_mode import CharacterMode, character_mode_for_request
from .prompt_common import as_str_list, detail_str, leaf_dialog, player_name
from .types import LeafGenerationRequest

_MODE_HINTS: dict[CharacterMode, str] = {
    "peace": (
        "平和時: 気さくで落ち着いて返す。"
        "怖がり連発や戦闘口調は禁止。普通の相棒の雑談として自然に。"
    ),
    "battle": (
        "バトル時: 短く狼狽えつつも応援する。"
        "いま危ないなら状況を一言混ぜてよいが、発言への返事は必ずする。"
        "諦めや非難は禁止。"
        "行動指示は安全な範囲だけ。静止指示は禁止。"
    ),
    "tension": (
        "緊張時: 用心は見せるが、わーきゃー応援にはしない。"
        "落ち着いて短く返す。"
    ),
}


def build_player_chat_messages(request: LeafGenerationRequest) -> list[dict[str, str]]:
    details = dict(request.details or {})
    character_mode = character_mode_for_request("player_chat", details)
    user_text = detail_str(details, "user_text") or "（聞き取れなかった）"
    place = _resolve_place_line(details)
    threat_summary = detail_str(details, "threat_summary") or "とくになし"

    inventory_rules, inventory_block = _inventory_section(details)
    hearing_rules, hearing_block = _hearing_section(details)
    topic_rules, topic_block = _topic_section(details)
    place_rules = _place_rules(details)
    history_rules, history_block = _history_section(details)
    digest_rules, digest_block = _digest_section(details)
    combat_safety_rules = _combat_safety_rules(details, character_mode)
    mode_hint = _MODE_HINTS[character_mode]

    user_prompt = (
        "参考傾向:\n"
        "- プレイヤーからの話しかけへの、相棒としての返事\n"
        "- 実況口調や定型あいさつにしない\n"
        "- 質問なら知っている範囲で正直に。わからんことは正直に『わからん』と言う\n"
        "- 音声認識やローマ字の打ち間違いっぽい文は、無理に解釈せず軽く聞き返してよい\n"
        f"{inventory_rules}"
        f"{hearing_rules}"
        f"{topic_rules}"
        f"{place_rules}"
        f"{history_rules}"
        f"{digest_rules}"
        f"{combat_safety_rules}"
        f"- {mode_hint}\n\n"
        "/no_think\n"
        "本番:\n"
        f"{history_block}"
        f"{digest_block}"
        f"プレイヤーが話しかけてきた:「{user_text}」\n"
        f"プレイヤーの呼び名は{player_name(details)}。"
        "自然なら一度だけ呼び名を入れてよい。\n"
        f"場所メモ: {place}。\n"
        f"時間帯は{detail_str(details, 'time_phase', 'unknown') or 'unknown'}。\n"
        f"キャラクターモードは{character_mode}。"
        f"状態機械モードは{detail_str(details, 'mode', 'normal') or 'normal'}。\n"
        f"周囲の脅威メモ: {threat_summary}。\n"
        f"{topic_block}"
        f"{hearing_block}"
        f"{inventory_block}"
        "発言の内容にちゃんと噛み合った返事を、会話っぽく12〜42文字くらいで一言だけ返す。"
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
    rules = (
        "- 所持品リストが与えられていないときは、インベントリの中身を断定しない。"
        "持っているか聞かれてデータが無ければ『いま手元の情報ではわからん』と正直に言う\n"
    )
    block = ""
    if asks_inventory:
        block = (
            f"手持ち: {held_item_label or 'なし'}。\n"
            f"所持品（インベントリ要約）: {inventory_summary}。\n"
        )
        rules = (
            "- 所持品の話題には、与えられた所持品要約だけを根拠にする。"
            "リストに無いアイテムを『ある』と断定しない。"
            "全部を読み上げず、質問に関係しそうな物だけ短く触れる\n"
        )
    return rules, block


def _hearing_section(details: dict[str, Any]) -> tuple[str, str]:
    hearing_summary = detail_str(details, "hearing_summary")
    hearing_named_mobs = as_str_list(details.get("hearing_named_mobs"))
    named_line = "、".join(hearing_named_mobs) if hearing_named_mobs else "（なし）"
    if hearing_summary:
        block = (
            f"いまドギドが拾っている音のメモ: {hearing_summary}。\n"
            f"音から使ってよい具体モブ名: {named_line}。\n"
        )
        rules = (
            "- 音・気配の話は、与えられた音メモだけを根拠にする。"
            "メモに無い方向・種類・距離の音を『聞こえた』と捏造しない\n"
            "- 音の正体の具体モブ名は『音から使ってよい具体モブ名』にあるものだけ。"
            "リストに無い種名（バイオームからの連想・野犬など）は禁止\n"
            "- メモが『種別未確定』だけのときは種名を当てず、"
            "『なんか声がする』『低い声っぽい』程度に留める\n"
        )
    else:
        block = (
            "いまドギドが拾っている音のメモ: （なし）。\n"
            "音から使ってよい具体モブ名: （なし）。\n"
        )
        rules = (
            "- 音のメモが空のとき、プレイヤーが音の話をしていても種名を当てない。"
            "『こっちでははっきり拾えてへん』『さっきは聞こえた気がするが今はわからん』など正直に。"
            "バイオーム名から動物を連想して補完しない\n"
        )
    return rules, block


def _topic_section(details: dict[str, Any]) -> tuple[str, str]:
    catalog_topic_hints = detail_str(details, "catalog_topic_hints")
    if catalog_topic_hints:
        block = (
            "カタログからの話題ヒント（断定材料ではない）:\n"
            f"{catalog_topic_hints}\n"
        )
        rules = (
            "- 話題ヒントはカタログ照合の候補。ヒントに無い種族名・NPC・ダンジョン住人を捏造しない\n"
            "- 脅威メモ（視認）に載っていないとき: 自分は『見えてへん』と偽らない。"
            "ただしプレイヤーの『いる／見えてる』を『おらへん』『気のせい』で否定して落とさない。"
            "『俺には見えんけど、それなら（候補）かもしれん』のように弱く触れてよい\n"
            "- 脅威メモに候補と一致する視認があるときは、候補名を通常どおり言ってよい\n"
            "- 複数候補なら決めつけず『どっちやろ』程度でよい\n"
        )
    else:
        block = ""
        rules = (
            "- 話題ヒントが空のとき、プレイヤーが『あれ何？』など特徴の薄い聞き方なら種名を当てず、"
            "特徴を聞き返してよい。カタログに無い名前を作らない\n"
            "- ドギドの視認が空でも、プレイヤーが誰か／何かを見ている話をしているなら"
            "『おらへん』で否定しない。見えてへんことと、プレイヤーの話を聞くことは両立する\n"
        )
    return rules, block


def _place_rules(details: dict[str, Any]) -> str:
    space_kind = detail_str(details, "space_kind")
    rules = (
        "- 場所メモは『地表バイオーム』と『空間（空が見えるか・地下っぽさ）』が別。"
        "バイオーム名だけ見て地上の散歩と決めつけない\n"
    )
    if space_kind in {"underground_or_roofed", "cave_biome", "underwater"}:
        rules += (
            "- いまは地下っぽい／洞窟／水中寄り。『野原を歩いてる』『空の下』のような地上オープンスペース扱いをしない。"
            "バイオームは気候・植生のタグとして軽く触れてよい\n"
        )
    elif space_kind == "canopy":
        rules += "- 木陰っぽい。真上は空でも葉で塞がれている前提でよい\n"
    elif space_kind == "open_surface":
        rules += "- 開けた地上（空が見える）。屋内・洞窟扱いにしない\n"
    return rules


def _history_section(details: dict[str, Any]) -> tuple[str, str]:
    conversation_history = detail_str(details, "conversation_history")
    if not conversation_history:
        return "", ""
    block = f"【直近の会話】\n{conversation_history}\n"
    rules = "- 直近の会話があるときは続きとして自然に返す。前の話題を無理に蒸し返さない\n"
    return rules, block


def _digest_section(details: dict[str, Any]) -> tuple[str, str]:
    event_digest = detail_str(details, "event_digest")
    if not event_digest:
        return "", ""
    block = f"【直近の出来事メモ】\n{event_digest}\n"
    rules = (
        "- 出来事メモは粗い要約。細かい数値や見えていないことは足さない。"
        "会話の補助として軽く触れてよい\n"
    )
    return rules, block


def _combat_safety_rules(details: dict[str, Any], character_mode: CharacterMode) -> str:
    rules = (
        "- プレイヤーを死なせる誤アドバイスは禁止。"
        "位置・種類の警告と短い応援はよいが、根拠の薄い作戦指示は控える\n"
    )
    if (
        character_mode == "battle"
        or details.get("has_visual_threats")
        or details.get("combat_active")
        or details.get("nearby_hostile_types")
    ):
        rules += (
            "- 敵対モブがいる／交戦中は『じっとして』『動かないで』『止まって』『固まれ』は禁止。"
            "ほとんどの敵対は寄ってくるか距離を取って撃ってくる。静止は危険\n"
        )
    tactics_notes = as_str_list(details.get("mob_tactics_notes"))
    forbidden_advice = as_str_list(details.get("forbidden_advice"))
    safe_hints = as_str_list(details.get("safe_hints"))
    if tactics_notes:
        joined = " / ".join(tactics_notes[:4])
        rules += f"- 周囲の敵の性質メモ: {joined}\n"
    if forbidden_advice:
        joined = "」「".join(forbidden_advice[:12])
        rules += f"- 今の敵について追加の禁止助言: 「{joined}」\n"
    if safe_hints:
        joined = " / ".join(safe_hints[:8])
        rules += f"- 言ってよい短いヒント例: {joined}\n"
    return rules


# 未使用警告回避（player_name は helper 経由だが import 保持）
_ = player_name
