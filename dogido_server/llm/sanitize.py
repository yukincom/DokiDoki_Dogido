# llm/sanitize.py
from __future__ import annotations

import re
from typing import Any


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
        line = re.sub(r"^(Final answer|Answer|返答|出力|セリフ)\s*[:：]\s*", "", line, flags=re.IGNORECASE).strip()
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
    if not text:
        return False
    if len(text) < 4:
        return False
    normalized = strip_allowed_ascii_tokens(text, details or {})
    if re.search(r"[A-Za-z]{2,}", normalized):
        return False
    if re.search(r"(.)\1{3,}", text):
        return False
    banned_fragments = ("ドギド", "すみません", "申し訳", "例", "本番", "user", "assistant")
    if any(fragment in text for fragment in banned_fragments):
        return False

    compact = re.sub(r"\s+", "", normalized)
    if not compact:
        return False

    japanese_like = sum(1 for ch in compact if is_japanese_like_char(ch))
    if japanese_like / max(len(compact), 1) < 0.85:
        return False

    hiragana_count = sum(1 for ch in compact if "\u3040" <= ch <= "\u309f")
    kanji_count = sum(1 for ch in compact if "\u4e00" <= ch <= "\u9fff")
    if hiragana_count == 0:
        return False
    if hiragana_count + kanji_count < 3:
        return False
    return True


def strip_allowed_ascii_tokens(text: str, details: dict[str, Any]) -> str:
    stripped = text
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
    # S2: player_chat はカタログ種名の白リスト外を拒否（details にキーがあるとき）
    if kind == "player_chat" and "allowed_speech_labels" in details:
        from dogido_server.player_chat_policy import contains_unlisted_speech_names

        if contains_unlisted_speech_names(text, details.get("allowed_speech_labels") or []):
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
    banned_patterns = [
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
        banned_patterns.extend([
            "溶岩に飛び",
            "溶岩に入",
            "奈落に飛び",
            "Voidに",
        ])
    if any(pattern in text for pattern in banned_patterns):
        return False
    if kind == "darkness_escape" and not has_kansai_marker(text):
        return False
    if has_excessive_repetition(text):
        return False
    if has_suffix_chain_noise(text):
        return False
    return True


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
