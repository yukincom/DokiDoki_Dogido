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
from dogido_server.dialogue.conversation_repair import (
    ConversationRepairPayload, REPAIR_ACTIONS, parse_conversation_repair,
)

from dogido_server.language_dialogue.contracts import (
    GroundedReply,
    Interpretation,
    ParticipationAssessment,
    ParticipationForecast,
    ResearchIntent,
    ResearchReading,
    WebConsent,
)


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


class _WorkshopAgentStep(_StrictModel):
    action: Literal[
        "respond",
        "explain",
        "ask",
        "inspect",
        "propose_revision",
        "compare",
        "show_current",
        "stage_player_edit",
        "accept_pending",
        "reject_pending",
        "close_workshop",
        "unrelated",
        "defer_to_legacy",
    ]
    purpose: Literal[
        "understand_meaning",
        "improve_wording",
        "evaluate_verse",
        "review_pending",
        "adopt_pending",
        "discard_pending",
        "show_verse",
        "finish_workshop",
        "continue_discussion",
        "other",
    ]
    confidence: Confidence
    evidence: str
    speech: str
    checks: Annotated[
        list[Literal["reading", "meter", "source"]],
        Field(max_length=3),
    ]
    close_after_action: bool
    close_evidence: str
    findings: Annotated[list[_WorkshopFinding], Field(max_length=3)]
    line_reference: _WorkshopLineReference
    line_proposal: _WorkshopLineProposal


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


class _PlayerChatPlanEvidence(_StrictModel):
    turn_id: NonEmptyText
    quote: NonEmptyText


class _PlayerChatPlan(_StrictModel):
    action: Literal[
        "continue_conversation",
        "check_entity_presence",
        "identify_entity",
        "answer_observation",
        "clarify_reference",
        "correct_previous_reply",
        "repair_conversation",
        "clarify_repair",
    ]
    focus: NonEmptyText
    entity_query: str
    evidence: Annotated[list[_PlayerChatPlanEvidence], Field(min_length=1, max_length=3)]
    confidence: Confidence
    repair: ConversationRepairPayload | None = None

    @model_validator(mode="after")
    def _entity_query_matches_action(self) -> _PlayerChatPlan:
        entity_actions = {
            "check_entity_presence",
            "identify_entity",
            "correct_previous_reply",
        }
        if self.action in entity_actions and not self.entity_query:
            raise ValueError("entity action requires entity_query")
        if self.action not in entity_actions and self.entity_query:
            raise ValueError("non-entity action forbids entity_query")
        if (self.action in REPAIR_ACTIONS) != (self.repair is not None):
            raise ValueError("repair actions require repair; other actions forbid it")
        return self


class _LightSourceCommentPlan(_StrictModel):
    action: Literal[
        "stay_silent",
        "acknowledge_supply_gain",
        "relief_after_darkness",
    ]
    basis_ids: Annotated[list[NonEmptyText], Field(min_length=1, max_length=3)]
    confidence: Confidence


