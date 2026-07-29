"""VOICEVOX 向けの読み補正。

LLM 出力やカタログに無い自由文で、音読み誤読されやすい語をひらがなに寄せる。
表示ログ用の原文は変えず、合成直前だけ適用する想定。

パイプライン（docs/tts-reading-unidic-plan.md）:

1. UniDic 経路（optional: fugashi + unidic-lite）
   - 語種が和/混の内容語で漢字を含むトークンをひらがな化
   - トークン表層が優先読み表にあればそちらを使う（一日→いちにち 等）
2. 例外表 ``_TTS_HIRAGANA_REPLACEMENTS``
   - UniDic が残した語・辞書無し時の本線
   - 朝鮮などへの誤爆を減らすため複合を先に登録

``DOGIDO_TTS_READING_ENGINE``:
  - auto   … UniDic があれば使う（既定）
  - unidic … UniDic を試す（失敗時は例外表のみ）
  - off    … 例外表のみ
"""
from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any, Literal

logger = logging.getLogger(__name__)

TtsReadingEngine = Literal["auto", "unidic", "off"]

# UniDic トークン表層と一致したとき、辞書の既定読みよりこちらを優先する
# （「一日」→ついたち、「明日」→あす などを TTS 向けに上書き）
_PREFERRED_SURFACE_READINGS: dict[str, str] = {
    "朝": "あさ",
    "今朝": "けさ",
    "草地": "くさち",
    "一日": "いちにち",
    "大人": "おとな",
    "下手": "へた",
    "上手": "うま",
    "人参": "にんじん",
    "夕方": "ゆうがた",
    "昨夜": "ゆうべ",
    "今日": "きょう",
    "明日": "あした",
    "昨日": "きのう",
}

# 長い語から先に置換。辞書無し時の本線 + UniDic 後の残差用
_TTS_HIRAGANA_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("朝から", "あさから"),
    ("朝まで", "あさまで"),
    ("朝は", "あさは"),
    ("朝に", "あさに"),
    ("朝の", "あさの"),
    ("朝だ", "あさだ"),
    ("朝や", "あさや"),
    ("朝を", "あさを"),
    ("朝が", "あさが"),
    ("今朝", "けさ"),
    # 単独「朝」より前に複合を置く（単純 replace の誤爆防止）
    ("朝鮮", "ちょうせん"),
    ("草地", "くさち"),
    ("一日", "いちにち"),
    ("大人", "おとな"),
    ("下手", "へた"),
    ("上手", "うま"),  # 「うわて」誤読回避。文脈により弱いが VOICEVOX 向け
    ("人参", "にんじん"),
    ("夕方", "ゆうがた"),
    ("昨夜", "ゆうべ"),
    ("今日", "きょう"),
    ("明日", "あした"),
    ("昨日", "きのう"),
    # 単独「朝」は最後
    ("朝", "あさ"),
)

_KANJI_RE = re.compile(r"[\u4e00-\u9fff]")
# UniDic 語種: 和・混のみ対象（漢は音読み想定で触らない。固有名詞も触らない）
_GOSHU_HIRAGANA = frozenset({"和", "混"})
_POS_SKIP = frozenset({"助詞", "助動詞", "補助記号", "記号", "空白"})

_tagger_lock = threading.Lock()
_tagger: Any | None = None
_tagger_init_attempted = False
_tagger_available: bool | None = None


def _normalize_engine(value: str | None) -> TtsReadingEngine:
    raw = (value or "auto").strip().lower()
    if raw in ("auto", "unidic", "off"):
        return raw  # type: ignore[return-value]
    return "auto"


def resolve_tts_reading_engine(engine: str | None = None) -> TtsReadingEngine:
    """engine 引数 → 環境変数 → auto。"""
    if engine is not None:
        return _normalize_engine(engine)
    return _normalize_engine(os.environ.get("DOGIDO_TTS_READING_ENGINE"))


def _has_kanji(text: str) -> bool:
    return bool(_KANJI_RE.search(text))


def _katakana_to_hiragana(text: str) -> str:
    """カタカナをひらがなへ。長音・その他はそのまま。"""
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if 0x30A1 <= code <= 0x30F6:  # ァ-ヶ
            out.append(chr(code - 0x60))
        else:
            out.append(ch)
    return "".join(out)


