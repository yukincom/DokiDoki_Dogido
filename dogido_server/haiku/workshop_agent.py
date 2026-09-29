"""検証付き川柳ワークショップの、有界な共同編集ステップ。

モデルは会話の次の手を選ぶだけで、句・pending・memoryを直接変更しない。
読み／音数／出典の確認と編集の採否は、呼び出し側のコードが実行する。
保存する軌跡は action / outcome / validation code に限り、思考過程は保持しない。
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable

from dogido_server.llm.haiku import count_japanese_sounds
from dogido_server.llm.sanitize import clean_output, is_usable_output

from .workshop import (
    WORKSHOP_LINE_CONCEPTS,
    WORKSHOP_PROBLEM_TYPES,
    RecentHaikuWorkshop,
    WorkshopAnalysis,
    finalize_workshop_analysis_payload,
    pending_revision_decision,
    workshop_verse_lines,
)
from .workshop_context import workshop_context_details


WORKSHOP_AGENT_ACTIONS = frozenset(
    {
        "respond",
        "explain",
        "ask",
        "inspect",
        "propose_revision",
        "compare",
        "show_current",
        "stage_player_edit",
        "stage_conversation_candidate",
        "accept_pending",
        "reject_pending",
        "close_workshop",
        "unrelated",
        "defer_to_legacy",
    }
)
WORKSHOP_AGENT_DIRECT_ACTIONS = frozenset({"respond", "explain", "ask", "compare"})
WORKSHOP_AGENT_CHECKS = frozenset({"reading", "meter", "source"})
WORKSHOP_AGENT_PURPOSES = frozenset(
    {
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
    }
)
WORKSHOP_AGENT_MIN_CONFIDENCE = 0.72
WORKSHOP_AGENT_MUTATION_MIN_CONFIDENCE = 0.85
WORKSHOP_AGENT_RECORD_LIMIT = 12
_STRUCTURED_STATUS_KEY = "__dogido_status"
_UNSAVED_ACTION_CLAIMS = (
    "保存した",
    "保存しといた",
    "保存できた",
    "覚えた",
    "覚えといた",
    "採用した",
    "採用しといた",
    "採用できた",
    "残したで",
    "残しといた",
    "確定したで",
    "これで決まり",
)
_UNFINISHED_SUCCESS_CLAIMS = (
    "直したで",
    "修正したで",
    "案ができた",
    "案できた",
    "直せた",
)
_INSPECTION_CLAIM_MARKERS = {
    "reading": (
        "読みは",
        "読み方は",
        "よみは",
        "よみかたは",
        "と読む",
        "って読む",
        "と読ん",
        "って読ん",
    ),
    "meter": (
        "音数は",
        "モーラ",
        "五・七・五",
        "5・7・5",
        "五七五",
        "575",
        "五音",
        "七音",
    ),
    "source": (
        "出典は",
        "出典の",
        "根拠は",
        "材料の記録",
        "出典の記録",
        "記録がある",
        "記録もある",
        "記録はある",
    ),
}
_STATE_CHANGE_REPORT = re.compile(
    r"(?:と|って)(?:"
    r"言(?:った|う|われた)|い(?:った|う|われた)|"
    r"聞(?:いた|く|かれた)|き(?:いた|く|かれた)|"
    r"書(?:いた|いてある)|か(?:いた|いてある)|"
    r"読(?:んだ|む)|よ(?:んだ|む)"
    r")"
)
_STATE_CHANGE_CONDITIONAL = re.compile(
    r"(?:もし|(?:採用|保存|残す|捨てる|戻す|終わる|終える|やめる)"
    r".{0,8}(?:たら|なら)(?:どう|どうなる|いい|ええ))"
)
_STATE_CHANGE_UNCERTAIN = re.compile(
    r"(?:かな|かしら|やろか|だろうか|でしょうか|ですか|ますか|ええか|いいか)\s*$"
)
_STATE_CHANGE_CONTRADICTIONS = {
    "accept_pending": re.compile(
        r"(?:"
        r"採用(?:は|を)?(?:しない|せん|せえへん|するな|やめ)|"
        r"保存(?:は|を)?(?:しない|せん|せえへん|するな)|"
        r"残(?:さない|さん|せえへん|すな)|"
        r"(?:この|その|いまの|今の)案.{0,8}(?:なし|だめ|駄目|あかん|嫌)|"
        r"(?:元|もと|前)の.{0,8}(?:まま|方|ほう)|"
        r"まだ.{0,12}(?:採用|保存|残).{0,8}(?:しない|せん|決めない|決めん)"
        r")"
    ),
    "reject_pending": re.compile(
        r"(?:"
        r"(?:捨て|戻さ|却下し|破棄し)(?:ない|ん|へん|なくて)|"
        r"(?:捨てる|戻す|却下する|破棄する)な|"
        r"(?:この|その|いまの|今の)案.{0,8}"
        r"(?:残して|残そう|採用して|採用しよう|保存して|使おう)|"
        r"(?:この|その|いまの|今の)案で(?:いこう|行こう|いい|ええ)"
        r")"
    ),
    "close_workshop": re.compile(
        r"(?:"
        r"終わ(?:らない|らん|らへん)|終え(?:ない|ん|へん|るな)|"
        r"やめ(?:ない|ん|へん|るな)|閉じ(?:ない|ん|へん|るな)|"
        r"続け(?:る|たい|よう)|もう少し|"
        r"まだ.{0,12}(?:続け|直し|話し|相談し)|"
        r"(?:次の|上の|下の|真ん中の)(?:行|パート)|"
        r"(?:上|中|下)(?:五|七)|(?:一|二|三|1|2|3)行目"
        r")"
    ),
    "stage_conversation_candidate": re.compile(
        r"(?:にし(?:ない|なく)|にするな|使わない|採用しない|まだ.{0,12}(?:決めない|迷う))"
    ),
}
_STATE_CHANGE_POSITIVE_MARKERS = {
    "accept_pending": re.compile(
        r"(?:"
        r"採用|保存|覚え(?:て|といて|とく)|"
        r"残(?:して|しといて|しとく|そう)|"
        r"(?:この|その|いまの|今の|新しい|直した)(?:案|句|直し).{0,10}"
        r"(?:使って|使おう|決まり|確定)|"
        r"(?:これ|それ|この案|その案)で(?:いこう|行こう|いい|ええ|お願い|決まり)|"
        r"(?:これ|それ|この案|その案)で.{0,8}"
        r"(?:完成|終わ|終え|おしまい|ここまで|区切)"
        r")"
    ),
    "reject_pending": re.compile(
        r"(?:"
        r"捨て|破棄|却下|戻(?:して|そう|す)|やめと(?:く|こう)|"
        r"(?:元|もと|前)の(?:句|案|まま|方|ほう)|"
        r"(?:この|その|いまの|今の)(?:案|直し).{0,8}(?:なし|使わない|使わん)"
        r")"
    ),
    "close_workshop": re.compile(
        r"(?:"
        r"終わ|終え|おしまい|お開き|ここまで|やめ|閉じ|区切|次の句|次へ|"
        r"完成(?:に|で)(?:し|する|しよう|いい|ええ)|"
        r"(?:これ|それ|この案|その案)で.{0,8}完成"
        r")"
    ),
    "stage_conversation_candidate": re.compile(
        r"(?:それ|その案|この案|さっき.{0,8}案|前に.{0,8}案|話した案).{0,12}"
        r"(?:にして|でいい|でええ|でお願い|でいこう|を使って|を採用して)"
    ),
}
_MUTATION_REQUIRED_PURPOSES = {
    "accept_pending": "adopt_pending",
    "reject_pending": "discard_pending",
    "close_workshop": "finish_workshop",
    "stage_player_edit": "improve_wording",
    "stage_conversation_candidate": "improve_wording",
}
_INSPECTION_REQUEST_MARKERS = {
    "reading": ("読み", "よみ", "どう読む", "なんて読む", "何て読む"),
    "meter": ("音数", "モーラ", "五・七・五", "5・7・5", "五七五", "575", "字余り", "字足らず"),
    "source": ("出典", "由来", "元ネタ", "材料の記録", "どこから取", "何を見て"),
}


@dataclass(frozen=True, slots=True)
class WorkshopAgentStep:
    """検証済みの一手。実行権限は持たない。"""

    action: str
    purpose: str
    confidence: float
    evidence: str
    speech: str = ""
    checks: tuple[str, ...] = ()
    close_after_action: bool = False
    close_evidence: str = ""
    analysis: WorkshopAnalysis = WorkshopAnalysis()


def allowed_workshop_agent_actions(
    workshop: RecentHaikuWorkshop,
    *,
    phase: str,
) -> tuple[str, ...]:
    """状態とループ段階から、その時点で選べる一手だけを返す。"""

    if phase == "after_validation":
        actions = {"respond", "explain", "ask", "compare", "show_current"}
    elif phase == "after_inspection":
        actions = {
            "respond",
            "explain",
            "ask",
            "propose_revision",
            "compare",
            "show_current",
            "stage_player_edit",
        }
    else:
        actions = {
            "respond",
            "explain",
            "ask",
            "inspect",
            "propose_revision",
            "compare",
            "show_current",
            "stage_player_edit",
            "close_workshop",
            "unrelated",
            "defer_to_legacy",
        }
        if workshop.pending_revision:
            actions.update({"accept_pending", "reject_pending"})
    if not workshop.pending_revision:
        actions.discard("compare")
        actions.discard("accept_pending")
        actions.discard("reject_pending")
    else:
        # 未採用案を黙って捨てて終了しない。先に採否を明示してもらう。
        actions.discard("close_workshop")
    return tuple(sorted(actions))


def build_workshop_agent_details(
    workshop: RecentHaikuWorkshop,
    player_text: str,
    *,
    original_player_text: str | None = None,
    phase: str,
    observation: dict[str, Any] | None = None,
    turn_steps: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """同じ一句と現在ターンだけに閉じた、共同編集プランナー入力。"""

    stage = "pending_review" if workshop.pending_revision else "discussion"
    if workshop.awaiting_close_confirmation:
        stage = "close_confirmation"
    elif workshop.awaiting_meaning_ack:
        stage = "meaning_explained"
    bounded_turn_steps = [dict(row) for row in list(turn_steps)[-4:]]
    context = workshop_context_details(workshop)
    recent_steps = context.get("recent_agent_steps")
    if (
        bounded_turn_steps
        and isinstance(recent_steps, list)
        and recent_steps[-len(bounded_turn_steps) :] == bounded_turn_steps
    ):
        # 実行直後の一手はturn_stepsへ分け、同じJSONを二重に見せない。
        context["recent_agent_steps"] = recent_steps[: -len(bounded_turn_steps)]
    return {
        "phase": phase,
        "conversation_stage": stage,
        "canonical_verse": workshop.display_surface(),
        "canonical_reading": workshop.display_line(),
        "working_verse": workshop.editing_surface(),
        "working_reading": workshop.editing_line(),
        "pending_verse": workshop.editing_surface() if workshop.pending_revision else None,
        "player_text": (player_text or "").strip(),
        "original_player_text": (
            original_player_text if original_player_text is not None else player_text
        ).strip(),
        "workshop_context": context,
        "tool_observation": observation,
        "turn_steps": bounded_turn_steps,
        "allowed_actions": list(allowed_workshop_agent_actions(workshop, phase=phase)),
        "allowed_purposes": sorted(WORKSHOP_AGENT_PURPOSES),
        "allowed_checks": sorted(WORKSHOP_AGENT_CHECKS),
        "allowed_problem_types": sorted(WORKSHOP_PROBLEM_TYPES),
        "line_concepts": [concept.to_llm_dict() for concept in WORKSHOP_LINE_CONCEPTS],
    }


def finalize_workshop_agent_step(
    payload: object,
    *,
    details: dict[str, Any],
) -> tuple[WorkshopAgentStep | None, str]:
    """モデルの一手を発話根拠・状態・段階・発話外形で閉じる。"""

    if not isinstance(payload, dict):
        return None, "missing_payload"
    status = str(payload.get(_STRUCTURED_STATUS_KEY) or "accepted")
    if status != "accepted":
        return None, f"structured_{status}"
    action = str(payload.get("action") or "").strip()
    allowed_actions = {
        str(value)
        for value in details.get("allowed_actions", [])
        if isinstance(value, str) and value
    }
    if action not in WORKSHOP_AGENT_ACTIONS or action not in allowed_actions:
        return None, "action_not_allowed"
    if action == "defer_to_legacy":
        return None, "deferred"
    purpose = str(payload.get("purpose") or "").strip()
    if purpose not in WORKSHOP_AGENT_PURPOSES:
        return None, "purpose_not_allowed"
    required_purpose = _MUTATION_REQUIRED_PURPOSES.get(action)
    if required_purpose is not None and purpose != required_purpose:
        return None, "action_purpose_mismatch"
    raw_confidence = payload.get("confidence")
    try:
        confidence = float(raw_confidence) if not isinstance(raw_confidence, bool) else 0.0
    except (TypeError, ValueError):
        return None, "invalid_confidence"
    threshold = (
        WORKSHOP_AGENT_MUTATION_MIN_CONFIDENCE
        if action in {"accept_pending", "reject_pending", "close_workshop", "stage_player_edit", "stage_conversation_candidate"}
        else WORKSHOP_AGENT_MIN_CONFIDENCE
    )
    if not 0.0 <= confidence <= 1.0 or confidence < threshold:
        return None, "low_confidence"
    player_text = str(details.get("player_text") or "").strip()
    original_player_text = str(details.get("original_player_text") or player_text).strip()
    evidence = str(payload.get("evidence") or "").strip()[:120]
    if len(_compact(evidence)) < 2 or _compact(evidence) not in _compact(player_text):
        return None, "ungrounded_evidence"
    if action in _STATE_CHANGE_CONTRADICTIONS and not _state_change_evidence_is_safe(
        action,
        player_text=original_player_text,
        evidence=evidence,
    ):
        return None, "unsafe_state_change_evidence"
    if action in _MUTATION_REQUIRED_PURPOSES and _compact(evidence) not in _compact(
        original_player_text
    ):
        return None, "mutation_evidence_not_in_original"
    if action in _STATE_CHANGE_POSITIVE_MARKERS and not _has_positive_state_change_intent(
        action,
        evidence=evidence,
    ):
        return None, "state_change_intent_not_explicit"
    raw_close_after_action = payload.get("close_after_action", False)
    if not isinstance(raw_close_after_action, bool):
        return None, "invalid_close_after_action"
    close_after_action = raw_close_after_action
    close_evidence = str(payload.get("close_evidence") or "").strip()[:120]
    if close_after_action:
        if action not in {"accept_pending", "reject_pending"}:
            return None, "close_after_action_not_allowed"
        if len(_compact(close_evidence)) < 2 or _compact(close_evidence) not in _compact(
            player_text
        ):
            return None, "ungrounded_close_evidence"
        if not _state_change_evidence_is_safe(
            "close_workshop",
            player_text=original_player_text,
            evidence=close_evidence,
        ):
            return None, "unsafe_close_evidence"
        if _compact(close_evidence) not in _compact(original_player_text):
            return None, "close_evidence_not_in_original"
        if not _has_positive_state_change_intent(
            "close_workshop",
            evidence=close_evidence,
        ):
            return None, "close_intent_not_explicit"
    elif close_evidence:
        return None, "close_evidence_without_action"

    raw_checks = payload.get("checks")
    checks = (
        tuple(
            str(value).strip()
            for value in raw_checks
            if isinstance(value, str) and str(value).strip()
        )
        if isinstance(raw_checks, list)
        else ()
    )
    if len(checks) != len(set(checks)) or any(
        check not in WORKSHOP_AGENT_CHECKS for check in checks
    ):
        return None, "invalid_checks"
    if action == "inspect" and not checks:
        return None, "inspection_check_required"
    if action != "inspect" and checks:
        return None, "checks_without_inspection"

    raw_speech = str(payload.get("speech") or "")
    speech = clean_output(raw_speech)
    if action in WORKSHOP_AGENT_DIRECT_ACTIONS:
        if not speech or "\n" in speech or len(speech) > 120 or not is_usable_output(speech):
            return None, "invalid_speech"
        if any(claim in speech for claim in _UNSAVED_ACTION_CLAIMS):
            return None, "false_persistence_claim"
        observation = details.get("tool_observation")
        validation_accepted = bool(
            isinstance(observation, dict)
            and observation.get("kind") == "revision_validation"
            and observation.get("status") == "proposed"
        )
        if not validation_accepted and any(claim in speech for claim in _UNFINISHED_SUCCESS_CLAIMS):
            return None, "false_revision_claim"
        observation = details.get("tool_observation")
        completed_checks = {
            str(value)
            for value in (
                observation.get("checks", [])
                if isinstance(observation, dict)
                and observation.get("kind") == "inspection"
                and observation.get("status") == "completed"
                else []
            )
            if isinstance(value, str)
        }
        for check, markers in _INSPECTION_CLAIM_MARKERS.items():
            if check not in completed_checks and any(marker in speech for marker in markers):
                return None, f"unverified_{check}_claim"
        requested_checks = {
            check
            for check, markers in _INSPECTION_REQUEST_MARKERS.items()
            if any(marker in player_text for marker in markers)
        }
        if action != "ask" and requested_checks - completed_checks:
            return None, "requested_inspection_not_completed"
    elif speech:
        return None, "speech_not_allowed_for_action"

    analysis = finalize_workshop_analysis_payload(
        {
            "intent": _analysis_intent_for_action(action),
            "confidence": confidence,
            "repair_requested": action == "propose_revision",
            "findings": payload.get("findings")
            if isinstance(payload.get("findings"), list)
            else [],
            "evaluation": {
                "found": False,
                "sentiment": "unknown",
                "scope": "unknown",
                "evidence": "",
                "confidence": 0.0,
            },
            "close_request": {
                "found": False,
                "scope": "unknown",
                "evidence": "",
                "confidence": 0.0,
            },
            "line_reference": payload.get("line_reference")
            if isinstance(payload.get("line_reference"), dict)
            else {
                "found": False,
                "concept_id": "unknown",
                "evidence": "",
                "confidence": 0.0,
            },
            "line_proposal": payload.get("line_proposal")
            if isinstance(payload.get("line_proposal"), dict)
            else {
                "found": False,
                "target_fragment": "",
                "replacement_text": "",
                "evidence": "",
                "confidence": 0.0,
            },
        },
        verse_lines=workshop_verse_lines(str(details.get("working_reading") or "")),
        player_text=player_text,
    )
    previous_findings = details.get("workshop_context")
    has_previous_findings = bool(
        isinstance(previous_findings, dict) and previous_findings.get("last_findings")
    )
    if action == "propose_revision" and not analysis.findings and not has_previous_findings:
        return None, "repair_target_required"
    if action == "stage_player_edit" and analysis.line_proposal is None:
        return None, "grounded_line_proposal_required"
    if action == "stage_player_edit" and not workshop_player_edit_is_grounded_in_original(
        analysis,
        original_player_text,
    ):
        return None, "player_edit_not_in_original"
    if action == "compare" and not details.get("pending_verse"):
        return None, "pending_required"
    return (
        WorkshopAgentStep(
            action=action,
            purpose=purpose,
            confidence=confidence,
            evidence=evidence,
            speech=speech,
            checks=checks,
            close_after_action=close_after_action,
            close_evidence=close_evidence,
            analysis=analysis,
        ),
        "accepted",
    )


def inspect_workshop(
    workshop: RecentHaikuWorkshop,
    checks: Iterable[str],
) -> dict[str, Any]:
    """現在の編集対象について、読み・音数・保存済み出典だけを測る。"""

    requested = tuple(dict.fromkeys(check for check in checks if check in WORKSHOP_AGENT_CHECKS))
    readings = workshop_verse_lines(workshop.editing_line())
    surfaces = workshop_verse_lines(workshop.editing_surface())
    if workshop.pending_revision:
        # pendingの行記録が欠けているとき、正本側の出典を新案の出典として
        # 流用しない。読み・音数は本文から測れるが、出典はunavailableにする。
        records = (
            workshop.pending_revision_lines if len(workshop.pending_revision_lines) == 3 else ()
        )
    else:
        records = workshop.current_lines
    record_by_index = {line.line_index: line for line in records}
    targets = (5, 7, 5)
    rows: list[dict[str, Any]] = []
    validation_codes: list[str] = []
    for index in range(3):
        reading = readings[index] if index < len(readings) else ""
        surface = surfaces[index] if index < len(surfaces) else reading
        row: dict[str, Any] = {"line_index": index}
        if "reading" in requested:
            row.update(
                {
                    "surface_text": surface,
                    "reading_text": reading or None,
                    "reading_status": "known" if reading else "unavailable",
                }
            )
            if not reading:
                validation_codes.append(f"reading_unavailable_line_{index}")
        if "meter" in requested:
            count = count_japanese_sounds(reading) if reading else None
            row.update(
                {
                    "mora_count": count,
                    "target_mora": targets[index],
                    "meter_delta": count - targets[index] if count is not None else None,
                    "meter_exact": count == targets[index] if count is not None else None,
                }
            )
            if count is None:
                validation_codes.append(f"meter_unavailable_line_{index}")
            elif count != targets[index]:
                validation_codes.append(f"meter_mismatch_line_{index}")
        if "source" in requested:
            record = record_by_index.get(index)
            atom_ids = list(record.source_atom_ids) if record is not None else []
            source_texts = [
                str(source.get("text") or "")[:240]
                for source in (record.source_atoms if record is not None else ())
                if isinstance(source, dict) and source.get("text")
            ]
            row.update(
                {
                    "source_status": "recorded" if atom_ids else "unavailable",
                    "source_atom_ids": atom_ids,
                    "source_texts": source_texts[:3],
                }
            )
            if not atom_ids:
                validation_codes.append(f"source_unavailable_line_{index}")
        rows.append(row)
    return {
        "kind": "inspection",
        "status": "completed",
        "verse_kind": "pending" if workshop.pending_revision else "canonical",
        "checks": list(requested),
        "lines": rows,
        "validation_codes": validation_codes,
    }


def record_workshop_agent_step(
    workshop: RecentHaikuWorkshop,
    step: WorkshopAgentStep,
    *,
    phase: str,
    outcome: str,
    validation_codes: Iterable[str] = (),
) -> dict[str, Any]:
    """次ターンと永続監査へ渡す、短い行動結果だけを保持する。"""

    row = {
        "phase": phase,
        "action": step.action,
        "purpose": step.purpose,
        "outcome": (outcome or "unknown")[:80],
        "validation_codes": [str(code)[:80] for code in validation_codes if code][:12],
        "checks": list(step.checks),
        "evidence": step.evidence[:120],
        "close_after_action": step.close_after_action,
        "close_evidence": step.close_evidence[:120],
    }
    workshop.agent_steps.append(row)
    if len(workshop.agent_steps) > WORKSHOP_AGENT_RECORD_LIMIT:
        del workshop.agent_steps[:-WORKSHOP_AGENT_RECORD_LIMIT]
    return row


def workshop_mutation_evidence_is_valid(
    action: str,
    *,
    player_text: str,
    evidence: str,
) -> bool:
    """採用・破棄・終了を、原文内の明示意思だけへ閉じる。"""

    return bool(
        action in _STATE_CHANGE_CONTRADICTIONS
        and _compact(evidence) in _compact(player_text)
        and _state_change_evidence_is_safe(
            action,
            player_text=player_text,
            evidence=evidence,
        )
        and _has_positive_state_change_intent(action, evidence=evidence)
    )


def workshop_player_edit_is_grounded_in_original(
    analysis: WorkshopAnalysis,
    player_text: str,
) -> bool:
    """局所置換語とその根拠が、解釈前の発話にも実在するか。"""

    proposal = analysis.line_proposal
    if proposal is None:
        return False
    raw_compact = _compact(player_text)
    return bool(
        _compact(proposal.replacement_text)
        and _compact(proposal.replacement_text) in raw_compact
        and _compact(proposal.evidence)
        and _compact(proposal.evidence) in raw_compact
    )


def _analysis_intent_for_action(action: str) -> str:
    return {
        "explain": "ask_meaning",
        "show_current": "show_current",
        "stage_player_edit": "propose_line_edit",
        "propose_revision": "request_repair",
    }.get(action, "other_haiku")


def _state_change_evidence_is_safe(
    action: str,
    *,
    player_text: str,
    evidence: str,
) -> bool:
    """疑問・引用・条件・反対意思を、モデルの状態変更候補から除く。"""

    source = f"{player_text}\n{evidence}".strip()
    if not source or "?" in evidence or "？" in evidence:
        return False
    if _evidence_is_quoted(player_text, evidence):
        return False
    local_context = _evidence_local_context(player_text, evidence)
    if "?" in local_context or "？" in local_context:
        return False
    if _STATE_CHANGE_REPORT.search(local_context) or _STATE_CHANGE_CONDITIONAL.search(
        local_context
    ):
        return False
    if _STATE_CHANGE_UNCERTAIN.search(local_context):
        return False
    contradiction = _STATE_CHANGE_CONTRADICTIONS.get(action)
    return contradiction is None or contradiction.search(source) is None


def _has_positive_state_change_intent(action: str, *, evidence: str) -> bool:
    """状態変更の根拠が、その行為自体を明示していることを確認する。"""

    if action in {"accept_pending", "reject_pending"}:
        expected = "accept" if action == "accept_pending" else "reject"
        if pending_revision_decision(evidence) == expected:
            return True
    marker = _STATE_CHANGE_POSITIVE_MARKERS.get(action)
    return marker is not None and marker.search(evidence) is not None


def _evidence_local_context(player_text: str, evidence: str) -> str:
    """根拠の直前直後だけを、疑問・伝聞・条件の検査へ使う。"""

    start = player_text.find(evidence)
    if start < 0:
        return evidence
    left = max(0, start - 24)
    right = min(len(player_text), start + len(evidence) + 24)
    for boundary in "。！？!?":
        previous = player_text.rfind(boundary, left, start)
        if previous >= 0:
            left = max(left, previous + 1)
        following = player_text.find(boundary, start + len(evidence), right)
        if following >= 0:
            right = min(right, following + 1)
    return player_text[left:right]


def _evidence_is_quoted(player_text: str, evidence: str) -> bool:
    """行名の引用は許し、操作根拠そのものが引用内にある場合だけ拒否する。"""

    stripped = evidence.strip()
    if any(
        len(stripped) >= 2 and stripped.startswith(opening) and stripped.endswith(closing)
        for opening, closing in (("「", "」"), ("『", "』"), ('"', '"'), ("'", "'"))
    ):
        return True
    starts = [match.start() for match in re.finditer(re.escape(evidence), player_text)]
    if not starts:
        return any(mark in evidence for mark in ("「", "」", "『", "』", '"', "'"))
    for start in starts:
        end = start + len(evidence)
        inside = False
        for opening, closing in (("「", "」"), ("『", "』")):
            opening_at = player_text.rfind(opening, 0, start + 1)
            closing_before = player_text.rfind(closing, 0, start + 1)
            closing_after = player_text.find(closing, end)
            if opening_at > closing_before and closing_after >= 0:
                inside = True
                break
        if not inside:
            for quote in ('"', "'"):
                before_count = player_text[:start].count(quote)
                if before_count % 2 == 1 and player_text.find(quote, end) >= 0:
                    inside = True
                    break
        if not inside:
            return False
    return True


def _compact(value: str) -> str:
    return re.sub(r"[\s、。！？!?・,:：『』「」]", "", value or "").lower()
