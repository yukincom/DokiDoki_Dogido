"""出典つき川柳の生成・行単位検証・限定再生成。

意味の近さは LLM に判定させるが、採否、source atom の重複、再試行回数は
必ずコードで決める。壊れた句を workshop に渡して直させる経路は持たない。
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from dogido_server.llm import LLMFrontend, StructuredGenerationRequest
from dogido_server.llm.client import STRUCTURED_STATUS_KEY
from dogido_server.llm.haiku import is_haiku_line_usable
from dogido_server.llm.sanitize import summarize_for_log

from .source_atoms import HaikuSourceAtom

LOGGER = logging.getLogger("uvicorn.error")

INITIAL_TEMPERATURE = 0.60
REGENERATION_TEMPERATURE = 0.30
GROUNDING_TEMPERATURE = 0.0
MAX_REGENERATION_ROUNDS = 2


@dataclass(frozen=True, slots=True)
class GroundedHaikuResult:
    text: str
    accepted: bool
    line_sources: tuple[dict[str, object], ...] = ()
    failure_reason: str | None = None


@dataclass(frozen=True, slots=True)
class _LineAssessment:
    line_index: int
    atom_ids: tuple[str, ...]
    meaning_retained: bool
    natural_japanese: bool


def generate_grounded_haiku(
    llm: LLMFrontend | None,
    *,
    details: dict[str, object],
    source_atoms: tuple[HaikuSourceAtom, ...],
    fallback_text: str,
    max_tokens: int | None,
) -> GroundedHaikuResult:
    """三行を生成し、合格行を固定したまま不合格行だけを最大2回直す。"""

    if llm is None:
        return _failed(fallback_text, "llm_unavailable")
    if len(source_atoms) < 3:
        # 同じ材料を三行へ水増ししない。観測材料自体が薄い場合は静かに閉じる。
        return _failed(fallback_text, "insufficient_source_atoms")

    atom_by_id = {atom.atom_id: atom for atom in source_atoms}
    prompt_details = dict(details)
    prompt_details["source_atoms"] = [atom.to_prompt_dict() for atom in source_atoms]

    draft_payload = llm.generate_structured_json(
        StructuredGenerationRequest(
            kind="haiku_draft",
            fallback_value={"lines": []},
            details=prompt_details,
            temperature=INITIAL_TEMPERATURE,
            route="haiku",
            max_tokens=max_tokens,
        )
    )
    lines = _draft_lines(draft_payload)
    if lines is None:
        return _failed(fallback_text, "invalid_draft")

    accepted: dict[int, _LineAssessment] = {}
    failed_indices = {0, 1, 2}
    for round_index in range(MAX_REGENERATION_ROUNDS + 1):
        used_atom_ids = {
            atom_id
            for assessment in accepted.values()
            for atom_id in assessment.atom_ids
        }
        eligible_atoms = tuple(atom for atom in source_atoms if atom.atom_id not in used_atom_ids)
        assessments = _assess_lines(
            llm,
            details=prompt_details,
            lines=lines,
            line_indices=failed_indices,
            eligible_atoms=eligible_atoms,
            max_tokens=max_tokens,
        )
        newly_accepted, failed_indices = _accept_lines(
            lines,
            line_indices=failed_indices,
            assessments=assessments,
            details=details,
            already_used=used_atom_ids,
            frozen_lines={lines[index] for index in accepted},
        )
        accepted.update(newly_accepted)

        if not failed_indices:
            text = "\n".join(lines)
            return GroundedHaikuResult(
                text=text,
                accepted=True,
                line_sources=_line_source_records(lines, accepted, atom_by_id),
            )
        if round_index >= MAX_REGENERATION_ROUNDS:
            break

        used_atom_ids = {
            atom_id
            for assessment in accepted.values()
            for atom_id in assessment.atom_ids
        }
        remaining_atoms = tuple(atom for atom in source_atoms if atom.atom_id not in used_atom_ids)
        if len(remaining_atoms) < len(failed_indices):
            return _failed(fallback_text, "insufficient_unused_atoms")

        regenerated = _regenerate_failed_lines(
            llm,
            details=prompt_details,
            lines=lines,
            failed_indices=failed_indices,
            remaining_atoms=remaining_atoms,
            max_tokens=max_tokens,
        )
        # 合格行には一切触れない。不足した返答も一試行として数える。
        for line_index, text in regenerated.items():
            if line_index in failed_indices:
                lines[line_index] = text

    LOGGER.warning(
        "haiku_grounding result=fallback reason=max_regeneration_rounds lines=%s failed=%s",
        summarize_for_log(" / ".join(lines)),
        sorted(failed_indices),
    )
    return _failed(fallback_text, "max_regeneration_rounds")


def _draft_lines(payload: dict[str, Any] | None) -> list[str] | None:
    if not _structured_accepted(payload):
        return None
    raw_lines = payload.get("lines") if isinstance(payload, dict) else None
    if not isinstance(raw_lines, list) or len(raw_lines) != 3:
        return None
    lines = [_clean_single_line(value) for value in raw_lines]
    if any(not line for line in lines):
        return None
    return lines


def _assess_lines(
    llm: LLMFrontend,
    *,
    details: dict[str, object],
    lines: list[str],
    line_indices: set[int],
    eligible_atoms: tuple[HaikuSourceAtom, ...],
    max_tokens: int | None,
) -> dict[int, _LineAssessment]:
    request_details = dict(details)
    request_details["grounding_lines"] = [
        {"line_index": index, "text": lines[index]}
        for index in sorted(line_indices)
    ]
    request_details["source_atoms"] = [atom.to_prompt_dict() for atom in eligible_atoms]
    payload = llm.generate_structured_json(
        StructuredGenerationRequest(
            kind="haiku_line_grounding",
            fallback_value={"assessments": []},
            details=request_details,
            temperature=GROUNDING_TEMPERATURE,
            route="chat",
            max_tokens=max_tokens,
        )
    )
    if not _structured_accepted(payload):
        return {}
    raw_assessments = payload.get("assessments") if isinstance(payload, dict) else None
    if not isinstance(raw_assessments, list):
        return {}

    eligible_ids = {atom.atom_id for atom in eligible_atoms}
    assessments: dict[int, _LineAssessment] = {}
    for raw in raw_assessments:
        if not isinstance(raw, dict):
            continue
        line_index = raw.get("line_index")
        if not isinstance(line_index, int) or isinstance(line_index, bool):
            continue
        if line_index not in line_indices or line_index in assessments:
            continue
        raw_ids = raw.get("atom_ids")
        if not isinstance(raw_ids, list):
            continue
        if not raw_ids or any(not isinstance(value, str) or not value for value in raw_ids):
            continue
        atom_ids = tuple(raw_ids)
        # LLM が材料 ID を捏造した評価は、不合格として閉じる。
        if len(set(atom_ids)) != len(atom_ids) or any(atom_id not in eligible_ids for atom_id in atom_ids):
            continue
        assessments[line_index] = _LineAssessment(
            line_index=line_index,
            atom_ids=atom_ids,
            meaning_retained=raw.get("meaning_retained") is True,
            natural_japanese=raw.get("natural_japanese") is True,
        )
    return assessments


def _accept_lines(
    lines: list[str],
    *,
    line_indices: set[int],
    assessments: dict[int, _LineAssessment],
    details: dict[str, object],
    already_used: set[str],
    frozen_lines: set[str],
) -> tuple[dict[int, _LineAssessment], set[int]]:
    accepted: dict[int, _LineAssessment] = {}
    failed: set[int] = set()
    used = set(already_used)
    seen_lines = set(frozen_lines)
    for line_index in sorted(line_indices):
        line = lines[line_index]
        assessment = assessments.get(line_index)
        valid = (
            assessment is not None
            and assessment.meaning_retained
            and assessment.natural_japanese
            and is_haiku_line_usable(line, line_index, details)
            and line not in seen_lines
            and not used.intersection(assessment.atom_ids)
        )
        if not valid:
            failed.add(line_index)
            continue
        # 行順で先に合格した材料を予約し、後ろの重複行を再生成へ回す。
        accepted[line_index] = assessment
        used.update(assessment.atom_ids)
        seen_lines.add(line)
    return accepted, failed


def _regenerate_failed_lines(
    llm: LLMFrontend,
    *,
    details: dict[str, object],
    lines: list[str],
    failed_indices: set[int],
    remaining_atoms: tuple[HaikuSourceAtom, ...],
    max_tokens: int | None,
) -> dict[int, str]:
    request_details = dict(details)
    request_details["current_lines"] = [
        {"line_index": index, "text": text, "frozen": index not in failed_indices}
        for index, text in enumerate(lines)
    ]
    request_details["failed_line_indices"] = sorted(failed_indices)
    request_details["source_atoms"] = [atom.to_prompt_dict() for atom in remaining_atoms]
    payload = llm.generate_structured_json(
        StructuredGenerationRequest(
            kind="haiku_line_regeneration",
            fallback_value={"lines": []},
            details=request_details,
            temperature=REGENERATION_TEMPERATURE,
            route="haiku",
            max_tokens=max_tokens,
        )
    )
    if not _structured_accepted(payload):
        return {}
    rows = payload.get("lines") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {}
    regenerated: dict[int, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            return {}
        line_index = row.get("line_index")
        if not isinstance(line_index, int) or isinstance(line_index, bool):
            return {}
        text = _clean_single_line(row.get("text"))
        # 対象外・重複・欠損が一つでもあれば、そのroundの返答全体を採用しない。
        if line_index not in failed_indices or not text or line_index in regenerated:
            return {}
        regenerated[line_index] = text
    if set(regenerated) != failed_indices:
        return {}
    return regenerated


def _line_source_records(
    lines: list[str],
    assessments: dict[int, _LineAssessment],
    atom_by_id: dict[str, HaikuSourceAtom],
) -> tuple[dict[str, object], ...]:
    records: list[dict[str, object]] = []
    for line_index in range(3):
        assessment = assessments[line_index]
        records.append(
            {
                "line_index": line_index,
                "text": lines[line_index],
                "atom_ids": list(assessment.atom_ids),
                "sources": [atom_by_id[atom_id].to_prompt_dict() for atom_id in assessment.atom_ids],
            }
        )
    return tuple(records)


def _clean_single_line(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip().strip("「」\"' ")
    # JSON の一要素から複数行を差し込ませない。
    if not text or "\n" in text or "\r" in text:
        return ""
    return text


def _structured_accepted(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    status = str(payload.get(STRUCTURED_STATUS_KEY) or "accepted")
    return status == "accepted"


def _failed(fallback_text: str, reason: str) -> GroundedHaikuResult:
    LOGGER.warning("haiku_grounding result=fallback reason=%s", reason)
    return GroundedHaikuResult(
        text=fallback_text,
        accepted=False,
        failure_reason=reason,
    )
