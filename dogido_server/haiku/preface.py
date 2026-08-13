"""川柳の見どころ節を、発話前に一次 source atom へ照合する。"""

from __future__ import annotations

from typing import Any

from dogido_server.llm import LLMFrontend, StructuredGenerationRequest
from dogido_server.llm.client import STRUCTURED_STATUS_KEY

from .source_atoms import HaikuSourceAtom, PrefaceClause


def validate_preface_clauses(
    llm: LLMFrontend | None,
    *,
    clauses: tuple[PrefaceClause, ...],
    source_atoms: tuple[HaikuSourceAtom, ...],
    max_tokens: int | None,
) -> bool:
    """別のstructured評価が全節の根拠・分類・主張範囲を確認した時だけ通す。

    scene生成AIの自己申告を採否の根拠にせず、評価結果の形・ID・真偽はコードで
    fail-closedに検査する。一次atom以外をbasisにする循環参照も許さない。
    """

    if llm is None or not clauses:
        return False
    atom_by_id = {atom.atom_id: atom for atom in source_atoms if not atom.basis_atom_ids}
    if not atom_by_id:
        return False
    details = {
        "preface_clauses": [clause.to_dict() for clause in clauses],
        "source_atoms": [atom.to_prompt_dict() for atom in atom_by_id.values()],
    }
    payload = llm.generate_structured_json(
        StructuredGenerationRequest(
            kind="haiku_preface_grounding",
            fallback_value={"assessments": []},
            details=details,
            temperature=0.0,
            route="chat",
            max_tokens=max_tokens,
        )
    )
    if not isinstance(payload, dict) or str(payload.get(STRUCTURED_STATUS_KEY) or "accepted") != "accepted":
        return False
    rows = payload.get("assessments")
    if not isinstance(rows, list) or len(rows) != len(clauses):
        return False

    seen: set[int] = set()
    for row in rows:
        if not isinstance(row, dict):
            return False
        index = row.get("clause_index")
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or not 0 <= index < len(clauses)
            or index in seen
        ):
            return False
        seen.add(index)
        clause = clauses[index]
        raw_ids = row.get("basis_atom_ids")
        if (
            not isinstance(raw_ids, list)
            or tuple(raw_ids) != clause.basis_atom_ids
            or row.get("claim_class") != clause.claim_class
            or row.get("meaning_retained") is not True
            or row.get("class_correct") is not True
            or row.get("within_claim_scope") is not True
            or row.get("natural_japanese") is not True
        ):
            return False
    return seen == set(range(len(clauses)))
