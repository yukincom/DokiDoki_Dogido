# player_input/guardrails.py
from __future__ import annotations

HUSH_KEYWORDS = ("うるさい", "静かにして", "黙れ")
HOSTILE_QUERY_KEYWORDS = ("敵", "モンスター", "モブ", "敵対モブ")
HOSTILE_COUNT_QUERY_KEYWORDS = ("何体", "なんたい", "何匹", "残り", "何人", "何個体")
DRAGON_KEYWORDS = ("ドラゴン", "どらごん")
DIRECTION_QUERY_KEYWORDS = ("どこ", "どっち", "方向", "方角", "どのへん", "どの辺")
HAIKU_SAVE_PREFIXES = ("川柳保存:", "川柳保存：", "川柳:", "川柳：")
HAIKU_REVISE_PREFIXES = ("直し:", "直し：", "川柳直し:", "川柳直し：", "句直し:", "句直し：")
SAVE_LAST_HAIKU_KEYWORDS = (
    "今の句保存",
    "いまの句保存",
    "今の川柳保存",
    "いまの川柳保存",
    "さっきの句保存",
    "さっきの川柳保存",
)
HAIKU_RECALL_KEYWORDS = (
    "句思い出",
    "句を思い出",
    "川柳思い出",
    "さっきの句",
    "前の句",
    "いつ頃の句",
    "どこで詠んだ",
    "どこで詠んだ句",
    "覚えてる句",
    "保存した句",
    "今月の句",
    "今日の句",
    "昨日の句",
)
# 表示名ヒント → biome id（想起用の粗い辞書。カタログ全件は haiku_recall 側で補完可）
BIOME_HINT_TO_ID = {
    "草地": "meadow",
    "くさち": "meadow",
    "雪のタイガ": "snowy_taiga",
    "雪原": "snowy_plains",
    "砂漠": "desert",
    "平原": "plains",
    "森": "forest",
    "洞窟": "dripstone_caves",
    "キノコ島": "mushroom_fields",
    "ネザー": "nether_wastes",
}
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


def _parse_haiku_payload(payload: str) -> str | None:
    payload = payload.replace("　", " ").strip()
    if not payload:
        return None
    pieces: list[str] = []
    for separator in ("\n", "/", "／", "|", "｜"):
        if separator in payload:
            pieces = [piece.strip() for piece in payload.split(separator) if piece.strip()]
            break
    if not pieces:
        pieces = [" ".join(payload.split())]
    if not pieces:
        return None
    if len(pieces) == 1:
        return pieces[0]
    return "\n".join(pieces[:3])


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
    return _parse_haiku_payload(text[len(matched_prefix) :])


def extract_revised_haiku(raw_text: str | None) -> str | None:
    """プレイヤーが直した 5-7-5。元句とペアで長期保存する。"""
    text = (raw_text or "").strip()
    if not text:
        return None
    matched_prefix = ""
    for prefix in HAIKU_REVISE_PREFIXES:
        if text.startswith(prefix):
            matched_prefix = prefix
            break
    if not matched_prefix:
        return None
    return _parse_haiku_payload(text[len(matched_prefix) :])


def extract_reading_correction(raw_text: str | None) -> tuple[str, str, str | None] | None:
    """読み訂正。戻り値: (surface, reading, wrong_reading|None)

    例:
      読み: 草地=くさち
      草地はくさち
      草地の読みはくさち
      そうちじゃなくてくさち（surface は呼び出し側で文脈補完してもよい）

    「おはようさん」など日常の「は」を含む発話には反応しない。
    """
    import re

    text = (raw_text or "").strip()
    if not text:
        return None
    folded = _fold_kana(text)

    # 読み: 草地=くさち
    m = re.match(
        r"^(?:読み|よみ)\s*[:：]?\s*(.+?)\s*[=＝:：]\s*([ぁ-んー]+)\s*$",
        text,
    )
    if m:
        surface, reading = m.group(1).strip(), m.group(2).strip()
        if surface and reading and surface != reading:
            return surface, reading, None

    # 明示: 草地の読みはくさち / 草地のよみはくさち
    m = re.match(
        r"^(.+?)(?:の読みは|のよみは)\s*([ぁ-んー]+)\s*(?:やで|や|だよ|です)?\s*$",
        text,
    )
    if m:
        surface, reading = m.group(1).strip(), m.group(2).strip()
        if surface and reading and surface != reading and len(surface) <= 20:
            return surface, reading, None

    # 漢字などを含む語 + は + ひらがな読み（「草地はくさち」）
    # 「おはようさん」は surface に漢字が無く、読み訂正にしない
    m = re.match(
        r"^(.+?)は\s*([ぁ-んー]+)\s*(?:やで|や|だよ|です)?\s*$",
        text,
    )
    if m:
        surface, reading = m.group(1).strip(), m.group(2).strip()
        has_kanji = bool(re.search(r"[\u4e00-\u9fff]", surface))
        if (
            has_kanji
            and surface
            and reading
            and surface != reading
            and 1 <= len(surface) <= 20
            and 1 <= len(reading) <= 20
        ):
            return surface, reading, None

    # そうちじゃなくてくさち
    m = re.search(
        r"([ぁ-んー]{2,})(?:じゃなくて|ではなく|やなくて|じゃなく)\s*([ぁ-んー]{2,})",
        folded,
    )
    if m:
        wrong, right = m.group(1), m.group(2)
        if wrong != right:
            return wrong, right, wrong

    return None


