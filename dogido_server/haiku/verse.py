"""川柳一行の表示・読み・出典を一つに束ねる。

LLM の表記、TTS/音数/CAS が使う読み、workshop の行概念を別々の句として
持たないための小さな正規化境界。元カタログやモデル出力は書き換えず、
確定した一句にだけ派生行レコードを作る。
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

from dogido_server.memory_types import HaikuLine
from dogido_server.tts_reading import hiraganize_japanese_text, katakana_to_hiragana


_LINE_META = (
    ("line_1", "upper", "上五"),
    ("line_2", "middle", "中七"),
    ("line_3", "lower", "下五"),
)
_STRICT_HIRAGANA_LINE = re.compile(r"[\u3041-\u3096ー]+")


def split_haiku_verse(text: str | None) -> list[str]:
    """改行または三つの空白区切りを、順序を保った三行へ分ける。"""

    normalized = str(text or "").strip()
    if not normalized:
        return []
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    if len(lines) == 1:
        parts = [part.strip() for part in normalized.split() if part.strip()]
        if len(parts) == 3:
            lines = parts
    return lines


def canonical_line_reading(surface_text: str | None) -> str | None:
    """一行の表記を、内容を補作せず確定ひらがなへ変換する。"""

    surface = str(surface_text or "").strip()
    if not surface or "\n" in surface or "\r" in surface:
        return None
    reading = hiraganize_japanese_text(surface)
    reading = katakana_to_hiragana(reading)
    reading = re.sub(r"\s+", "", reading).strip()
    if not reading or _STRICT_HIRAGANA_LINE.fullmatch(reading) is None:
        return None
    return reading


def build_haiku_lines(
    surface_verse: str | None,
    *,
    line_sources: Iterable[dict[str, object]] | None = None,
    provenance: str = "generated",
) -> tuple[HaikuLine, ...]:
    """三行の表示・読み・行概念・出典を同じレコードへ確定する。"""

    surfaces = split_haiku_verse(surface_verse)
    if len(surfaces) != 3:
        return ()
    readings = [canonical_line_reading(surface) for surface in surfaces]
    if any(reading is None for reading in readings):
        return ()
    source_by_index: dict[int, dict[str, object]] = {}
    for row in line_sources or ():
        if not isinstance(row, dict):
            continue
        index = row.get("line_index")
        if isinstance(index, int) and not isinstance(index, bool) and index in (0, 1, 2):
            source_by_index.setdefault(index, row)

    result: list[HaikuLine] = []
    for index, (line_id, position, canonical_name) in enumerate(_LINE_META):
        source_row = source_by_index.get(index, {})
        atom_ids = tuple(
            str(atom_id).strip()
            for atom_id in source_row.get("atom_ids", [])
            if isinstance(atom_id, str) and atom_id.strip()
        ) if isinstance(source_row.get("atom_ids"), list) else ()
        raw_sources = source_row.get("sources")
        source_atoms = tuple(
            dict(source)
            for source in raw_sources
            if isinstance(source, dict)
        ) if isinstance(raw_sources, list) else ()
        result.append(
            HaikuLine(
                line_id=line_id,
                line_index=index,
                position=position,
                canonical_name=canonical_name,
                surface_text=surfaces[index],
                reading_text=str(readings[index]),
                source_atom_ids=atom_ids,
                source_atoms=source_atoms,
                provenance=provenance,
            )
        )
    return tuple(result)


def verse_surface_text(lines: Sequence[HaikuLine]) -> str:
    return "\n".join(line.surface_text for line in lines)


def verse_reading_text(lines: Sequence[HaikuLine]) -> str:
    return "\n".join(line.reading_text for line in lines)


def replace_haiku_line(
    lines: Sequence[HaikuLine],
    *,
    line_index: int,
    surface_text: str,
    reading_text: str,
    provenance: str,
    source_atom_ids: Sequence[str] = (),
    source_atoms: Sequence[dict[str, Any]] = (),
) -> tuple[HaikuLine, ...]:
    """同じ行IDの表示と読みを、一操作で必ず一緒に置き換える。"""

    if len(lines) != 3 or line_index not in (0, 1, 2):
        return ()
    current = lines[line_index]
    revised = list(lines)
    revised[line_index] = HaikuLine(
        line_id=current.line_id,
        line_index=current.line_index,
        position=current.position,
        canonical_name=current.canonical_name,
        surface_text=surface_text,
        reading_text=reading_text,
        source_atom_ids=tuple(source_atom_ids),
        source_atoms=tuple(dict(source) for source in source_atoms),
        provenance=provenance,
    )
    return tuple(revised)


def line_source_records(lines: Sequence[HaikuLine]) -> list[dict[str, object]]:
    """既存の生成修正器が読む line_sources へ、確定読みを同期する。"""

    return [
        {
            "line_index": line.line_index,
            "text": line.reading_text,
            "atom_ids": list(line.source_atom_ids),
            "sources": [dict(source) for source in line.source_atoms],
        }
        for line in lines
        if line.source_atom_ids
    ]
