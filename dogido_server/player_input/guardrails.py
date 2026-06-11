# player_input/guardrails.py
from __future__ import annotations

HUSH_KEYWORDS = ("うるさい", "静かにして", "黙れ")
HOSTILE_QUERY_KEYWORDS = ("敵", "モンスター", "モブ", "敵対モブ")
HOSTILE_COUNT_QUERY_KEYWORDS = ("何体", "なんたい", "何匹", "残り", "何人", "何個体")
DRAGON_KEYWORDS = ("ドラゴン", "どらごん")
DIRECTION_QUERY_KEYWORDS = ("どこ", "どっち", "方向", "方角", "どのへん", "どの辺")
HAIKU_SAVE_PREFIXES = ("川柳保存:", "川柳保存：", "川柳:", "川柳：")
SAVE_LAST_HAIKU_KEYWORDS = (
    "今の句保存",
    "いまの句保存",
    "今の川柳保存",
    "いまの川柳保存",
    "さっきの句保存",
    "さっきの川柳保存",
)


def wants_quiet(normalized_text: str) -> bool:
    return bool(normalized_text) and any(keyword in normalized_text for keyword in HUSH_KEYWORDS)


def should_block_ambient(normalized_text: str) -> bool:
    return bool(normalized_text)


def asks_hostile_count(normalized_text: str) -> bool:
    if not normalized_text:
        return False
    if not any(keyword in normalized_text for keyword in HOSTILE_COUNT_QUERY_KEYWORDS):
        return False
    if any(keyword in normalized_text for keyword in HOSTILE_QUERY_KEYWORDS):
        return True
    return "残り" in normalized_text or "あと" in normalized_text


def asks_dragon_direction(normalized_text: str) -> bool:
    if not normalized_text:
        return False
    if not any(keyword in normalized_text for keyword in DRAGON_KEYWORDS):
        return False
    return any(keyword in normalized_text for keyword in DIRECTION_QUERY_KEYWORDS)


def asks_save_last_haiku(normalized_text: str) -> bool:
    if not normalized_text:
        return False
    if any(keyword in normalized_text for keyword in SAVE_LAST_HAIKU_KEYWORDS):
        return True
    return ("保存" in normalized_text) and ("今の" in normalized_text or "さっきの" in normalized_text) and (
        "句" in normalized_text or "川柳" in normalized_text
    )


def extract_player_haiku(raw_text: str | None) -> str | None:
    text = (raw_text or "").strip()
    if not text:
        return None
    matched_prefix = ""
    for prefix in HAIKU_SAVE_PREFIXES:
        if text.startswith(prefix):
            matched_prefix = prefix
            break
    if not matched_prefix:
        return None
    payload = text[len(matched_prefix):].replace("　", " ").strip()
    if not payload:
        return None
    pieces: list[str] = []
    for separator in ("\n", "/", "／", "|", "｜"):
        if separator in payload:
            pieces = [piece.strip() for piece in payload.split(separator) if piece.strip()]
            break
    if not pieces:
        pieces = [" ".join(payload.split())]
    if len(pieces) == 3:
        return "\n".join(pieces)
    return " ".join(payload.split())
