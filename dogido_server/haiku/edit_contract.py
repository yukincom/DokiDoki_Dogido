"""川柳 workshop の検証済み行差分契約。

生成、セッション中の明示採用、長期保存の三境界が、同じ compare-and-swap
検査を使うための小さな純関数。LLM 出力の解釈や状態変更はここで行わない。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


LINE_EDIT_CONTRACT_VERSION = "line_compare_and_swap_v1"
PLAYER_LINE_EDIT_CONTRACT_VERSION = "player_line_compare_and_swap_v1"


def line_edit_plan_applies(
    *,
    original_text: str,
    revised_text: str,
    edit_contract: str | None,
    edits: Sequence[dict[str, Any]] | None,
) -> bool:
    """差分を元句へ適用すると修正句になり、対象外行が不変なら True。"""

    if edit_contract not in {
        LINE_EDIT_CONTRACT_VERSION,
        PLAYER_LINE_EDIT_CONTRACT_VERSION,
    } or not edits:
        return False
    original_lines = _three_lines(original_text)
    revised_lines = _three_lines(revised_text)
    if original_lines is None or revised_lines is None:
        return False
    changed: set[int] = set()
    for edit in edits:
        if not isinstance(edit, dict):
            return False
        index = edit.get("line_index")
        expected = edit.get("expected_text")
        replacement = edit.get("replacement_text")
        atom_ids = edit.get("atom_ids")
        provenance = edit.get("provenance")
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or index not in (0, 1, 2)
            or index in changed
            or expected != original_lines[index]
            or replacement != revised_lines[index]
            or not isinstance(expected, str)
            or not isinstance(replacement, str)
            or expected == replacement
        ):
            return False
        if edit_contract == LINE_EDIT_CONTRACT_VERSION:
            # AI生成行は、従来どおり検証済みsource atomを必須にする。
            if (
                provenance not in (None, "generated_grounded")
                or not isinstance(atom_ids, list)
                or not atom_ids
                or any(
                    not isinstance(atom_id, str) or not atom_id.strip()
                    for atom_id in atom_ids
                )
            ):
                return False
        else:
            # プレイヤーが明示した語は、観測atomへ偽装しない。
            if provenance != "player_explicit" or atom_ids not in (None, []):
                return False
        changed.add(index)
    return all(
        index in changed or revised_lines[index] == original_lines[index]
        for index in range(3)
    )


def _three_lines(text: str) -> tuple[str, str, str] | None:
    lines = tuple(line.strip() for line in (text or "").splitlines() if line.strip())
    if len(lines) != 3:
        return None
    return lines[0], lines[1], lines[2]
