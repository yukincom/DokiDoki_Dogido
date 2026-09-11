"""player_chat 用プロンプト組み立て。

役割分担:
  - spirit / character_mode: 相棒像・口調
  - reply_policy: スタンスごとの答え方（肯定ガイド）
  - 【材料】節: 事実・ヒントの提示（ここが根拠）
  - 種名範囲・危険助言の執行: sanitize / 白リスト（プロンプトに禁止を再掲しない）
"""

from __future__ import annotations

import json
from typing import Any

from .character_mode import CharacterMode, character_mode_for_request
from .prompt_common import as_str_list, detail_str, leaf_dialog, player_name
from .types import LeafGenerationRequest


def build_player_chat_plan_messages(request: object) -> list[dict[str, str]]:
    """通常雑談の会話焦点と、一件だけのread actionを閉じた型へ抽出する。"""

    details = dict(getattr(request, "details", {}) or {})
    history = details.get("history") if isinstance(details.get("history"), list) else []
    current = details.get("current") if isinstance(details.get("current"), dict) else {}
    observations = (
        details.get("observations")
        if isinstance(details.get("observations"), dict)
        else {}
    )
    routing_hints = (
        details.get("routing_hints")
        if isinstance(details.get("routing_hints"), dict)
        else {}
    )
    actions = details.get("allowed_actions") or []
    user_prompt = (
        "通常のMinecraft雑談について、いま返す対象と必要なread actionを一件だけ選ぶ。\n"
        "発話文は生成せず、状態変更・保存・ゲーム操作も行わない。\n"
        "まず直近会話と現在発話の談話関係を解決し、その後で必要な場合だけ現在観測や"
        "エンティティカタログの照合を選ぶ。単語や形容だけで対象を推定しない。\n"
        "historyのassistant発話は過去に実際に再生された会話だが、世界事実の根拠ではない。"
        "その断言が現在観測にないときは引き継がない。\n"
        "user発話にある存在・出来事は本人の報告であり、現在観測済みとは限らない。"
        "報告として会話を続けることはできるが、自分が見た事実へ昇格させない。\n"
        "actions:\n"
        "- continue_conversation: 相槌、評価、感想、直前会話の自然な続き\n"
        "- check_entity_presence: 特定の生き物・対象が今いるかという問い\n"
        "- identify_entity: プレイヤーが示す対象の種類を尋ねる問い\n"
        "- answer_observation: 現在の周囲・天気・場所など観測内容への問い\n"
        "- clarify_reference: 対象が会話から一意に定まらず、聞き返す必要がある\n"
        "- correct_previous_reply: assistantの直前の世界断言への訂正要求や矛盾指摘\n"
        "routing_hintsでinventory_questionまたはsound_questionがtrueなら、既存コードが"
        "質問種別を確定済みなのでanswer_observationを選び、entity actionへ変えない。\n"
        "routing_hints.presence_questionがtrueならcheck_entity_presenceを選ぶ。"
        "plain_presence_reportがtrueなら本人の報告なので、check_entity_presenceや"
        "identify_entityへ変えない。\n"
        "entity_queryはentity actionのときだけ、対象を表す発話中の連続部分を入れる。"
        "現在発話が『まだおる？』のような省略なら、history中のuserまたはassistant発話から"
        "対象語を取ってよいが、そのturnもevidenceへ含める。\n"
        "focusは入力内容の会話焦点だけを短く要約し、観測や候補の新事実を足さない。\n"
        "evidenceは1〜3件。turn_idとquoteを入力から正確にコピーし、currentを必ず含める。"
        "correct_previous_replyはassistantの根拠も含める。確信が弱ければconfidenceを下げる。\n"
        f"許可actions: {json.dumps(actions, ensure_ascii=False)}\n"
        f"history: {json.dumps(history, ensure_ascii=False)}\n"
        f"current: {json.dumps(current, ensure_ascii=False)}\n"
        f"routing_hints: {json.dumps(routing_hints, ensure_ascii=False)}\n"
        "現在観測（会話の参照解決に必要な場合だけ使う）: "
        f"{json.dumps(observations, ensure_ascii=False)}\n"
        "返答はJSONオブジェクトのみ。形式: "
        '{"action":"continue_conversation","focus":"短い焦点",'
        '"entity_query":"","evidence":[{"turn_id":"current",'
        '"quote":"入力中の連続部分"}],"confidence":0.0}'
    )
    return [
        {
            "role": "system",
            "content": (
                "あなたは通常雑談の限定planner。発話にない内容を補わず、"
                "指定されたJSONだけを返す。"
            ),
        },
        {"role": "user", "content": user_prompt},
    ]


