"""STT（whisper 等）の既知誤変換を正表記へ直す（最小固定表）。

カタログ全件は埋めない。実害が出たゴミ語だけ痛みドリブンで足す。
調査メモ: docs/research/minecraft-ja-stt-dictionary-2026-07.md
Issue: #29
"""

from __future__ import annotations

# (誤, 正) — 長い誤から先に当てるため apply 側でソートする
# 一般語を壊しにくい「ほぼ辞書に無いゴミ → MC 正表記」だけ
_ASR_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    # 感圧板（かんあつばん）— whisper が漢字を寄せ集める典型
    ("関圧番", "感圧板"),
    ("管轄版", "感圧板"),
    ("間月版", "感圧板"),
    ("貨物板", "感圧板"),
    ("感圧版", "感圧板"),  # 板/版の表記ゆれ
    ("かんあつばん", "感圧板"),
    ("カンアツバン", "感圧板"),
)


def asr_replacements() -> tuple[tuple[str, str], ...]:
    """長い wrong を先に（部分置換の取り違え防止）。"""
    return tuple(sorted(_ASR_REPLACEMENTS, key=lambda pair: len(pair[0]), reverse=True))


def apply_asr_fixes(text: str) -> tuple[str, list[tuple[str, str]]]:
    """本文を直し、(結果, 適用した (wrong, right) 一覧) を返す。"""
    out = text or ""
    if not out:
        return out, []
    applied: list[tuple[str, str]] = []
    for wrong, right in asr_replacements():
        if not wrong or wrong == right:
            continue
        if wrong in out:
            out = out.replace(wrong, right)
            applied.append((wrong, right))
    return out, applied
