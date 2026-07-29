"""VOICEVOX 向けの読み補正（薄い例外表）。

LLM 出力やカタログに無い自由文で、音読み誤読されやすい語をひらがなに寄せる。
表示ログ用の原文は変えず、合成直前だけ適用する想定。

辞書は本モジュール内の定数（外部ファイルではない）。
全訓読みの自動判定はしない。本命の形態素+UniDic 方針は
docs/tts-reading-unidic-plan.md。導入後も本表は例外・上書き用に残す。
"""
from __future__ import annotations

# 長い語から先に置換（「朝から」を「あさから」にしたあと単独「朝」が残らないよう）
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
    # 単独「朝」は最後（朝鮮などへの誤爆を減らすため複合を先に）
    ("朝", "あさ"),
)


def prepare_text_for_tts(text: str) -> str:
    """合成用に読みやすい表記へ寄せる。空ならそのまま。"""
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    result = cleaned
    for surface, reading in _TTS_HIRAGANA_REPLACEMENTS:
        if surface in result:
            result = result.replace(surface, reading)
    return result