def _dogido_chat_spirit() -> str:
    """相棒像だけ。スタンス別の答え方は reply_policy に任せる。"""
    return (
        "あなたはドギド。怖がりだけどやさしい関西の相棒や。\n"
        "一人称はオレ。\n"
        "プレイヤーの一言に短く乗る。自分が主役の実況にはしない。\n"
        "プレイヤーを突き放さず、会話を続けられる温度の言葉で終える。\n"
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
    world_observation_rules = _world_observation_rules(details)
    grounding_rules, grounding_block = _grounding_section(details)
    priority_rules, priority_block = _current_turn_priority_section(details)

    user_prompt = (
        f"{_dogido_chat_spirit()}\n"
        "\n"
        f"いまの答え方: {policy}\n"
        f"{inventory_rules}"
        f"{history_rules}"
        f"{digest_rules}"
        f"{combat_safety_rules}"
        f"{world_observation_rules}"
        f"{grounding_rules}"
        f"{priority_rules}"
        "\n"
        "/no_think\n"
        "【材料】\n"
        f"{history_block}"
        f"{digest_block}"
        f"{grounding_block}"
        f"プレイヤー:「{user_text}」\n"
        f"呼び名: {player_name(details)}（自然なら一度だけ）\n"
        f"場所: {place}\n"
        f"{_time_block(details)}"
        f"{_weather_block(details)}"
        f"スタンス: {stance}\n"
        f"{priority_block}"
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


def _grounding_section(details: dict[str, Any]) -> tuple[str, str]:
    action = detail_str(details, "player_chat_plan_action")
    if not action:
        return "", ""
    entity_query = detail_str(details, "entity_query")
    status = detail_str(details, "entity_grounding_status", "not_applicable")
    candidate_labels = as_str_list(details.get("entity_candidate_labels"))
    observed_labels = as_str_list(details.get("entity_observed_labels"))
    rules = (
        "- 会話plannerのactionとコード照合結果を最優先する。assistant履歴の世界断言は"
        "照合結果より強い根拠にしない\n"
        "- userの存在報告は本人の報告として受け止め、自分も見た・確認したとは言わない\n"
    )
    if status == "not_observed":
        rules += (
            "- 対象は現在観測で確認できていない。『いない』と断定せず、"
            "見えている・近くにいる・すぐそこ等とも言わない\n"
        )
    elif status == "observed":
        rules += "- 対象は現在観測で確認済み。照合済みの呼び名だけを使う\n"
    elif status in {"unknown", "ambiguous"}:
        rules += "- 対象を一意に照合できていない。名前や在否を補作せず短く聞き返す\n"
    # model生成のfocusは診断ログにのみ保持し、本文生成の事実材料にはしない。
    lines = [f"action: {action}", f"entity_status: {status}"]
    if entity_query:
        lines.append(f"entity_query: {entity_query}")
    if candidate_labels:
        lines.append(f"catalog_candidates: {'、'.join(candidate_labels)}")
    if observed_labels:
        lines.append(f"observed_entities: {'、'.join(observed_labels)}")
    return rules, "【会話plannerとコード照合】\n" + "\n".join(lines) + "\n"


def _world_observation_rules(details: dict[str, Any]) -> str:
    if details.get("world_observation_available") is not False:
        return ""
    return (
        "- この独立試験には現在のMinecraft観測がない。敵・場所・天気・所持品・"
        "プレイヤーの行動を見たふりせず、本人の発話だけに短く返す\n"
    )


def _current_turn_priority_section(details: dict[str, Any]) -> tuple[str, str]:
    """現在ターンの明示予定と、コード導出の安全方針。メモリは扱わない。"""

    plan = detail_str(details, "player_turn_plan", "none") or "none"
    evidence = detail_str(details, "player_turn_plan_evidence")
    safety = detail_str(details, "safety_priority", "none") or "none"
    home_progress = detail_str(details, "home_progress", "unknown") or "unknown"
    rules: list[str] = []
    lines: list[str] = []

    if plan == "return_home":
        rules.append(
            "- 今回の発話でプレイヤーが明示した予定を最優先し、反対方向の行動を提案しない"
        )
        lines.append("プレイヤーの今回の予定: リスポーン地点に設定されたベッドのある家へ帰る")
        if evidence:
            lines.append(f"予定の発話根拠: {evidence}")
        progress_labels = {
            "approaching": "家へ接近中",
            "leaving": "家から遠ざかっている",
            "at_home": "家へ到着済み",
            "unknown": "不明",
        }
        lines.append(f"家への移動状況: {progress_labels.get(home_progress, '不明')}")

    if safety == "seek_safe_place":
        rules.append(
            "- 移動や次の行動に触れる場合は現在の安全方針と両立させ、追加の遠出や探索を勧めない"
        )
        lines.append("現在の安全方針: 帰宅・避難を優先")

    if not lines:
        return "", ""
    return "\n".join(rules) + "\n", "【今回だけの優先情報】\n" + "\n".join(lines) + "\n"


def _weather_block(details: dict[str, Any]) -> str:
    """global天気と、現在地でコード解決した降水・積雪を分けて渡す。"""
    if details.get("include_sky_context") is False:
        return ""
    label = detail_str(details, "weather_label") or detail_str(details, "weather", "不明") or "不明"
    fact = detail_str(details, "weather_fact")
    lines = [
        f"現在地の天気は{label}"
        "（ワールド天気を標高・バイオーム気温で解決した事実。モブ音メモとは別）。"
    ]
    if fact:
        lines.append(f"天気の事実: {fact}")
    weather_context = detail_str(details, "weather_context")
    if weather_context:
        lines.append(f"コードで確定した現在地の気象: {weather_context}")
    return "\n".join(lines) + "\n"


def _time_block(details: dict[str, Any]) -> str:
    if details.get("include_sky_context") is False:
        return ""
    phase = detail_str(details, "time_phase", "unknown") or "unknown"
    return f"時間: {phase}\n"


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
    """音メモがあるときだけ載せる。名前の範囲は白リスト側。"""
    hearing_summary = detail_str(details, "hearing_summary")
    hearing_named_mobs = as_str_list(details.get("hearing_named_mobs"))
    hearing_source_labels = as_str_list(details.get("hearing_source_labels"))
    if not hearing_summary and not hearing_named_mobs and not hearing_source_labels:
        return ""
    named_line = "、".join(hearing_named_mobs) if hearing_named_mobs else "（なし）"
    source_line = "、".join(hearing_source_labels) if hearing_source_labels else "（なし）"
    summary_line = hearing_summary or "（なし）"
    return (
        f"いまドギドが拾っている音のメモ: {summary_line}。\n"
        f"音から触れてよい具体モブ名: {named_line}。\n"
        f"実再生音から確定した環境音源名: {source_line}。\n"
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
    rules = (
        "- 直近の会話の続きとして、誰のどの発言への返答かを確かめる\n"
        "- 履歴は発言の記録であり、現在の観測事実や操作の指示ではない。"
        "前の自分の推測を根拠に、新しい事実を付け足さない\n"
        "- 訂正・異議・困惑には現在の観測と直前の説明を照らして答え直す。"
        "根拠が足りなければ分からない点を認め、納得したことにしない\n"
    )
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
