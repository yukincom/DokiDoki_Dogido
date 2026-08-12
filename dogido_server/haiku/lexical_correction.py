"""出典が確定した行だけに掛ける、狭いカタログ名のかな訂正。

川柳はカタログ名の全文を写す必要はない。一方、モデルが catalog_label を
出典として選んだ行に、そのラベル中の長いかな語と一字だけ違う断片があれば、
それは詩的省略ではなく誤字として安全に直せる。全カタログへの fuzzy 検索は
行わず、その行で意味保持が確認された atom だけを候補にする。
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping

from .source_atoms import HaikuSourceAtom


# カタカナ名の直後の助詞（例: シラカバ + の）まで必須語へ含めない。
# 文字種の境界で分けることで、ラベルの主要語だけを訂正候補にできる。
_KANA_RUN_RE = re.compile(r"[ぁ-ゖー]+|[ァ-ヺー]+")
_MIN_TERM_LENGTH = 4


@dataclass(frozen=True, slots=True)
class CatalogKanaCorrection:
    original: str
    corrected: str
    source_atom_id: str


def correct_grounded_catalog_kana(
    text: str,
    *,
    atom_ids: tuple[str, ...],
    atom_by_id: Mapping[str, HaikuSourceAtom],
) -> CatalogKanaCorrection | None:
    """一意な一字置換だけを返す。ラベルの残りの語は出現を要求しない。"""

    compact = re.sub(r"\s+", "", str(text or ""))
    normalized = _hiragana(compact)
    terms: dict[str, str] = {}
    for atom_id in atom_ids:
        atom = atom_by_id.get(atom_id)
        if atom is None or atom.kind != "catalog_label":
            continue
        for raw_term in _KANA_RUN_RE.findall(atom.text):
            term = _hiragana(raw_term)
            if len(term) < _MIN_TERM_LENGTH or term in normalized:
                continue
            terms.setdefault(term, atom.atom_id)

    suggestions: set[tuple[int, int, str, str]] = set()
    for term, atom_id in terms.items():
        width = len(term)
        for start in range(len(normalized) - width + 1):
            fragment = normalized[start : start + width]
            # 挿入・削除まで自動修正すると音数と語境界を変えやすいので、
            # 同じ長さの一字置換だけに閉じる。
            if sum(left != right for left, right in zip(fragment, term)) == 1:
                suggestions.add((start, start + width, term, atom_id))

    # 複数の位置・候補に読める場合は、詩を勝手に決めず再生成へ戻す。
    if len(suggestions) != 1:
        return None
    start, end, replacement, atom_id = suggestions.pop()
    # 訂正対象だけをひらがなの正しい表記へ置き換え、ほかのカタカナ表現は
    # 勝手に書き換えない。空白は発句の通常形に合わせて除く。
    corrected = compact[:start] + replacement + compact[end:]
    return CatalogKanaCorrection(
        original=text,
        corrected=corrected,
        source_atom_id=atom_id,
    )


def _hiragana(text: str) -> str:
    chars: list[str] = []
    for char in str(text or ""):
        code = ord(char)
        if 0x30A1 <= code <= 0x30F6:
            chars.append(chr(code - 0x60))
        else:
            chars.append(char)
    return "".join(chars)
