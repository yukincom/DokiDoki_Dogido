"""Structured LLM 出力の現行 JSON 契約。

プロンプト、provider、各ドメイン consumer の間で JSON の外形がずれないよう、
全 structured kind の静的な型と、入力に依存する行番号・atom ID をここで検査する。
旧形式を現行形式へ変換して受理はしない。意味・発話根拠・confidence 閾値・CAS は
従来どおり各ドメインの型付きコードが検証する。
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from dogido_server.language_dialogue.contracts import GroundedReply, Interpretation


Confidence = Annotated[float | int, Field(ge=0.0, le=1.0)]
NonEmptyText = Annotated[str, Field(min_length=1)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _HaikuDraft(_StrictModel):
    lines: Annotated[list[NonEmptyText], Field(min_length=3, max_length=3)]


class _LineAssessment(_StrictModel):
    line_index: int
    atom_ids: Annotated[list[NonEmptyText], Field(min_length=1)]
    meaning_retained: bool
    natural_japanese: bool
    reason: str


class _LineGrounding(_StrictModel):
    assessments: list[_LineAssessment]


class _RegeneratedLine(_StrictModel):
    line_index: int
    text: NonEmptyText


class _LineRegeneration(_StrictModel):
    lines: list[_RegeneratedLine]


class _Irony(_StrictModel):
    found: bool
    kind: Literal["none", "relation", "contrast", "juxtaposition", "scene"]
    description: str
    elements: list[str]
    focus: list[str]
    confidence: Confidence


class _SceneClause(_StrictModel):
    text: NonEmptyText
    basis_atom_ids: Annotated[list[NonEmptyText], Field(min_length=1)]
    claim_class: Literal["factual", "interpretive"]


class _Scene(_StrictModel):
    found: bool
    clauses: Annotated[list[_SceneClause], Field(max_length=3)]
    motifs: list[str]
    focus: list[str]
    confidence: Confidence

    @model_validator(mode="after")
    def _found_requires_clause(self) -> _Scene:
        if self.found and not self.clauses:
            raise ValueError("found=true requires at least one clause")
        return self


class _WorkshopFinding(_StrictModel):
    line_index: int | None = None
    fragment: str
    problem: str
    note: str
    confidence: Confidence


class _WorkshopEvaluation(_StrictModel):
    found: bool
    sentiment: Literal["positive", "negative", "mixed", "unknown"]
    scope: Literal["whole_verse", "part", "unknown"]
    evidence: str
    confidence: Confidence


class _WorkshopCloseRequest(_StrictModel):
    found: bool
    scope: Literal["workshop", "next_haiku", "unknown"]
    evidence: str
    confidence: Confidence


class _WorkshopLineReference(_StrictModel):
    found: bool
    concept_id: Literal["line_1", "line_2", "line_3", "unknown"]
    evidence: str
    confidence: Confidence


class _WorkshopLineProposal(_StrictModel):
    found: bool
    target_fragment: str
    replacement_text: str
    evidence: str
    confidence: Confidence


class _WorkshopIntent(_StrictModel):
    intent: str
    confidence: Confidence
    repair_requested: bool
    findings: Annotated[list[_WorkshopFinding], Field(max_length=3)]
    evaluation: _WorkshopEvaluation
    close_request: _WorkshopCloseRequest
    line_reference: _WorkshopLineReference
    line_proposal: _WorkshopLineProposal


class _WorkshopPendingDecision(_StrictModel):
    action: str
    confidence: Confidence
    evidence: str
    close_request: _WorkshopCloseRequest


class _WorkshopCombatInput(_StrictModel):
    action: str
    confidence: Confidence
    evidence: str


class _WorkshopRevisionLine(_StrictModel):
    line_index: int
    expected_text: NonEmptyText
    replacement_text: NonEmptyText
    atom_ids: Annotated[list[NonEmptyText], Field(min_length=1)]


class _WorkshopRevision(_StrictModel):
    lines: list[_WorkshopRevisionLine]


class _SelectSwordIntent(_StrictModel):
    intent: Literal["select_weapon", "other"]
    weapon_kind: Literal["sword", "unknown"]
    is_request: bool
    evidence: str
    confidence: Confidence


_MODELS: dict[str, type[BaseModel]] = {
    "language_dialogue_interpretation": Interpretation,
    "language_dialogue_reply": GroundedReply,
    "assist_select_sword_intent": _SelectSwordIntent,
    "haiku_draft": _HaikuDraft,
    "haiku_irony": _Irony,
    "haiku_line_grounding": _LineGrounding,
    "haiku_line_regeneration": _LineRegeneration,
    "haiku_scene": _Scene,
    "haiku_workshop_combat_input": _WorkshopCombatInput,
    "haiku_workshop_evaluation": _WorkshopEvaluation,
    "haiku_workshop_intent": _WorkshopIntent,
    "haiku_workshop_pending_decision": _WorkshopPendingDecision,
    "haiku_workshop_revision": _WorkshopRevision,
}

STRUCTURED_CONTRACT_KINDS = frozenset(_MODELS)
STRUCTURED_CONTRACT_RETRY_KEY = "__dogido_structured_contract_retry"


@dataclass(frozen=True, slots=True)
class StructuredContractResult:
    accepted: bool
    errors: tuple[str, ...] = ()

    @property
    def summary(self) -> str:
        return "; ".join(self.errors) or "accepted"


def validate_structured_payload(
    kind: str,
    payload: object,
    *,
    details: dict[str, Any] | None = None,
) -> StructuredContractResult:
    """静的schemaとリクエスト依存の外形を検証する。payloadは書き換えない。"""

    model = _MODELS.get(kind)
    if model is None:
        return StructuredContractResult(False, (f"unregistered_kind:{kind}",))
    try:
        model.model_validate(payload, strict=True)
    except ValidationError as exc:
        return StructuredContractResult(False, _validation_errors(exc))

    dynamic_errors = _validate_dynamic_contract(kind, payload, details or {})
    return StructuredContractResult(not dynamic_errors, tuple(dynamic_errors))


def structured_contract_retry_instruction(
    kind: str,
    *,
    details: dict[str, Any] | None = None,
) -> str:
    """schema専用再試行へ渡す、機械可読な現行契約と動的制約。"""

    model = _MODELS.get(kind)
    if model is None:
        return f"未登録のstructured kind: {kind}"
    request_details = details or {}
    constraints: list[str] = []
    if kind == "haiku_line_grounding":
        constraints.append(
            "assessments.line_indexをこの順で一件ずつ返す: "
            + json.dumps(
                _requested_indices(request_details.get("grounding_lines"), "line_index")
            )
        )
        constraints.append(
            "atom_idsは次の文字列から意味に合うものだけを正確にコピーする: "
            + json.dumps(sorted(_source_atom_ids(request_details)), ensure_ascii=False)
        )
    elif kind == "haiku_line_regeneration":
        constraints.append(
            "lines.line_indexをこの順で一件ずつ返す: "
            + json.dumps(_integer_list(request_details.get("failed_line_indices")))
        )
    elif kind in {"haiku_scene", "haiku_workshop_revision"}:
        constraints.append(
            "atom IDは次の文字列から意味に合うものだけを正確にコピーする: "
            + json.dumps(sorted(_source_atom_ids(request_details)), ensure_ascii=False)
        )
    elif kind == "haiku_workshop_intent":
        constraints.append(
            "intentは次から選ぶ: "
            + json.dumps(sorted(_string_set(request_details.get("allowed_intents"))))
        )
    elif kind in {"haiku_workshop_pending_decision", "haiku_workshop_combat_input"}:
        constraints.append(
            "actionは次から選ぶ: "
            + json.dumps(sorted(_string_set(request_details.get("allowed_actions"))))
        )
    schema = json.dumps(model.model_json_schema(), ensure_ascii=False, separators=(",", ":"))
    dynamic = "\n".join(constraints) or "追加の動的制約なし"
    return f"現行JSON Schema:\n{schema}\nリクエスト固有制約:\n{dynamic}"


def _validation_errors(exc: ValidationError) -> tuple[str, ...]:
    errors: list[str] = []
    for row in exc.errors(include_url=False)[:8]:
        location = ".".join(str(part) for part in row.get("loc", ())) or "$"
        errors.append(f"{location}:{row.get('type', 'invalid')}")
    if len(exc.errors(include_url=False)) > 8:
        errors.append("additional_errors")
    return tuple(errors)


def _validate_dynamic_contract(
    kind: str,
    payload: object,
    details: dict[str, Any],
) -> list[str]:
    assert isinstance(payload, dict)
    errors: list[str] = []

    if kind == "haiku_scene":
        allowed_ids = _source_atom_ids(details)
        for index, clause in enumerate(payload["clauses"]):
            _check_unique_known_ids(
                clause["basis_atom_ids"],
                allowed_ids,
                f"clauses.{index}.basis_atom_ids",
                errors,
            )

    elif kind == "haiku_line_grounding":
        requested = _requested_indices(details.get("grounding_lines"), "line_index")
        returned = [row["line_index"] for row in payload["assessments"]]
        _check_exact_indices(returned, requested, "assessments.line_index", errors)
        allowed_ids = _source_atom_ids(details)
        for index, row in enumerate(payload["assessments"]):
            _check_unique_known_ids(
                row["atom_ids"],
                allowed_ids,
                f"assessments.{index}.atom_ids",
                errors,
            )

    elif kind == "haiku_line_regeneration":
        requested = _integer_list(details.get("failed_line_indices"))
        returned = [row["line_index"] for row in payload["lines"]]
        _check_exact_indices(returned, requested, "lines.line_index", errors)

    elif kind == "haiku_workshop_revision":
        requested = _integer_list(details.get("target_line_indices"))
        returned = [row["line_index"] for row in payload["lines"]]
        _check_exact_indices(returned, requested, "lines.line_index", errors)
        current = {
            row.get("line_index"): row.get("text")
            for row in details.get("current_lines", [])
            if isinstance(row, dict)
        }
        allowed_ids = _source_atom_ids(details)
        for index, row in enumerate(payload["lines"]):
            if row["expected_text"] != current.get(row["line_index"]):
                errors.append(f"lines.{index}.expected_text:mismatch")
            _check_unique_known_ids(
                row["atom_ids"],
                allowed_ids,
                f"lines.{index}.atom_ids",
                errors,
            )

    elif kind == "haiku_workshop_intent":
        allowed_intents = _string_set(details.get("allowed_intents"))
        if allowed_intents and payload["intent"] not in allowed_intents:
            errors.append("intent:not_allowed")
        allowed_problems = _string_set(details.get("allowed_problem_types"))
        for index, row in enumerate(payload["findings"]):
            if row.get("line_index") not in (None, 0, 1, 2):
                errors.append(f"findings.{index}.line_index:not_allowed")
            if allowed_problems and row["problem"] not in allowed_problems:
                errors.append(f"findings.{index}.problem:not_allowed")

    elif kind in {"haiku_workshop_pending_decision", "haiku_workshop_combat_input"}:
        allowed_actions = _string_set(details.get("allowed_actions"))
        if allowed_actions and payload["action"] not in allowed_actions:
            errors.append("action:not_allowed")

    return errors[:12]


def _source_atom_ids(details: dict[str, Any]) -> set[str]:
    return {
        str(row["atom_id"])
        for row in details.get("source_atoms", [])
        if isinstance(row, dict) and isinstance(row.get("atom_id"), str) and row["atom_id"]
    }


def _requested_indices(value: object, key: str) -> list[int]:
    return [
        row[key]
        for row in value
        if isinstance(row, dict)
        and isinstance(row.get(key), int)
        and not isinstance(row.get(key), bool)
    ] if isinstance(value, list) else []


def _integer_list(value: object) -> list[int]:
    return [
        item for item in value
        if isinstance(item, int) and not isinstance(item, bool)
    ] if isinstance(value, list) else []


def _string_set(value: object) -> set[str]:
    return {
        item for item in value if isinstance(item, str) and item
    } if isinstance(value, (list, tuple, set, frozenset)) else set()


def _check_exact_indices(
    returned: list[int],
    requested: list[int],
    path: str,
    errors: list[str],
) -> None:
    if len(returned) != len(set(returned)):
        errors.append(f"{path}:duplicate")
    if sorted(returned) != sorted(requested):
        errors.append(f"{path}:expected={sorted(requested)},actual={sorted(returned)}")


def _check_unique_known_ids(
    values: list[str],
    allowed: set[str],
    path: str,
    errors: list[str],
) -> None:
    if len(values) != len(set(values)):
        errors.append(f"{path}:duplicate")
    unknown = sorted(set(values) - allowed)
    if unknown:
        errors.append(f"{path}:unknown={','.join(unknown[:3])}")
