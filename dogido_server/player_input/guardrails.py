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
# 持ち物・所持品の話題。常時 inventory を LLM に渡さないための意図検知。
INVENTORY_TOPIC_KEYWORDS = (
    "インベントリ",
    "いんべんとり",
    "インベ",
    "いんべ",
    "持ち物",
    "もちもの",
    "所持",
    "しょじ",
    "バッグ",
    "ばっぐ",
    "アイテム",
    "あいれむ",
    "何持って",
    "なに持って",
    "何を持って",
    "なにを持って",
    "何があった",
    "なにがあった",
)
# 所持とセットで聞かれやすい具体アイテム語（whisper の誤変換「明る」も含む）
INVENTORY_ITEM_HINT_KEYWORDS = (
    "松明",
    "とうまつ",
    "トーチ",
    "とーち",
    "明かり",
    "あかり",
    "明る",
    "照明",
    "ベッド",
    "べっど",
    "食料",
    "しょくりょう",
    "食べ物",
    "たべもの",
    "剣",
    "けん",
    "防具",
    "ぼうぐ",
    "ダイヤ",
    "だいや",
    "鉄",
    "てつ",
    "石炭",
    "せきたん",
    "木材",
    "もくざい",
    "棒",
    "ぼう",
)
POSSESSION_HINT_KEYWORDS = (
    "持って",
    "もって",
    "持っと",  # 持っとる
    "もっとる",
    "持ってた",
    "もってた",
    "持ってる",
    "もってる",
    "持っておる",
    "あるかな",
    "あるやろ",
    "残って",
    "のこって",
)


def _fold_kana(text: str) -> str:
    """カタカナをひらがなに畳む。音声認識（whisper）がカタカナで返すことがあるため、
    キーワード判定はかなを正規化してから行う。"""
    return "".join(
        chr(ord(ch) - 0x60) if "ァ" <= ch <= "ヶ" else ch
        for ch in text
    )


def wants_quiet(normalized_text: str) -> bool:
    normalized_text = _fold_kana(normalized_text)
    return bool(normalized_text) and any(keyword in normalized_text for keyword in HUSH_KEYWORDS)


def should_block_ambient(normalized_text: str) -> bool:
    return bool(normalized_text)


def asks_hostile_count(normalized_text: str) -> bool:
    normalized_text = _fold_kana(normalized_text)
    if not normalized_text:
        return False
    if not any(keyword in normalized_text for keyword in HOSTILE_COUNT_QUERY_KEYWORDS):
        return False
    if any(keyword in normalized_text for keyword in HOSTILE_QUERY_KEYWORDS):
        return True
    return "残り" in normalized_text or "あと" in normalized_text


def asks_dragon_direction(normalized_text: str) -> bool:
    normalized_text = _fold_kana(normalized_text)
    if not normalized_text:
        return False
    if not any(_fold_kana(keyword) in normalized_text for keyword in DRAGON_KEYWORDS):
        return False
    return any(keyword in normalized_text for keyword in DIRECTION_QUERY_KEYWORDS)


def asks_save_last_haiku(normalized_text: str) -> bool:
    normalized_text = _fold_kana(normalized_text)
    if not normalized_text:
        return False
    if any(keyword in normalized_text for keyword in SAVE_LAST_HAIKU_KEYWORDS):
        return True
    return ("保存" in normalized_text) and ("今の" in normalized_text or "さっきの" in normalized_text) and (
        "句" in normalized_text or "川柳" in normalized_text
    )


def asks_inventory(normalized_text: str) -> bool:
    """プレイヤーが所持品・インベントリについて聞いているか。

    常時 inventory 全文を LLM に渡すと重いので、このときだけ要約を注入する。
    whisper の誤変換（例: 明かり→明る）も拾う。
    """
    normalized_text = _fold_kana(normalized_text)
    if not normalized_text:
        return False
    if any(keyword in normalized_text for keyword in INVENTORY_TOPIC_KEYWORDS):
        return True
    has_item_hint = any(keyword in normalized_text for keyword in INVENTORY_ITEM_HINT_KEYWORDS)
    has_possession = any(keyword in normalized_text for keyword in POSSESSION_HINT_KEYWORDS)
    if has_item_hint and has_possession:
        return True
    # 「松明ある？」「明かりある？」のように所持を省略した短い問い
    if has_item_hint and ("ある" in normalized_text or "ない" in normalized_text or "何" in normalized_text or "なに" in normalized_text):
        return True
    # 「何持ってる」「持ち物は？」系
    if has_possession and ("何" in normalized_text or "なに" in normalized_text or "どんな" in normalized_text):
        return True
    return False


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
