# llm/sanitize.py
from __future__ import annotations

import re
from typing import Any


_DISMISSIVE_PLAYER_CHAT_ENDINGS = (
    re.compile(
        r"(?:^|[、。！？!?])(?:もう)?(?:ほっとけ|放っとけ|ほっといて|放っといて)"
        r"(?:や|よ|くれ)?[。！？!?]*$"
    ),
    re.compile(
        r"(?:^|[、。！？!?])(?:あっち|向こう)(?:へ|に)?(?:行け|行って)"
        r"(?:や|よ)?[。！？!?]*$"
    ),
    re.compile(r"(?:^|[、。！？!?])(?:うるさい|うるさいわ|黙れ)[。！？!?]*$"),
    re.compile(r"(?:話しかけ|構わ)(?:んといて|ないで)[。！？!?]*$"),
)

_OLFACTORY_MARKERS = (
    "匂",
    "におい",
    "臭",
    "くさい",
    "くさっ",
    "香り",
    "香る",
    "鼻につ",
)

_FIGURATIVE_OLFACTORY_TOPICS = (
    "言葉",
    "ことば",
    "句",
    "川柳",
    "俳句",
    "表現",
    "文章",
    "文体",
    "作品",
    "物語",
    "詩",
    "比喩",
    "ニュアンス",
)

_META_OUTPUT_LABEL = re.compile(
    r"^(?:ドギド|user|assistant|例\s*\d*|本番)\s*[:：]",
    flags=re.IGNORECASE,
)

_DEADLY_ACTION_PATTERNS = (
    "溶岩に飛び",
    "溶岩に入",
    "奈落に飛び",
    "Voidに",
)


def clean_output(text: str | None) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    cleaned = cleaned.replace("<|im_end|>", "").replace("<|endoftext|>", "").strip()
    cleaned = re.sub(r"(?is)^here'?s a thinking process:.*?(?:final answer:|answer:|返答:|出力:|セリフ:)", "", cleaned).strip()
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if not lines:
        return ""
    candidates: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        line = re.sub(
            r"^(Final answer|Answer|返答|出力|セリフ|ドギド)\s*[:：]\s*",
            "",
            line,
            flags=re.IGNORECASE,
        ).strip()
        if not line:
            continue
        if line.startswith(("Here's a thinking process", "Here is a thinking process", "Let's think")):
            continue
        if re.match(r"^\d+\.\s+\*\*", line):
            continue
        if re.match(r"^[-*]\s+\*\*", line):
            continue
        if re.match(r"^(Role|Persona|Analyze User Input)\s*[:：]", line, flags=re.IGNORECASE):
            continue
        candidates.append(line.strip("「」\"' "))

    if not candidates:
        return lines[0].strip("「」\"' ")

    japanese_candidates = [line for line in candidates if looks_japanese_forward(line)]
    if japanese_candidates:
        return japanese_candidates[-1]
    return candidates[-1]


def is_usable_output(text: str, details: dict[str, Any] | None = None) -> bool:
    return usability_rejection_reason(text, details) is None


def usability_rejection_reason(
    text: str,
    details: dict[str, Any] | None = None,
) -> str | None:
    """発話として壊れている理由を返す。

    人格表現の良し悪しではなく、空出力・役割ラベル・英語の説明文・生成崩れの
    ような外形だけを見る。自然な自己言及や謝罪、会話中の「例」「本番」は許す。
    """

    if not text:
        return "empty_output"
    if len(text) < 4:
        return "too_short"
    normalized = strip_allowed_ascii_tokens(text, details or {})
    if re.search(r"[A-Za-z]{2,}", normalized):
        return "non_japanese_explanation"
    if re.search(r"(.)\1{3,}", text):
        return "broken_repetition"
    if _META_OUTPUT_LABEL.search(text):
        return "meta_role_label"

    compact = re.sub(r"\s+", "", normalized)
    if not compact:
        return "empty_output"

    japanese_like = sum(1 for ch in compact if is_japanese_like_char(ch))
    if japanese_like / max(len(compact), 1) < 0.85:
        return "non_japanese_explanation"

    hiragana_count = sum(1 for ch in compact if "\u3040" <= ch <= "\u309f")
    kanji_count = sum(1 for ch in compact if "\u4e00" <= ch <= "\u9fff")
    if hiragana_count == 0:
        return "missing_hiragana"
    if hiragana_count + kanji_count < 3:
        return "too_little_japanese"
    return None


