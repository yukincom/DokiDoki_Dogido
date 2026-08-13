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
from dogido_server.llm.haiku import count_japanese_sounds, is_haiku_line_usable
from dogido_server.llm.sanitize import summarize_for_log

from .lexical_correction import correct_grounded_catalog_kana
from .source_atoms import HaikuSourceAtom

LOGGER = logging.getLogger("uvicorn.error")

INITIAL_TEMPERATURE = 0.60
REGENERATION_TEMPERATURE = 0.30
GROUNDING_TEMPERATURE = 0.0
DEFAULT_MAX_REGENERATION_ROUNDS = 6
MAX_REGENERATION_ROUNDS_LIMIT = 8

GENERATION_SLOT_GROUPS: dict[str, tuple[tuple[int, ...], ...]] = {
    # 一句全体を一単位として扱う。どこか一行が落ちれば三行とも作り直す。
    "whole_poem": ((0, 1, 2),),
    # 現行に近い方式。合格した行は個別に固定する。
    "three_slot": ((0,), (1,), (2,)),
    # 上五で入り、下二行を一まとまりとして展開する。
    "one_plus_two": ((0,), (1, 2)),
    # 上二行で場面を作り、下五を独立した着地にする。
    "two_plus_one": ((0, 1), (2,)),
}


@dataclass(frozen=True, slots=True)
class GroundedHaikuResult:
    text: str
    accepted: bool
    line_sources: tuple[dict[str, object], ...] = ()
    failure_reason: str | None = None
    generation_strategy: str = "three_slot"
    regeneration_rounds: int = 0


@dataclass(frozen=True, slots=True)
class WorkshopRevisionResult:
    text: str | None
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
    generation_strategy: str = "three_slot",
    max_regeneration_rounds: int = DEFAULT_MAX_REGENERATION_ROUNDS,
) -> GroundedHaikuResult:
    """共通検査を通し、選択中のスロット単位で不合格箇所だけを直す。"""

    slot_groups = GENERATION_SLOT_GROUPS.get(generation_strategy)
    if slot_groups is None:
        return _failed(
            fallback_text,
            "invalid_generation_strategy",
            generation_strategy=generation_strategy,
        )
    regeneration_limit = max(
        0,
        min(int(max_regeneration_rounds), MAX_REGENERATION_ROUNDS_LIMIT),
    )

    if llm is None:
        return _failed(
            fallback_text,
            "llm_unavailable",
            generation_strategy=generation_strategy,
        )
    if len(source_atoms) < 3:
        # 同じ材料を三行へ水増ししない。観測材料自体が薄い場合は静かに閉じる。
        return _failed(
            fallback_text,
            "insufficient_source_atoms",
            generation_strategy=generation_strategy,
        )

    atom_by_id = {atom.atom_id: atom for atom in source_atoms}
    prompt_details = dict(details)
    prompt_details["source_atoms"] = [atom.to_prompt_dict() for atom in source_atoms]
    prompt_details["generation_strategy"] = generation_strategy
    prompt_details["generation_slot_groups"] = [list(group) for group in slot_groups]

    LOGGER.warning(
        "haiku_grounding result=start strategy=%s max_regeneration_rounds=%s slots=%s",
        generation_strategy,
        regeneration_limit,
        [list(group) for group in slot_groups],
    )

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
        return _failed(
            fallback_text,
            "invalid_draft",
            generation_strategy=generation_strategy,
        )

    accepted: dict[int, _LineAssessment] = {}
    failed_indices = {0, 1, 2}
    for round_index in range(regeneration_limit + 1):
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
        tentatively_accepted, line_failures = _accept_lines(
            lines,
            line_indices=failed_indices,
            assessments=assessments,
            details=details,
            atom_by_id=atom_by_id,
            already_used=used_atom_ids,
            frozen_lines={lines[index] for index in accepted},
        )
        # 一行だけが落ちても、その行と意味展開を共有するスロット全体を直す。
        # これにより4方式は検査条件を変えず、探索単位だけを比較できる。
        failed_indices = _expand_failed_slot_indices(line_failures, slot_groups)
        newly_accepted = {
            index: assessment
            for index, assessment in tentatively_accepted.items()
            if index not in failed_indices
        }
        accepted.update(newly_accepted)

        LOGGER.warning(
            "haiku_grounding result=round strategy=%s round=%s accepted=%s failed=%s",
            generation_strategy,
            round_index,
            sorted(accepted),
            sorted(failed_indices),
        )

        if not failed_indices:
            text = "\n".join(lines)
            return GroundedHaikuResult(
                text=text,
                accepted=True,
                line_sources=_line_source_records(lines, accepted, atom_by_id),
                generation_strategy=generation_strategy,
                regeneration_rounds=round_index,
            )
        if round_index >= regeneration_limit:
            break

        used_atom_ids = {
            atom_id
            for assessment in accepted.values()
            for atom_id in assessment.atom_ids
        }
        remaining_atoms = tuple(atom for atom in source_atoms if atom.atom_id not in used_atom_ids)
        if len(remaining_atoms) < len(failed_indices):
            return _failed(
                fallback_text,
                "insufficient_unused_atoms",
                generation_strategy=generation_strategy,
                regeneration_rounds=round_index,
            )

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
        "haiku_grounding result=fallback reason=max_regeneration_rounds strategy=%s "
        "rounds=%s lines=%s failed=%s",
        generation_strategy,
        regeneration_limit,
        summarize_for_log(" / ".join(lines)),
        sorted(failed_indices),
    )
    return _failed(
        fallback_text,
        "max_regeneration_rounds",
        generation_strategy=generation_strategy,
        regeneration_rounds=regeneration_limit,
    )