def asks_haiku_recall(normalized_text: str) -> bool:
    normalized_text = _fold_kana(normalized_text)
    if not normalized_text:
        return False
    if any(keyword in normalized_text for keyword in HAIKU_RECALL_KEYWORDS):
        return True
    has_haiku = "句" in normalized_text or "川柳" in normalized_text
    has_recall = any(
        token in normalized_text
        for token in (
            "思い出",
            "覚えて",
            "前に",
            "いつ",
            "どこで",
            "場所",
            "今月",
            "昨日",
            "今日",
            "ひと月",
            "一ヶ月",
            "1ヶ月",
            "ヶ月",
            "寒い",
            "暖かい",
            "あたたかい",
            "乾燥",
            "洞窟",
            "ネザー",
            "エンド",
            "海",
            "水辺",
            "氷雪",
            "冷帯",
            "温帯",
            "暖地",
        )
    )
    # 「7月の句」「草地の句」など、句＋場所/時の言及
    if has_haiku:
        from dogido_server.entry_catalog import resolve_biome_place_from_text

        place = resolve_biome_place_from_text(normalized_text)
        if place.get("biome_ids") or place.get("biome_id"):
            return True
        if bool(__import__("re").search(r"\d{1,2}\s*月", normalized_text)):
            return True
    return has_haiku and has_recall


def haiku_recall_biome_hint(normalized_text: str) -> str | None:
    """後方互換: 具体バイオーム id だけ返す。グループは resolve_biome_place_from_text を使う。"""
    from dogido_server.entry_catalog import resolve_biome_place_from_text

    place = resolve_biome_place_from_text(normalized_text)
    biome_id = place.get("biome_id")
    return str(biome_id) if biome_id else None


def parse_haiku_time_range(
    normalized_text: str,
    *,
    now: "datetime | None" = None,
) -> tuple["datetime | None", "datetime | None", str | None]:
    """壁時計での期間フィルタ。戻り値: (since, until, label)

    - 今月: 当月1日 00:00 〜 now
    - ここひと月 / このひと月 / 直近ひと月: now-30日 〜 now
    - 今日 / 昨日 / N月
    ゲーム内時刻は使わない。
    """
    from datetime import datetime, timedelta, timezone
    import re

    text = _fold_kana(normalized_text or "")
    if not text:
        return None, None, None
    if now is None:
        now = datetime.now().astimezone()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    local = now.astimezone()
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)

    if "ここひと月" in text or "このひと月" in text or "直近ひと月" in text or "ここ一ヶ月" in text or "ここ1ヶ月" in text or "ここ一カ月" in text:
        return local - timedelta(days=30), local, "ここひと月"
    if "今月" in text:
        month_start = day_start.replace(day=1)
        return month_start, local, "今月"
    if "今日" in text or "きょう" in text:
        return day_start, local, "今日"
    if "昨日" in text or "きのう" in text:
        y = day_start - timedelta(days=1)
        return y, day_start, "昨日"
    if "先週" in text:
        return local - timedelta(days=7), local, "先週"

    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = local.year
        try:
            start = day_start.replace(year=year, month=month, day=day)
        except ValueError:
            return None, None, None
        if start > local:
            start = start.replace(year=year - 1)
        return start, start + timedelta(days=1), f"{start.month}月{start.day}日"

    m = re.search(r"(\d{1,2})\s*月", text)
    if m:
        month = int(m.group(1))
        if not 1 <= month <= 12:
            return None, None, None
        year = local.year
        start = day_start.replace(year=year, month=month, day=1)
        if start > local:
            start = start.replace(year=year - 1)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1, day=1)
        else:
            end = start.replace(month=start.month + 1, day=1)
        return start, min(end, local + timedelta(seconds=1)), f"{start.month}月"

    return None, None, None