def apply_manual_tts_replacements(text: str) -> str:
    """例外表だけ適用する（テスト・フォールバック用）。"""
    result = text
    for surface, reading in _TTS_HIRAGANA_REPLACEMENTS:
        if surface in result:
            result = result.replace(surface, reading)
    return result


def _get_unidic_tagger() -> Any | None:
    """プロセス内シングルトン。初回失敗時は以降 None（落ちない）。"""
    global _tagger, _tagger_init_attempted, _tagger_available
    if _tagger_init_attempted:
        return _tagger
    with _tagger_lock:
        if _tagger_init_attempted:
            return _tagger
        _tagger_init_attempted = True
        try:
            import fugashi  # type: ignore[import-not-found]

            tagger = fugashi.Tagger()
            # 一度だけ軽いウォームアップ（辞書ロード確認）
            _ = list(tagger("朝"))
            _tagger = tagger
            _tagger_available = True
            logger.info("tts_reading: UniDic (fugashi) ready")
        except Exception as exc:  # noqa: BLE001 — optional path must never break TTS
            _tagger = None
            _tagger_available = False
            logger.info("tts_reading: UniDic unavailable (%s); manual replacements only", exc)
        return _tagger


def unidic_available() -> bool:
    """UniDic 経路が使えるか（初回は初期化を試みる）。"""
    return _get_unidic_tagger() is not None


def reset_unidic_tagger_for_tests() -> None:
    """テスト用にシングルトン状態をリセットする。"""
    global _tagger, _tagger_init_attempted, _tagger_available
    with _tagger_lock:
        _tagger = None
        _tagger_init_attempted = False
        _tagger_available = None


def _should_hiraganaize_token(surface: str, feature: Any) -> bool:
    if not surface or not _has_kanji(surface):
        return False
    # 優先読み表にある表層は語種に関わらずひらがな化（上手=漢, 人参=漢 など）
    if surface in _PREFERRED_SURFACE_READINGS:
        return True
    goshu = getattr(feature, "goshu", None) or ""
    if goshu not in _GOSHU_HIRAGANA:
        return False
    pos1 = getattr(feature, "pos1", None) or ""
    if pos1 in _POS_SKIP:
        return False
    kana = (getattr(feature, "kana", None) or getattr(feature, "pron", None) or "").strip()
    return bool(kana)


def _token_to_hiragana(surface: str, feature: Any) -> str:
    preferred = _PREFERRED_SURFACE_READINGS.get(surface)
    if preferred is not None:
        return preferred
    # kana は表記寄り（キョウ）、pron は長音記号寄り（キョー）。VOICEVOX には kana を優先。
    kana = (getattr(feature, "kana", None) or getattr(feature, "pron", None) or "").strip()
    return _katakana_to_hiragana(kana)


def apply_unidic_reading(text: str) -> str:
    """優先読み + 和/混の漢字語を UniDic 単位でひらがな化する。失敗時は原文。"""
    tagger = _get_unidic_tagger()
    if tagger is None:
        return text
    try:
        parts: list[str] = []
        for word in tagger(text):
            surface = word.surface
            feature = word.feature
            if _should_hiraganaize_token(surface, feature):
                reading = _token_to_hiragana(surface, feature)
                parts.append(reading if reading else surface)
            else:
                parts.append(surface)
        return "".join(parts)
    except Exception as exc:  # noqa: BLE001
        logger.warning("tts_reading: UniDic parse failed (%s); skipping", exc)
        return text


def prepare_text_for_tts(text: str, *, engine: str | None = None) -> str:
    """合成用に読みやすい表記へ寄せる。空ならそのまま。

    Parameters
    ----------
    text:
        合成したい原文（ログ用漢字交じり可）。
    engine:
        ``auto`` / ``unidic`` / ``off``。None なら環境変数または auto。
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return ""

    mode = resolve_tts_reading_engine(engine)
    result = cleaned

    # 1) UniDic（トークン単位・朝鮮を壊さない）
    if mode != "off" and _has_kanji(result):
        if mode in ("auto", "unidic"):
            unidic_out = apply_unidic_reading(result)
            # 初期化失敗時は apply_unidic_reading が原文を返す
            result = unidic_out

    # 2) 例外表（残差・辞書無し・複合フレーズ）
    result = apply_manual_tts_replacements(result)
    return result