def generate_workshop_revision(
    llm: LLMFrontend | None,
    *,
    original_text: str,
    target_indices: tuple[int, ...],
    findings: tuple[dict[str, object], ...],
    source_atoms: tuple[HaikuSourceAtom, ...],
    original_line_sources: dict[int, tuple[str, ...]],
    details: dict[str, object],
    max_tokens: int | None,
    max_attempts: int = 2,
) -> WorkshopRevisionResult:
    """検証済み対象行だけを haiku route で直し、提案として返す。

    元句・memory は変更しない。モデルが対象外行、未知の出典、音数外の行を
    返した場合はその案全体を採用しない。
    """

    lines = [line.strip() for line in (original_text or "").splitlines() if line.strip()]
    targets = tuple(sorted(set(target_indices)))
    if llm is None or len(lines) != 3 or not targets or any(index not in (0, 1, 2) for index in targets):
        return WorkshopRevisionResult(None, False, failure_reason="invalid_targets")
    if not source_atoms:
        return WorkshopRevisionResult(None, False, failure_reason="no_source_atoms")

    atom_by_id = {atom.atom_id: atom for atom in source_atoms}
    frozen_indices = {index for index in range(3) if index not in targets}
    # 固定行の出典が一つでも欠ける旧データでは、修正行との材料重複を証明
    # できない。推測で補わず元句を維持する。
    if any(not original_line_sources.get(index) for index in frozen_indices):
        return WorkshopRevisionResult(None, False, failure_reason="missing_frozen_line_sources")
    if any(
        atom_id not in atom_by_id
        for index in frozen_indices
        for atom_id in original_line_sources[index]
    ):
        return WorkshopRevisionResult(None, False, failure_reason="invalid_frozen_line_sources")
    reserved = {
        atom_id
        for index, atom_ids in original_line_sources.items()
        if index in frozen_indices
        for atom_id in atom_ids
    }
    eligible = tuple(atom for atom in source_atoms if atom.atom_id not in reserved)
    if len(eligible) < len(targets):
        return WorkshopRevisionResult(None, False, failure_reason="insufficient_source_atoms")

    request_details = dict(details)
    request_details.update(
        {
            "current_lines": [
                {"line_index": index, "text": text, "frozen": index not in targets}
                for index, text in enumerate(lines)
            ],
            "target_line_indices": list(targets),
            "workshop_findings": list(findings),
            "source_atoms": [atom.to_prompt_dict() for atom in eligible],
        }
    )
    for _attempt in range(max(1, max_attempts)):
        payload = llm.generate_structured_json(
            StructuredGenerationRequest(
                kind="haiku_workshop_revision",
                fallback_value={"lines": []},
                details=request_details,
                temperature=REGENERATION_TEMPERATURE,
                route="haiku",
                max_tokens=max_tokens,
            )
        )
        repaired = _validated_workshop_lines(
            payload,
            targets=targets,
            original_lines=lines,
            eligible_ids={atom.atom_id for atom in eligible},
            reserved_ids=reserved,
            details=details,
        )
        if repaired is None:
            continue
        revised = list(lines)
        for index, (text, _claimed_atom_ids) in repaired.items():
            revised[index] = text
        # 修正AIの自己申告IDだけでは、既知IDを別の意味へ付け替えられる。初回発句と
        # 同じ意味保持・自然さ評価を別のstructured呼び出しで行い、評価側が選んだ
        # atom IDを最終出典にする。
        assessments = _assess_lines(
            llm,
            details=details,
            lines=revised,
            line_indices=set(targets),
            eligible_atoms=eligible,
            max_tokens=max_tokens,
        )
        accepted, failed = _accept_lines(
            revised,
            line_indices=set(targets),
            assessments=assessments,
            details=details,
            atom_by_id=atom_by_id,
            already_used=reserved,
            frozen_lines={text for index, text in enumerate(lines) if index not in targets},
        )
        if failed or set(accepted) != set(targets):
            continue
        records: list[dict[str, object]] = []
        for index, text in enumerate(revised):
            atom_ids = (
                accepted[index].atom_ids
                if index in accepted
                else original_line_sources.get(index, ())
            )
            records.append(
                {
                    "line_index": index,
                    "text": text,
                    "atom_ids": list(atom_ids),
                    "sources": [
                        atom_by_id[atom_id].to_prompt_dict()
                        for atom_id in atom_ids
                        if atom_id in atom_by_id
                    ],
                }
            )
        return WorkshopRevisionResult(
            "\n".join(revised),
            True,
            line_sources=tuple(records),
        )
    return WorkshopRevisionResult(None, False, failure_reason="invalid_revision")


