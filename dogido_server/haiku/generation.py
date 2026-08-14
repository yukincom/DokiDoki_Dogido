"""出典つき川柳の生成・行単位検証・限定再生成。

意味の近さは LLM に判定させるが、採否、source atom の重複、再試行回数は
必ずコードで決める。壊れた句を workshop に渡して直させる経路は持たない。
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any
import unicodedata

from dogido_server.llm import LLMFrontend, StructuredGenerationRequest
from dogido_server.llm.client import STRUCTURED_STATUS_KEY
from dogido_server.llm.haiku import (
    count_japanese_sounds,
    haiku_line_failure_reasons,
)
from dogido_server.llm.sanitize import summarize_for_log
from dogido_server.tts_reading import hiraganize_japanese_text

from .edit_contract import LINE_EDIT_CONTRACT_VERSION
from .lexical_correction import correct_grounded_catalog_kana
from .source_atoms import HaikuSourceAtom

LOGGER = logging.getLogger("uvicorn.error")

INITIAL_TEMPERATURE = 0.60
REGENERATION_TEMPERATURE = 0.30
GROUNDING_TEMPERATURE = 0.0
DEFAULT_MAX_REGENERATION_ROUNDS = 6
MAX_REGENERATION_ROUNDS_LIMIT = 8
GENERATION_PROMPT_VARIANT = "source_atoms_slots_v2_kana_normalize"

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
    prompt_variant: str = GENERATION_PROMPT_VARIANT


@dataclass(frozen=True, slots=True)
class WorkshopLineEdit:
    """検証済みの一行差分。expected_text は適用先の比較条件でもある。"""

    line_index: int
    expected_text: str
    replacement_text: str
    atom_ids: tuple[str, ...]

    def to_record(self) -> dict[str, object]:
        return {
            "line_index": self.line_index,
            "expected_text": self.expected_text,
            "replacement_text": self.replacement_text,
            "atom_ids": list(self.atom_ids),
        }


@dataclass(frozen=True, slots=True)
class WorkshopRevisionResult:
    text: str | None
    accepted: bool
    line_sources: tuple[dict[str, object], ...] = ()
    failure_reason: str | None = None
    base_text: str | None = None
    edits: tuple[WorkshopLineEdit, ...] = ()
    edit_contract: str | None = None


@dataclass(frozen=True, slots=True)
class _LineAssessment:
    line_index: int
    atom_ids: tuple[str, ...]
    meaning_retained: bool
    natural_japanese: bool


@dataclass(frozen=True, slots=True)
class _WorkshopEditValidation:
    """編集AIの差分を、意味評価へ渡す前にコードで検査した結果。"""

    edits: dict[int, WorkshopLineEdit] | None
    line_failures: dict[int, tuple[str, ...]]
    global_failures: tuple[str, ...] = ()
    replacements: tuple[dict[str, object], ...] = ()
    candidate_signature: tuple[tuple[int, str], ...] | None = None


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
    available_claim_ids = {
        claim_id
        for atom in source_atoms
        for claim_id in _atom_reservation_ids(atom)
    }
    if len(available_claim_ids) < 3:
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
    _normalize_candidate_lines(lines, stage="draft")

    accepted: dict[int, _LineAssessment] = {}
    failed_indices = {0, 1, 2}
    # モデルへ戻す前に同一・表記差だけの候補を落とす。全行・全roundで共有し、
    # 別行への横流しも「新しい案」と数えない。
    candidate_signatures = {_candidate_signature(line) for line in lines}
    forced_failures = _duplicate_line_failures(lines)
    for round_index in range(regeneration_limit + 1):
        used_claim_ids = {
            claim_id
            for assessment in accepted.values()
            for claim_id in _reservation_ids(assessment.atom_ids, atom_by_id)
        }
        eligible_atoms = tuple(
            atom
            for atom in source_atoms
            if not used_claim_ids.intersection(_atom_reservation_ids(atom))
        )
        assessment_indices = failed_indices - set(forced_failures)
        assessments = (
            _assess_lines(
                llm,
                details=prompt_details,
                lines=lines,
                line_indices=assessment_indices,
                eligible_atoms=eligible_atoms,
                max_tokens=max_tokens,
            )
            if assessment_indices
            else {}
        )
        tentatively_accepted, line_failures = _accept_lines(
            lines,
            line_indices=assessment_indices,
            assessments=assessments,
            details=details,
            atom_by_id=atom_by_id,
            already_used=used_claim_ids,
            frozen_lines={lines[index] for index in accepted},
        )
        line_failures.update(forced_failures)
        # 一行だけが落ちても、その行と意味展開を共有するスロット全体を直す。
        # これにより4方式は検査条件を変えず、探索単位だけを比較できる。
        expanded_failures = _expand_failure_reasons(line_failures, slot_groups)
        failed_indices = set(expanded_failures)
        newly_accepted = {
            index: assessment
            for index, assessment in tentatively_accepted.items()
            if index not in failed_indices
        }
        accepted.update(newly_accepted)

        LOGGER.warning(
            "haiku_grounding result=round strategy=%s round=%s accepted=%s failed=%s reasons=%s",
            generation_strategy,
            round_index,
            sorted(accepted),
            sorted(failed_indices),
            {index: list(reasons) for index, reasons in sorted(expanded_failures.items())},
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

        used_claim_ids = {
            claim_id
            for assessment in accepted.values()
            for claim_id in _reservation_ids(assessment.atom_ids, atom_by_id)
        }
        remaining_atoms = tuple(
            atom
            for atom in source_atoms
            if not used_claim_ids.intersection(_atom_reservation_ids(atom))
        )
        remaining_claim_ids = {
            claim_id
            for atom in remaining_atoms
            for claim_id in _atom_reservation_ids(atom)
        }
        if len(remaining_claim_ids) < len(failed_indices):
            return _failed(
                fallback_text,
                "insufficient_unused_atoms",
                generation_strategy=generation_strategy,
                regeneration_rounds=round_index,
            )

        # 一意なカタログ名かな訂正後の字面も、既出候補として履歴へ反映する。
        candidate_signatures.update(_candidate_signature(line) for line in lines)
        regenerated = _regenerate_failed_lines(
            llm,
            details=prompt_details,
            lines=lines,
            failed_indices=failed_indices,
            failure_reasons=expanded_failures,
            remaining_atoms=remaining_atoms,
            max_tokens=max_tokens,
        )
        forced_failures = {}
        # 合格行には一切触れない。不足・既出候補も一試行として数えるが、
        # grounding AI へは回さず次の再生成理由として即時返却する。
        for line_index in failed_indices:
            text = regenerated.get(line_index)
            if text is None:
                forced_failures[line_index] = ("missing_regenerated_line",)
                continue
            text = _normalize_candidate_kana(
                text,
                line_index=line_index,
                stage=f"regeneration_{round_index + 1}",
            )
            signature = _candidate_signature(text)
            if not signature or signature in candidate_signatures:
                forced_failures[line_index] = ("duplicate_candidate",)
                LOGGER.warning(
                    "haiku_grounding result=duplicate_candidate round=%s line_index=%s text=%s",
                    round_index + 1,
                    line_index,
                    summarize_for_log(text),
                )
                continue
            candidate_signatures.add(signature)
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
    reserved_claim_ids = _reservation_ids(reserved, atom_by_id)
    eligible = tuple(
        atom
        for atom in source_atoms
        if not reserved_claim_ids.intersection(_atom_reservation_ids(atom))
    )
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
            # Editor の出力を曖昧な全文ではなく、元行との比較条件つき差分にする。
            # 旧 {line_index, text} 応答は受け付けず fail-closed にする。
            "edit_contract": LINE_EDIT_CONTRACT_VERSION,
        }
    )
    seen_candidate_signatures: set[tuple[tuple[int, str], ...]] = set()
    rejected_replacements: list[dict[str, object]] = []
    last_failures: dict[int, tuple[str, ...]] = {}
    last_global_failures: tuple[str, ...] = ()
    for attempt in range(max(1, max_attempts)):
        payload = llm.generate_structured_json(
            StructuredGenerationRequest(
                kind="haiku_workshop_revision",
                fallback_value={"lines": []},
                # 次roundで feedback を差し替えても、この試行の監査snapshotは変えない。
                details=dict(request_details),
                temperature=REGENERATION_TEMPERATURE,
                route="haiku",
                max_tokens=max_tokens,
            )
        )
        validation = _validate_workshop_lines(
            payload,
            targets=targets,
            original_lines=lines,
            eligible_ids={atom.atom_id for atom in eligible},
            reserved_ids=reserved,
            details=details,
        )
        if validation.edits is None:
            last_failures = validation.line_failures
            last_global_failures = validation.global_failures
            rejected_replacements.extend(validation.replacements)
            _set_workshop_retry_feedback(
                request_details,
                attempt=attempt + 1,
                line_failures=last_failures,
                global_failures=last_global_failures,
                rejected_replacements=rejected_replacements,
            )
            LOGGER.warning(
                "haiku_workshop_revision result=retry attempt=%s stage=edit_contract "
                "global=%s lines=%s",
                attempt + 1,
                list(last_global_failures),
                {index: list(reasons) for index, reasons in sorted(last_failures.items())},
            )
            continue
        repaired = validation.edits
        candidate_signature = validation.candidate_signature
        if candidate_signature is None or candidate_signature in seen_candidate_signatures:
            last_failures = {index: ("duplicate_candidate",) for index in targets}
            last_global_failures = ()
            rejected_replacements.extend(validation.replacements)
            _set_workshop_retry_feedback(
                request_details,
                attempt=attempt + 1,
                line_failures=last_failures,
                global_failures=last_global_failures,
                rejected_replacements=rejected_replacements,
            )
            LOGGER.warning(
                "haiku_workshop_revision result=retry attempt=%s stage=duplicate_candidate",
                attempt + 1,
            )
            continue
        seen_candidate_signatures.add(candidate_signature)
        revised = list(lines)
        for index, edit in repaired.items():
            revised[index] = edit.replacement_text
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
            already_used=reserved_claim_ids,
            frozen_lines={text for index, text in enumerate(lines) if index not in targets},
        )
        if failed or set(accepted) != set(targets):
            last_failures = dict(failed)
            for index in set(targets) - set(accepted) - set(last_failures):
                last_failures[index] = ("grounding_missing",)
            last_global_failures = ()
            rejected_replacements.extend(validation.replacements)
            _set_workshop_retry_feedback(
                request_details,
                attempt=attempt + 1,
                line_failures=last_failures,
                global_failures=last_global_failures,
                rejected_replacements=rejected_replacements,
            )
            LOGGER.warning(
                "haiku_workshop_revision result=retry attempt=%s stage=grounding lines=%s",
                attempt + 1,
                {index: list(reasons) for index, reasons in sorted(last_failures.items())},
            )
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
        verified_edits = tuple(
            WorkshopLineEdit(
                line_index=index,
                expected_text=lines[index],
                replacement_text=revised[index],
                atom_ids=accepted[index].atom_ids,
            )
            for index in sorted(targets)
        )
        return WorkshopRevisionResult(
            "\n".join(revised),
            True,
            line_sources=tuple(records),
            base_text="\n".join(lines),
            edits=verified_edits,
            edit_contract=LINE_EDIT_CONTRACT_VERSION,
        )
    LOGGER.warning(
        "haiku_workshop_revision result=fallback reason=max_attempts global=%s lines=%s",
        list(last_global_failures),
        {index: list(reasons) for index, reasons in sorted(last_failures.items())},
    )
    return WorkshopRevisionResult(None, False, failure_reason="invalid_revision")


def _validate_workshop_lines(
    payload: dict[str, Any] | None,
    *,
    targets: tuple[int, ...],
    original_lines: list[str],
    eligible_ids: set[str],
    reserved_ids: set[str],
    details: dict[str, object],
) -> _WorkshopEditValidation:
    if not _structured_accepted(payload):
        return _WorkshopEditValidation(None, {}, ("structured_rejected",))
    rows = payload.get("lines") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return _WorkshopEditValidation(None, {}, ("invalid_edit_rows",))
    result: dict[int, WorkshopLineEdit] = {}
    line_failures: dict[int, tuple[str, ...]] = {}
    global_failures: list[str] = []
    replacements: list[dict[str, object]] = []
    replacement_signatures: dict[int, str] = {}
    used = set(reserved_ids)
    fixed_signatures = {
        _candidate_signature(text)
        for index, text in enumerate(original_lines)
        if index not in targets
    }
    seen_line_signatures = set(fixed_signatures)
    for row in rows:
        if not isinstance(row, dict):
            global_failures.append("invalid_edit_row")
            continue
        index = row.get("line_index")
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or index not in targets
        ):
            global_failures.append("unexpected_line_index")
            continue
        expected_text = _clean_single_line(row.get("expected_text"))
        replacement_text = _clean_single_line(row.get("replacement_text"))
        if replacement_text:
            replacement_text = _normalize_candidate_kana(
                replacement_text,
                line_index=index,
                stage="workshop_revision",
            )
        raw_ids = row.get("atom_ids")
        if replacement_text:
            replacements.append(
                {"line_index": index, "replacement_text": replacement_text}
            )
            replacement_signatures[index] = _candidate_signature(replacement_text)
        reasons: list[str] = []
        if index in result or index in line_failures:
            reasons.append("duplicate_target_edit")
        if expected_text != original_lines[index]:
            reasons.append("expected_text_mismatch")
        if not replacement_text:
            reasons.append("empty_replacement")
        else:
            replacement_signature = _candidate_signature(replacement_text)
            if replacement_signature == _candidate_signature(expected_text):
                reasons.append("unchanged_replacement")
            if replacement_signature in fixed_signatures:
                reasons.append("duplicate_fixed_line")
            elif replacement_signature in seen_line_signatures:
                reasons.append("duplicate_line")
        atom_ids: tuple[str, ...] = ()
        if (
            not isinstance(raw_ids, list)
            or not raw_ids
            or any(not isinstance(value, str) or not value.strip() for value in raw_ids)
        ):
            reasons.append("invalid_atom_ids")
        else:
            atom_ids = tuple(value.strip() for value in raw_ids)
            if any(atom_id not in eligible_ids for atom_id in atom_ids):
                reasons.append("unknown_atom_id")
            if len(set(atom_ids)) != len(atom_ids):
                reasons.append("duplicate_atom_id")
            if used.intersection(atom_ids):
                reasons.append("source_reused")
        if replacement_text:
            reasons.extend(haiku_line_failure_reasons(replacement_text, index, details))
        if reasons:
            line_failures[index] = tuple(dict.fromkeys(reasons))
            continue
        result[index] = WorkshopLineEdit(
            line_index=index,
            expected_text=expected_text,
            replacement_text=replacement_text,
            atom_ids=atom_ids,
        )
        used.update(atom_ids)
        seen_line_signatures.add(_candidate_signature(replacement_text))
    for missing_index in set(targets) - set(result) - set(line_failures):
        line_failures[missing_index] = ("missing_target_edit",)
    candidate_signature = (
        tuple(
            (index, replacement_signatures[index])
            for index in targets
        )
        if set(replacement_signatures) == set(targets)
        and all(replacement_signatures.values())
        else None
    )
    failed = bool(global_failures or line_failures or set(result) != set(targets))
    return _WorkshopEditValidation(
        None if failed else result,
        line_failures,
        tuple(dict.fromkeys(global_failures)),
        tuple(replacements),
        candidate_signature,
    )


def _set_workshop_retry_feedback(
    details: dict[str, object],
    *,
    attempt: int,
    line_failures: dict[int, tuple[str, ...]],
    global_failures: tuple[str, ...],
    rejected_replacements: list[dict[str, object]],
) -> None:
    """次の editor 呼び出しへ、コードで確定した失敗だけを閉じた型で渡す。"""

    details["edit_retry_feedback"] = {
        "attempt": attempt,
        "global_failure_reasons": list(global_failures),
        "line_failures": [
            {"line_index": index, "failure_reasons": list(reasons)}
            for index, reasons in sorted(line_failures.items())
        ],
    }
    # 直前の不合格案を再び「新案」として返させない。最大試行数が小さいため
    # 全履歴を保持しても prompt は肥大しない。
    details["rejected_replacements"] = list(rejected_replacements)


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
    # 一部モデルは複数行を頼んでも assessments の一部だけを返す。
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
        # 単一行objectだった旧契約は受けない。新契約の配列が無ければ欠落扱い。
        raw_assessments = []

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
) -> tuple[dict[int, _LineAssessment], dict[int, tuple[str, ...]]]:
    accepted: dict[int, _LineAssessment] = {}
    failed: dict[int, tuple[str, ...]] = {}
    used = set(already_used)
    seen_lines = {_candidate_signature(line) for line in frozen_lines}
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
        reasons: list[str] = []
        if assessment is None:
            reasons.append("grounding_missing")
        else:
            if not assessment.meaning_retained:
                reasons.append("meaning_not_retained")
            if not assessment.natural_japanese:
                reasons.append("unnatural_japanese")
            if used.intersection(_reservation_ids(assessment.atom_ids, atom_by_id)):
                reasons.append("source_reused")
        reasons.extend(haiku_line_failure_reasons(line, line_index, details))
        signature = _candidate_signature(line)
        if not signature or signature in seen_lines:
            reasons.append("duplicate_line")
        if reasons:
            failed[line_index] = tuple(dict.fromkeys(reasons))
            continue
        # 行順で先に合格した材料を予約し、後ろの重複行を再生成へ回す。
        accepted[line_index] = assessment
        used.update(_reservation_ids(assessment.atom_ids, atom_by_id))
        seen_lines.add(signature)
    return accepted, failed


def _expand_failure_reasons(
    failures: dict[int, tuple[str, ...]],
    slot_groups: tuple[tuple[int, ...], ...],
) -> dict[int, tuple[str, ...]]:
    """一行の不合格を、その行が属する生成スロット全体へ広げる。"""

    expanded: dict[int, tuple[str, ...]] = {}
    for group in slot_groups:
        if not set(group).intersection(failures):
            continue
        for line_index in group:
            expanded[line_index] = failures.get(line_index, ("slot_dependency",))
    return expanded


def _regenerate_failed_lines(
    llm: LLMFrontend,
    *,
    details: dict[str, object],
    lines: list[str],
    failed_indices: set[int],
    failure_reasons: dict[int, tuple[str, ...]],
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
                    "failure_reasons": list(failure_reasons.get(index, ())),
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


def _candidate_signature(text: str) -> str:
    """空白・句読点・カナ種だけが違う候補を同一として扱う。"""

    normalized = unicodedata.normalize("NFKC", str(text or "")).casefold()
    chars: list[str] = []
    for char in normalized:
        code = ord(char)
        if 0x30A1 <= code <= 0x30F6:
            char = chr(code - 0x60)
        if char.isalnum() or "ぁ" <= char <= "ゖ" or "一" <= char <= "鿿":
            chars.append(char)
    return "".join(chars)


def _normalize_candidate_lines(lines: list[str], *, stage: str) -> None:
    """生成済み三行をその場でかな化し、以後の全検査を正規化後へ統一する。"""

    for line_index, text in enumerate(lines):
        lines[line_index] = _normalize_candidate_kana(
            text,
            line_index=line_index,
            stage=stage,
        )


def _normalize_candidate_kana(text: str, *, line_index: int, stage: str) -> str:
    """UniDicで展開できた場合だけ採用し、意味内容には触れない。"""

    normalized = _clean_single_line(hiraganize_japanese_text(text))
    if not normalized or normalized == text:
        return text
    LOGGER.warning(
        "haiku_kana_normalized stage=%s line_index=%s from=%s to=%s",
        stage,
        line_index,
        summarize_for_log(text),
        summarize_for_log(normalized),
    )
    return normalized


def _duplicate_line_failures(lines: list[str]) -> dict[int, tuple[str, ...]]:
    seen: set[str] = set()
    failures: dict[int, tuple[str, ...]] = {}
    for line_index, line in enumerate(lines):
        signature = _candidate_signature(line)
        if not signature or signature in seen:
            failures[line_index] = ("duplicate_line",)
        else:
            seen.add(signature)
    return failures


def _atom_reservation_ids(atom: HaikuSourceAtom) -> set[str]:
    return set(atom.basis_atom_ids or (atom.atom_id,))


def _reservation_ids(
    atom_ids: Any,
    atom_by_id: dict[str, HaikuSourceAtom],
) -> set[str]:
    reserved: set[str] = set()
    for atom_id in atom_ids:
        atom = atom_by_id.get(atom_id)
        if atom is not None:
            reserved.update(_atom_reservation_ids(atom))
    return reserved


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
