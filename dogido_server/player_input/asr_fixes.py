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


# 視線先・手持ちに感圧板があるときだけ足す（誤爆を抑える）
_CONTEXT_PRESSURE_PLATE_EXTRA: tuple[tuple[str, str], ...] = (
    ("感圧番", "感圧板"),
    ("間圧板", "感圧板"),
    ("管圧板", "感圧板"),
    ("関圧板", "感圧板"),
    ("環圧板", "感圧板"),
)


def has_pressure_plate_context(
    *,
    look_name: str | None = None,
    held_item: str | None = None,
    inventory: dict[str, int] | None = None,
) -> bool:
    """look_target / 手持ち / 所持に pressure_plate があるか。"""

    def is_pp(value: str | None) -> bool:
        return bool(value) and "pressure_plate" in str(value).lower()

    if is_pp(look_name) or is_pp(held_item):
        return True
    for key in inventory or {}:
        if is_pp(str(key)):
            return True
    return False


def apply_contextual_asr_fixes(
    text: str,
    *,
    look_name: str | None = None,
    held_item: str | None = None,
    inventory: dict[str, int] | None = None,
) -> tuple[str, list[tuple[str, str]]]:
    """固定表 +（感圧板コンテキスト時のみ）追加置換。"""
    out, applied = apply_asr_fixes(text)
    if not has_pressure_plate_context(
        look_name=look_name,
        held_item=held_item,
        inventory=inventory,
    ):
        return out, applied
    extras = tuple(
        sorted(_CONTEXT_PRESSURE_PLATE_EXTRA, key=lambda pair: len(pair[0]), reverse=True)
    )
    for wrong, right in extras:
        if wrong in out:
            out = out.replace(wrong, right)
            applied.append((wrong, right))
    return out, applied