def _validated_workshop_lines(
    payload: dict[str, Any] | None,
    *,
    targets: tuple[int, ...],
    original_lines: list[str],
    eligible_ids: set[str],
    reserved_ids: set[str],
    details: dict[str, object],
) -> dict[int, tuple[str, tuple[str, ...]]] | None:
    if not _structured_accepted(payload):
        return None
    rows = payload.get("lines") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None
    result: dict[int, tuple[str, tuple[str, ...]]] = {}
    used = set(reserved_ids)
    fixed_text = {text for index, text in enumerate(original_lines) if index not in targets}
    for row in rows:
        if not isinstance(row, dict):
            return None
        index = row.get("line_index")
        text = _clean_single_line(row.get("text"))
        raw_ids = row.get("atom_ids")
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or index not in targets
            or index in result
            or not text
            or text in fixed_text
            or not isinstance(raw_ids, list)
            or not raw_ids
        ):
            return None
        atom_ids = tuple(str(value).strip() for value in raw_ids)
        if (
            any(not atom_id or atom_id not in eligible_ids for atom_id in atom_ids)
            or len(set(atom_ids)) != len(atom_ids)
            or used.intersection(atom_ids)
            or not is_haiku_line_usable(text, index, details)
        ):
            return None
        result[index] = (text, atom_ids)
        used.update(atom_ids)
        fixed_text.add(text)
    return result if set(result) == set(targets) else None


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
    assessments, reported_indices = _request_line_assessments(
        llm,
        details=details,
        lines=lines,
        line_indices=line_indices,
        eligible_atoms=eligible_atoms,
        max_tokens=max_tokens,
    )
    # 一部モデルは複数行を頼んでも先頭行だけ、または旧単体objectを返す。
    # 欠けた行だけ一行ずつ再照合し、句の再生成回数とは別に検証形式を補う。
    for line_index in sorted(line_indices - reported_indices):
        single, _reported = _request_line_assessments(
            llm,
            details=details,
            lines=lines,
            line_indices={line_index},
            eligible_atoms=eligible_atoms,
            max_tokens=max_tokens,
        )
        assessments.update(single)
    return assessments