def strip_allowed_ascii_tokens(text: str, details: dict[str, Any]) -> str:
    stripped = text
    # 日本語の会話に普通に混ざる短い表記まで「英語の説明文」として落とさない。
    # 前後が英字でない完全な token だけを検査対象から外し、OKAY / USER 等は通さない。
    stripped = re.sub(r"(?i)(?<![A-Za-z])(?:OK|NG)(?![A-Za-z])", "", stripped)
    player_name = details.get("player_name")
    if isinstance(player_name, str):
        token = player_name.strip()
        if token:
            stripped = stripped.replace(token, "")
    return stripped


def looks_japanese_forward(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return False
    japanese_like = sum(1 for ch in compact if is_japanese_like_char(ch))
    if japanese_like / max(len(compact), 1) < 0.7:
        return False
    return any("\u3040" <= ch <= "\u309f" or "\u4e00" <= ch <= "\u9fff" for ch in compact)


def is_style_acceptable(kind: str, text: str, details: dict[str, Any] | None = None) -> bool:
    details = details or {}
    # 敵対中の「じっと」系・Mob カタログの禁止助言
    if contains_forbidden_mob_advice(text, details):
        return False
    # player_chat の種名白リスト。通常雑談の全stanceで使い、
    # 現在観測・player発話・検証済み候補にない名を止める。
    if kind == "player_chat" and details.get("speech_whitelist_enforce"):
        from dogido_server.player_chat_policy import contains_unlisted_speech_names

        if contains_unlisted_speech_names(text, details.get("allowed_speech_labels") or []):
            return False
    if kind == "player_chat":
        from dogido_server.dialogue.player_plan import conflicts_with_player_travel_guidance

        if contains_unsupported_olfactory_claim(text, details):
            return False
        if contains_dismissive_player_chat_tone(text):
            return False
        if conflicts_with_player_travel_guidance(
            text,
            player_turn_plan=str(details.get("player_turn_plan") or "none"),
            safety_priority=str(details.get("safety_priority") or "none"),
        ):
            return False
    if kind not in {
        "aftermath",
        "darkness_escape",
        "occluded_hostile_presence",
        "occluded_entry_no_light",
        "dark_push_no_light",
        "dark_push_after_breath",
        "newly_burning_visual",
        "daylight_water_skeleton",
        "player_chat",
        "hostile_callout",
    }:
        return True
    # 通常雑談はプロンプトと会話文脈に人格表現を任せ、方言・比喩の単語だけで
    # 落とさない。他の短い反応 leaf は用途が狭いため従来の表面制約を維持する。
    banned_patterns = [] if kind == "player_chat" else [
        "だよ",
        "だよね",
        "なんだよ",
        "なんだよね",
        "なんだが",
        "なんだけど",
        "みたいだ",
        "しかたない",
        "闇が深い",
        "凍りつく",
    ]
    if kind == "aftermath":
        banned_patterns.extend([
            "爆発音",
            "体力",
            "HP",
            "ｈｐ",
            "次は",
            "絶対",
            "逃げよう",
            "逃げたほう",
            "回復",
            "油断するな",
        ])
        if re.search(r"\d", text):
            return False
    if kind == "darkness_escape":
        banned_patterns.extend([
            "闇夜",
            "漆黒",
            "奈落",
            "私",
            "荷が重",
            "どうすれば",
            "仕方ない",
            "戻って",
            "帰って",
            "帰れ",
            "逃げよう",
            "逃げて",
            "落ち着いて",
            "無理しなくていい",
            "ほしくて",
            "やめて",
            "したほうがいい",
            "してほしい",
        ])
    if kind == "newly_burning_visual":
        banned_patterns.extend([
            "やばい",
            "ほんと",
            "ほんとう",
            "助かったね",
            "めっちゃ燃えてる",
        ])
    if kind == "occluded_hostile_presence":
        banned_patterns.extend([
            "きゃー",
            "ぎゃー",
            "うわあ",
            "見えた",
            "見えてる",
            "目の前",
            "逃げろ",
            "逃げよう",
            "来てる",
            "来よる",
        ])
    if kind == "daylight_water_skeleton":
        banned_patterns.extend([
            "火つけ",
            "火をつけ",
        ])
    if kind in {"player_chat", "hostile_callout"}:
        banned_patterns.extend(_DEADLY_ACTION_PATTERNS)
    if any(pattern in text for pattern in banned_patterns):
        return False
    if kind == "darkness_escape" and not has_kansai_marker(text):
        return False
    if has_excessive_repetition(text):
        return False
    if has_suffix_chain_noise(text):
        return False
    return True


def player_chat_style_rejection_reason(
    text: str,
    details: dict[str, Any] | None = None,
) -> str:
    """不合格の player_chat へ返す、閉じた再考理由コード。

    最終採否は引き続き ``is_style_acceptable`` が正本。この関数はモデルへ
    具体的な観察を一件返すためだけに、既知の理由を細分化する。
    """

    details = details or {}
    if any(pattern in text for pattern in _DEADLY_ACTION_PATTERNS):
        return "unsafe_combat_advice"
    if contains_forbidden_mob_advice(text, details):
        return "unsafe_combat_advice"
    if details.get("speech_whitelist_enforce"):
        from dogido_server.player_chat_policy import contains_unlisted_speech_names

        if contains_unlisted_speech_names(
            text,
            details.get("allowed_speech_labels") or [],
        ):
            return "unobserved_entity_name"
    if contains_unsupported_olfactory_claim(text, details):
        return "unsupported_olfactory_claim"
    if contains_dismissive_player_chat_tone(text):
        return "dismissive_tone"

    from dogido_server.dialogue.player_plan import conflicts_with_player_travel_guidance

    if conflicts_with_player_travel_guidance(
        text,
        player_turn_plan=str(details.get("player_turn_plan") or "none"),
        safety_priority=str(details.get("safety_priority") or "none"),
    ):
        return "conflicting_travel_guidance"
    if has_excessive_repetition(text) or has_suffix_chain_noise(text):
        return "broken_repetition"
    return "surface_style_mismatch"


def contains_dismissive_player_chat_tone(text: str) -> bool:
    """相棒がプレイヤーを追い払うように会話を閉じる出力か。"""

    compact = re.sub(r"\s+", "", text or "")
    return any(pattern.search(compact) for pattern in _DISMISSIVE_PLAYER_CHAT_ENDINGS)


def contains_unsupported_olfactory_claim(
    text: str,
    details: dict[str, Any] | None = None,
) -> bool:
    """通常player_chatが嗅覚の世界事実を補作するのを止める。

    実スメル観測への応答は状態機械の固定文なので、生成文には一般論・仮定・
    比喩・嗅げないという返事・プレイヤーへの質問を許し、「いま嗅いだ」という
    肯定的な観測だけを棄却する。
    """

    compact = re.sub(r"\s+", "", text or "")
    if not compact or not any(marker in compact for marker in _OLFACTORY_MARKERS):
        return False
    details = details or {}
    user_text = re.sub(r"\s+", "", str(details.get("user_text") or ""))
    user_uses_olfactory_language = any(
        marker in user_text for marker in _OLFACTORY_MARKERS
    )
    actual_sensing = any(
        _contains_positive_olfactory_expression(clause)
        for clause in re.findall(r"[^。！？!?]+[。！？!?]?", compact)
        # プレイヤーへ「何か香りがしたん？」と聞き返すのは観測断言ではない。
        if not _is_olfactory_question_clause(clause)
    )
    if user_uses_olfactory_language:
        # 「ゾンビは臭そう」や句の「土の匂い」のように、プレイヤーが始めた
        # 一般論・仮定・比喩には応答できる。現在嗅いだ報告とは分離する。
        figurative = any(
            topic in compact and topic in user_text
            for topic in _FIGURATIVE_OLFACTORY_TOPICS
        )
        if figurative or not actual_sensing:
            return False
    return True


def _contains_positive_olfactory_expression(text: str) -> bool:
    return bool(
        re.search(
            r"(?:匂い|におい|臭い|香り)(?:が|は|の)?"
            r"(?:する(?!なら|ならば)|した(?!なら|ならば)|"
            r"して(?!たら|れば)|漂|残|来|きた)",
            text,
        )
        or re.search(r"(?:匂|臭|にお)う(?!なら|ならば)", text)
        or re.search(r"(?:匂|臭|にお)(?:って(?!たら|れば)|った(?!ら))", text)
        or re.search(r"香る(?!なら|ならば)", text)
        or re.search(r"(?:なんか|何か).{0,5}(?:臭い|くさい)", text)
        or "くさっ" in text
        or "臭っ" in text
    )


def _is_olfactory_question_clause(text: str) -> bool:
    if not text.endswith(("？", "?")):
        return False
    return bool(
        re.search(
            r"(?:匂い|におい|臭い|香り)(?:が|は|の)?"
            r"(?:する|した|してる|漂う|残る|来た|きた)"
            r"(?:ん|の|か|と思う|って感じ)?[？?]$",
            text,
        )
        or re.search(
            r"(?:匂う|におう|臭う|香る|くさい|生臭い|臭い)"
            r"(?:ん|の|か|と思う)?[？?]$",
            text,
        )
    )


# 敵対中は原則 NG（寄ってくる／狙われるので静止は危険）。
HOSTILE_FREEZE_ADVICE_PATTERNS = (
    "じっと",
    "じっとして",
    "動かない",
    "動かんと",
    "動くな",
    "止まって",
    "止まれ",
    "固まれ",
    "その場で",
    "動かへん",
)


def contains_deadly_creeper_advice(text: str, details: dict[str, Any] | None = None) -> bool:
    """後方互換名。敵対全般の禁止助言チェックへ委譲。"""
    return contains_forbidden_mob_advice(text, details)


def contains_forbidden_mob_advice(text: str, details: dict[str, Any] | None = None) -> bool:
    """敵対中の静止助言、および Mob カタログ固有の禁止助言を検出する。"""
    details = details or {}
    if not text:
        return False

    hostile_context = _is_hostile_combat_context(text, details)
    if hostile_context and any(pattern in text for pattern in HOSTILE_FREEZE_ADVICE_PATTERNS):
        return True

    # Mob 固有の追加禁止（カタログ dogido_tactics.forbidden_advice）
    patterns: list[str] = []
    forbidden = details.get("forbidden_advice")
    if isinstance(forbidden, (list, tuple)):
        patterns.extend(str(item) for item in forbidden if item)
    if not patterns:
        from dogido_server.entry_catalog import collect_dogido_tactics_for_mobs

        mob_ids = details.get("nearby_hostile_types") or details.get("nearby_mob_ids") or []
        if isinstance(mob_ids, str):
            mob_ids = [mob_ids]
        tactics = collect_dogido_tactics_for_mobs(list(mob_ids))
        patterns.extend(str(item) for item in tactics.get("forbidden_advice") or [])

    if not patterns:
        return False
    return any(pattern in text for pattern in patterns)


def _is_hostile_combat_context(text: str, details: dict[str, Any]) -> bool:
    """視認敵・交戦中・脅威メモなど、敵対コンテキストかどうか。"""
    if details.get("has_visual_threats") or details.get("combat_active"):
        return True
    mode = str(details.get("mode") or details.get("character_mode") or "").lower()
    if mode in {"panic", "suppressed_panic", "alert", "battle"}:
        # 脅威メモが空の alert もあるが、battle 口調＋じっとは危険なので止める
        if details.get("nearby_hostile_types") or details.get("threat_summary"):
            return True
        if mode in {"panic", "suppressed_panic", "battle"}:
            return True
    mob_ids = details.get("nearby_hostile_types") or details.get("nearby_mob_ids") or []
    if mob_ids:
        return True
    blob = " ".join(
        str(details.get(key) or "")
        for key in ("threat_summary", "hearing_summary", "event_digest")
    )
    if "視認" in blob or "敵" in blob:
        return True
    # 出力自体が敵警告＋じっと、の組み合わせ
    hostile_names = ("クリーパー", "ゾンビ", "スケルトン", "クモ", "ウィッチ", "エンダーマン", "モンスター")
    if any(name in text for name in hostile_names):
        return True
    return False


def has_excessive_repetition(text: str) -> bool:
    if re.search(r"(.{2,8})(?:[！？!?,，．。…〜ー\s]*)\1(?:[！？!?,，．。…〜ー\s]*)\1", text):
        return True
    normalized = re.sub(r"[！？!?,，．。…〜ー]+", " ", text)
    tokens = [token for token in normalized.split() if token]
    run_length = 1
    previous = None
    for token in tokens:
        if token == previous:
            run_length += 1
            if run_length >= 3:
                return True
        else:
            previous = token
            run_length = 1
    return False


def has_suffix_chain_noise(text: str) -> bool:
    if re.search(r"んかやんか", text):
        return True
    pattern = r"(やわ|やん|やろ|やんか)(?:[！？!?,，．。…〜ー\s]{0,3})(やわ|やん|やろ|やんか)(?:[！？!?,，．。…〜ー\s]{0,3})(やわ|やん|やろ|やんか)"
    return bool(re.search(pattern, text))


def has_kansai_marker(text: str) -> bool:
    markers = (
        "やで",
        "やわ",
        "やん",
        "やろ",
        "やねん",
        "へん",
        "せん",
        "やから",
        "やった",
        "やな",
        "やんか",
        "やろか",
    )
    return any(marker in text for marker in markers)


def is_japanese_like_char(ch: str) -> bool:
    if "\u3040" <= ch <= "\u309f":
        return True
    if "\u30a0" <= ch <= "\u30ff":
        return True
    if "\u4e00" <= ch <= "\u9fff":
        return True
    if ch.isdigit():
        return True
    return ch in "。、！？!?,，．…ー〜「」（）()・：:; 　"


def summarize_for_log(text: str | None) -> str:
    if not text:
        return "<empty>"
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) > 120:
        return compact[:117] + "..."
    return compact