_MODELS: dict[str, type[BaseModel]] = {
    "language_dialogue_interpretation": Interpretation,
    "language_dialogue_reply": GroundedReply,
    "language_participation_forecast": ParticipationForecast,
    "language_participation_assessment": ParticipationAssessment,
    "language_research_intent": ResearchIntent,
    "language_research_reading": ResearchReading,
    "language_web_consent": WebConsent,
    "assist_select_sword_intent": _SelectSwordIntent,
    "player_chat_plan": _PlayerChatPlan,
    "light_source_comment_plan": _LightSourceCommentPlan,
    "haiku_draft": _HaikuDraft,
    "haiku_irony": _Irony,
    "haiku_line_grounding": _LineGrounding,
    "haiku_line_regeneration": _LineRegeneration,
    "haiku_scene": _Scene,
    "haiku_workshop_combat_input": _WorkshopCombatInput,
    "haiku_workshop_evaluation": _WorkshopEvaluation,
    "haiku_workshop_agent_step": _WorkshopAgentStep,
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
    if kind == "language_participation_assessment":
        constraints.append(
            "matched_pattern_idsは次の文字列だけを重複なく使う: "
            + json.dumps(sorted(_participation_pattern_ids(request_details)))
        )
    elif kind == "player_chat_plan":
        constraints.append(
            "actionは次から選ぶ: "
            + json.dumps(sorted(_string_set(request_details.get("allowed_actions"))))
        )
        constraints.append(
            "evidence.turn_idとquoteはhistory/currentにある発話IDと連続部分を正確にコピーし、"
            "currentを必ず含める。entity_queryもevidence.quote内の連続部分にする"
        )
        routing_hints = request_details.get("routing_hints")
        if isinstance(routing_hints, dict) and (
            routing_hints.get("inventory_question") is True
            or routing_hints.get("sound_question") is True
        ):
            constraints.append(
                "routing_hintsで確定済みの所持品または音の問いなので、"
                "actionはanswer_observationにする"
            )
        if (
            isinstance(routing_hints, dict)
            and routing_hints.get("presence_question") is True
        ):
            constraints.append(
                "routing_hintsで明示的な在否問いと確定済みなので、"
                "actionはcheck_entity_presenceにする"
            )
        if (
            isinstance(routing_hints, dict)
            and routing_hints.get("plain_presence_report") is True
        ):
            constraints.append(
                "routing_hintsでplayerの平叙存在報告と確定済みなので、"
                "check_entity_presenceまたはidentify_entityへ変えない"
            )
    elif kind == "light_source_comment_plan":
        constraints.append(
            "actionは次から選ぶ: "
            + json.dumps(sorted(_string_set(request_details.get("allowed_actions"))))
        )
        constraints.append(
            "basis_idsは次の文字列から判断に使ったものだけを重複なく1〜3件返す: "
            + json.dumps(sorted(_light_comment_basis_ids(request_details)))
        )
        constraints.append(
            "relief_after_darknessはdark_push_recoveredをbasis_idsへ含める。"
            "acknowledge_supply_gainはfirst_light_supply、supply_before、"
            "surroundings_lightのどれかを含める"
        )
    elif kind == "haiku_line_grounding":
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
    elif kind == "haiku_workshop_agent_step":
        constraints.append(
            "actionは次から選ぶ: "
            + json.dumps(sorted(_string_set(request_details.get("allowed_actions"))))
        )
        constraints.append(
            "purposeは次から選ぶ: "
            + json.dumps(sorted(_string_set(request_details.get("allowed_purposes"))))
        )
        constraints.append(
            "checksは次から重複なく選ぶ: "
            + json.dumps(sorted(_string_set(request_details.get("allowed_checks"))))
        )
        constraints.append(
            "evidenceはplayer_textの連続部分を正確にコピーする。"
            "defer_to_legacy以外は空にしない"
        )
        constraints.append(
            "speechはrespond/explain/ask/compareだけ非空にし、"
            "inspect/propose_revision/show_current/stage_player_edit/"
            "accept_pending/reject_pending/close_workshop/unrelated/"
            "defer_to_legacyでは必ず空文字にする"
        )
        constraints.append(
            "close_after_action=trueはaccept_pendingまたはreject_pendingだけ。"
            "その場合は採否をevidence、相談終了をclose_evidenceへplayer_textから"
            "それぞれ抜く。同じ短い連続部分に両方の意思があれば重なってもよい。"
            "それ以外はfalseかつclose_evidenceを空にする"
        )
        if request_details.get("pending_verse"):
            constraints.append(
                "未採用案があるためclose_workshopは選ばない。採用または破棄と終了を"
                "同時に求められた場合はaccept_pendingまたはreject_pendingを選び、"
                "close_after_action=trueにする"
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

    if kind == "language_participation_assessment":
        _check_unique_known_ids(
            payload["matched_pattern_ids"],
            _participation_pattern_ids(details),
            "matched_pattern_ids",
            errors,
        )
    elif kind == "player_chat_plan":
        allowed_actions = _string_set(details.get("allowed_actions"))
        if allowed_actions and payload["action"] not in allowed_actions:
            errors.append("action:not_allowed")
        turns = _player_chat_turns(details)
        evidence_turn_ids: set[str] = set()
        for index, row in enumerate(payload["evidence"]):
            turn_id = row["turn_id"]
            quote = row["quote"]
            if turn_id in evidence_turn_ids:
                errors.append(f"evidence.{index}.turn_id:duplicate")
            evidence_turn_ids.add(turn_id)
            source = turns.get(turn_id)
            if source is None:
                errors.append(f"evidence.{index}.turn_id:unknown")
            elif quote not in source["text"]:
                errors.append(f"evidence.{index}.quote:not_exact")
        if "current" not in evidence_turn_ids:
            errors.append("evidence:current_required")
        entity_query = payload["entity_query"]
        if entity_query and not any(
            entity_query in row["quote"] for row in payload["evidence"]
        ):
            errors.append("entity_query:not_in_evidence")
        if payload["action"] == "correct_previous_reply" and not any(
            turns.get(row["turn_id"], {}).get("role") == "assistant"
            for row in payload["evidence"]
        ):
            errors.append("evidence:assistant_required")
        if payload["action"] in REPAIR_ACTIONS:
            repair = parse_conversation_repair(payload["action"], payload.get("repair"), details)
            if repair is None:
                errors.append("repair:ungrounded")
            elif not any(
                row["turn_id"] == repair.target_turn_id and repair.target_quote in row["quote"]
                for row in payload["evidence"]
            ):
                errors.append("repair:target_evidence_required")
        routing_hints = details.get("routing_hints")
        if isinstance(routing_hints, dict) and (
            routing_hints.get("inventory_question") is True
            or routing_hints.get("sound_question") is True
        ) and payload["action"] != "answer_observation":
            errors.append("action:routing_hint_requires_answer_observation")
        if (
            isinstance(routing_hints, dict)
            and routing_hints.get("presence_question") is True
            and payload["action"] != "check_entity_presence"
        ):
            errors.append("action:routing_hint_requires_presence_check")
        if (
            isinstance(routing_hints, dict)
            and routing_hints.get("plain_presence_report") is True
            and payload["action"] in {"check_entity_presence", "identify_entity"}
        ):
            errors.append("action:routing_hint_preserves_player_report")

    elif kind == "light_source_comment_plan":
        allowed_actions = _string_set(details.get("allowed_actions"))
        if allowed_actions and payload["action"] not in allowed_actions:
            errors.append("action:not_allowed")
        _check_unique_known_ids(
            payload["basis_ids"],
            _light_comment_basis_ids(details),
            "basis_ids",
            errors,
        )
        if (
            payload["action"] == "relief_after_darkness"
            and "dark_push_recovered" not in payload["basis_ids"]
        ):
            errors.append("basis_ids:dark_push_recovered_required")
        if payload["action"] == "acknowledge_supply_gain" and not {
            "first_light_supply",
            "supply_before",
            "surroundings_light",
        }.intersection(payload["basis_ids"]):
            errors.append("basis_ids:acknowledgement_reason_required")

    elif kind == "haiku_scene":
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

    elif kind == "haiku_workshop_agent_step":
        allowed_actions = _string_set(details.get("allowed_actions"))
        if allowed_actions and payload["action"] not in allowed_actions:
            errors.append("action:not_allowed")
        allowed_purposes = _string_set(details.get("allowed_purposes"))
        if allowed_purposes and payload["purpose"] not in allowed_purposes:
            errors.append("purpose:not_allowed")
        required_purposes = {
            "accept_pending": "adopt_pending",
            "reject_pending": "discard_pending",
            "close_workshop": "finish_workshop",
            "stage_player_edit": "improve_wording",
        }
        required_purpose = required_purposes.get(payload["action"])
        if required_purpose is not None and payload["purpose"] != required_purpose:
            errors.append("purpose:action_mismatch")
        checks = payload["checks"]
        allowed_checks = _string_set(details.get("allowed_checks"))
        if len(checks) != len(set(checks)):
            errors.append("checks:duplicate")
        if set(checks) - allowed_checks:
            errors.append("checks:not_allowed")
        if payload["action"] == "inspect" and not checks:
            errors.append("checks:inspection_requires_check")
        if payload["action"] != "inspect" and checks:
            errors.append("checks:only_for_inspection")
        direct_actions = {"respond", "explain", "ask", "compare"}
        if payload["action"] in direct_actions and not payload["speech"].strip():
            errors.append("speech:required")
        if payload["action"] not in direct_actions and payload["speech"].strip():
            errors.append("speech:not_allowed")
        close_after_action = payload["close_after_action"]
        close_evidence = payload["close_evidence"]
        if close_after_action and payload["action"] not in {
            "accept_pending",
            "reject_pending",
        }:
            errors.append("close_after_action:not_allowed")
        if not close_after_action and close_evidence.strip():
            errors.append("close_evidence:unexpected")
        evidence = payload["evidence"]
        player_text = str(details.get("player_text") or "")
        if payload["action"] != "defer_to_legacy" and (
            len(evidence.strip()) < 2 or evidence not in player_text
        ):
            errors.append("evidence:not_exact")
        if close_after_action and (
            len(close_evidence.strip()) < 2 or close_evidence not in player_text
        ):
            errors.append("close_evidence:not_exact")
        original_player_text = str(details.get("original_player_text") or player_text)
        mutation_actions = {
            "accept_pending",
            "reject_pending",
            "close_workshop",
            "stage_player_edit",
        }
        if payload["action"] in mutation_actions and evidence not in original_player_text:
            errors.append("evidence:not_in_original")
        if close_after_action and close_evidence not in original_player_text:
            errors.append("close_evidence:not_in_original")
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


def _light_comment_basis_ids(details: dict[str, Any]) -> set[str]:
    return {
        str(row["basis_id"])
        for row in details.get("facts", [])
        if isinstance(row, dict)
        and isinstance(row.get("basis_id"), str)
        and row["basis_id"]
    }


def _participation_pattern_ids(details: dict[str, Any]) -> set[str]:
    return {
        str(row["pattern_id"])
        for row in details.get("expected_continuations", [])
        if isinstance(row, dict)
        and isinstance(row.get("pattern_id"), str)
        and row["pattern_id"]
    }


def _player_chat_turns(details: dict[str, Any]) -> dict[str, dict[str, str]]:
    rows: list[object] = []
    history = details.get("history")
    if isinstance(history, list):
        rows.extend(history)
    current = details.get("current")
    if isinstance(current, dict):
        rows.append(current)
    turns: dict[str, dict[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        turn_id = row.get("turn_id")
        role = row.get("role")
        text = row.get("text")
        if (
            isinstance(turn_id, str)
            and turn_id
            and role in {"user", "assistant"}
            and isinstance(text, str)
            and text
        ):
            turns[turn_id] = {"role": role, "text": text}
    return turns


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