def _request_line_assessments(
    llm: LLMFrontend,
    *,
    details: dict[str, object],
    lines: list[str],
    line_indices: set[int],
    eligible_atoms: tuple[HaikuSourceAtom, ...],
    max_tokens: int | None,
) -> tuple[dict[int, _LineAssessment], set[int]]:
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
        return {}, set()
    raw_assessments = payload.get("assessments") if isinstance(payload, dict) else None
    if not isinstance(raw_assessments, list):
        # 初期実装の単一行shapeも入力としてだけ受ける。採否条件は同じ。
        raw_assessments = [payload] if isinstance(payload, dict) and "line_index" in payload else []

    eligible_ids = {atom.atom_id for atom in eligible_atoms}
    assessments: dict[int, _LineAssessment] = {}
    reported_indices: set[int] = set()
    for raw in raw_assessments:
        if not isinstance(raw, dict):
            continue
        line_index = raw.get("line_index")
        if not isinstance(line_index, int) or isinstance(line_index, bool):
            continue
        if line_index not in line_indices or line_index in assessments:
            continue
        reported_indices.add(line_index)
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
    return assessments, reported_indices


def _accept_lines(
    lines: list[str],
    *,
    line_indices: set[int],
    assessments: dict[int, _LineAssessment],
    details: dict[str, object],
    atom_by_id: dict[str, HaikuSourceAtom],
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
        # 意味保持が確認された catalog_label だけを使い、一意な一字誤りを
        # 決定的に直す。ラベル全文（例: 「階段」）の出現は要求しない。
        correction = (
            correct_grounded_catalog_kana(
                line,
                atom_ids=assessment.atom_ids,
                atom_by_id=atom_by_id,
            )
            if assessment is not None and assessment.meaning_retained
            else None
        )
        if correction is not None:
            line = correction.corrected
            lines[line_index] = line
            LOGGER.warning(
                "haiku_catalog_kana_corrected line_index=%s from=%s to=%s atom_id=%s",
                line_index,
                summarize_for_log(correction.original),
                summarize_for_log(correction.corrected),
                correction.source_atom_id,
            )
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


def _expand_failed_slot_indices(
    failed_line_indices: set[int],
    slot_groups: tuple[tuple[int, ...], ...],
) -> set[int]:
    """一行の不合格を、その行が属する生成スロット全体へ広げる。"""

    return {
        line_index
        for group in slot_groups
        if failed_line_indices.intersection(group)
        for line_index in group
    }


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
    current_lines: list[dict[str, object]] = []
    for index, text in enumerate(lines):
        row: dict[str, object] = {
            "line_index": index,
            "text": text,
            "frozen": index not in failed_indices,
        }
        if index in failed_indices:
            # モデル自身の音数申告には頼らず、採否と同じコード計数を再生成へ返す。
            target = (5, 7, 5)[index]
            sound_count = count_japanese_sounds(text)
            row.update(
                {
                    "sound_count": sound_count,
                    "target_sound_count": target,
                    "allowed_sound_min": target - 1,
                    "allowed_sound_max": target + 1,
                    "meter_status": (
                        "too_short"
                        if sound_count < target - 1
                        else "too_long"
                        if sound_count > target + 1
                        else "within_range"
                    ),
                }
            )
        current_lines.append(row)
    request_details["current_lines"] = current_lines
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


def _failed(
    fallback_text: str,
    reason: str,
    *,
    generation_strategy: str = "three_slot",
    regeneration_rounds: int = 0,
) -> GroundedHaikuResult:
    LOGGER.warning(
        "haiku_grounding result=fallback reason=%s strategy=%s rounds=%s",
        reason,
        generation_strategy,
        regeneration_rounds,
    )
    return GroundedHaikuResult(
        text=fallback_text,
        accepted=False,
        failure_reason=reason,
        generation_strategy=generation_strategy,
        regeneration_rounds=regeneration_rounds,
    )
