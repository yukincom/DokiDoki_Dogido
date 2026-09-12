"""検証付きworkshop agent。外部モデル・Minecraft・TTSは使わない。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from dogido_server.config import Settings
from dogido_server.haiku.edit_contract import (
    LINE_EDIT_CONTRACT_VERSION,
    PLAYER_LINE_EDIT_CONTRACT_VERSION,
)
from dogido_server.haiku.generation import (
    WorkshopLineEdit,
    WorkshopRevisionResult,
)
from dogido_server.haiku.source_atoms import HaikuSourceAtom
from dogido_server.haiku.workshop_agent import (
    build_workshop_agent_details,
    finalize_workshop_agent_step,
    inspect_workshop,
)
from dogido_server.haiku.workshop import (
    PlayerLineReplacement,
    build_player_line_revision,
    open_from_emission,
)
from dogido_server.memory_types import HaikuEmission
from dogido_server.models import (
    AdapterSessionCreateRequest,
    EventDescriptor,
    EventName,
    GameEvent,
    MetaState,
    PlayerState,
)
from dogido_server.player_input.routing import route_player_input
from dogido_server.service import DogidoService


NOW = datetime(2026, 9, 11, 12, tzinfo=timezone.utc)
VERSE = "はるのかぜ\nひつじがあるく\nよるのつき"


def _atom(index: int, text: str) -> HaikuSourceAtom:
    return HaikuSourceAtom(
        atom_id=f"observation:agent:{index}",
        text=text,
        source_ref=f"observation:agent:{index}",
        field_path="observed_label",
        observation_role="test",
        kind="observation",
        claim_class="factual",
        claim_scopes=("observed_state",),
    )


def _emission() -> HaikuEmission:
    atoms = (_atom(0, "春の風"), _atom(1, "歩く羊"), _atom(2, "夜の月"))
    return HaikuEmission(
        created_at=NOW,
        text=VERSE,
        preface="ここで一句。",
        interpretation="春の風、歩く羊、夜の月を並べた句。",
        biome="plains",
        structure=None,
        time_phase="night",
        dimension="overworld",
        event_sequence=1,
        materials={
            "source_atoms": [atom.to_prompt_dict() for atom in atoms],
            "line_sources": [
                {
                    "line_index": index,
                    "text": line,
                    "atom_ids": [atoms[index].atom_id],
                    "sources": [atoms[index].to_prompt_dict()],
                }
                for index, line in enumerate(VERSE.splitlines())
            ],
        },
    )


def _agent_payload(
    action: str,
    *,
    evidence: str,
    speech: str = "",
    purpose: str = "continue_discussion",
    confidence: float = 0.95,
    checks: list[str] | None = None,
    close_after_action: bool = False,
    close_evidence: str = "",
    findings: list[dict[str, object]] | None = None,
    line_reference: dict[str, object] | None = None,
    line_proposal: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "action": action,
        "purpose": purpose,
        "confidence": confidence,
        "evidence": evidence,
        "speech": speech,
        "checks": checks or [],
        "close_after_action": close_after_action,
        "close_evidence": close_evidence,
        "findings": findings or [],
        "line_reference": line_reference
        or {
            "found": False,
            "concept_id": "unknown",
            "evidence": "",
            "confidence": 0.0,
        },
        "line_proposal": line_proposal
        or {
            "found": False,
            "target_fragment": "",
            "replacement_text": "",
            "evidence": "",
            "confidence": 0.0,
        },
    }


class _ScriptedAgentLLM:
    def __init__(self, payloads: list[dict[str, object]]) -> None:
        self.payloads = list(payloads)
        self.requests = []

    def generate_structured_json(self, request):
        self.requests.append(request)
        assert request.kind == "haiku_workshop_agent_step"
        assert self.payloads
        return self.payloads.pop(0)

    def generate_leaf_text(self, request):  # pragma: no cover - agent path must not use it
        raise AssertionError(f"unexpected leaf: {request.kind}")


class _AgentThenLegacyMutationLLM:
    """agent棄権後、旧pending分類器だけが補正後文から採用を返す。"""

    def __init__(self) -> None:
        self.requests = []

    def generate_structured_json(self, request):
        self.requests.append(request)
        if request.kind == "haiku_workshop_agent_step":
            return _agent_payload(
                "defer_to_legacy",
                purpose="other",
                confidence=0.0,
                evidence="",
            )
        if request.kind == "haiku_workshop_pending_decision":
            return {
                "action": "accept_pending",
                "confidence": 0.95,
                "evidence": "この案を採用して",
                "close_request": {
                    "found": False,
                    "scope": "unknown",
                    "evidence": "",
                    "confidence": 0.0,
                },
            }
        if request.kind == "haiku_workshop_intent":
            return {
                "intent": "soft_default",
                "confidence": 0.9,
                "repair_requested": False,
                "findings": [],
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
                "line_reference": {
                    "found": False,
                    "concept_id": "unknown",
                    "evidence": "",
                    "confidence": 0.0,
                },
                "line_proposal": {
                    "found": False,
                    "target_fragment": "",
                    "replacement_text": "",
                    "evidence": "",
                    "confidence": 0.0,
                },
            }
        raise AssertionError(f"unexpected structured request: {request.kind}")

    def generate_leaf_text(self, request):
        assert request.kind == "haiku_workshop_reply"
        return "その話、もう少し聞かせてな。"


def _service_session(tmp_path, payloads):
    service = DogidoService(
        Settings(
            llm_enabled=True,
            haiku_workshop_agent_enabled=True,
            llm_backend="noop",
            audio_enabled=False,
            memory_enabled=True,
            memory_dir=tmp_path / "memory",
        )
    )
    response = service.create_session(
        AdapterSessionCreateRequest(
            schema_version="2026-05-24",
            adapter_name="test",
            adapter_version="0",
            game="minecraft",
            player_name="p",
            capabilities=[],
        )
    )
    session = service.sessions[response.session_id]
    emission = _emission()
    assert service.memory is not None
    entry, _ = service.memory.save_agent_haiku(emission)
    session.last_haiku_emission = emission
    service._open_haiku_workshop(
        session,
        emission,
        entry_id=str(entry["id"]),
        now=NOW,
    )
    service.llm = _ScriptedAgentLLM(payloads)
    return service, session


def _send(
    service,
    session,
    text: str,
    sequence: int = 1,
    *,
    semantic_text: str | None = None,
):
    session.machine.player_input = route_player_input(text)
    if semantic_text is not None:
        session.machine.player_input.interpreted_text = semantic_text
    event = GameEvent(
        schema_version="2026-05-24",
        adapter="test",
        sequence=sequence,
        observed_at=NOW + timedelta(seconds=sequence),
        event=EventDescriptor(
            name=EventName.STATUS_SNAPSHOT,
            source_kind="inferred",
            priority_hint="normal",
            certainty="high",
        ),
        player=PlayerState(name="p"),
        meta=MetaState(user_text=text),
    )
    return service._haiku_workshop_actions(session, event)


def test_agent_explains_directly_without_legacy_classifiers(tmp_path):
    payload = _agent_payload(
        "explain",
        purpose="understand_meaning",
        evidence="羊ってどういう意味",
        speech="ここは、春の風の中を歩く羊を置いたんや。",
    )
    service, session = _service_session(tmp_path, [payload])

    actions = _send(service, session, "羊ってどういう意味？")

    assert actions[0].text == payload["speech"]
    assert [request.kind for request in service.llm.requests] == ["haiku_workshop_agent_step"]
    assert session.haiku_workshop is not None
    assert session.haiku_workshop.awaiting_meaning_ack
    assert session.haiku_workshop.pending_revision is None
    turns = service.memory._read_jsonl(service.memory.haiku_workshop_turns_path)
    assert turns[-1]["steps"][0]["action"] == "explain"
    assert "reasoning" not in json.dumps(turns[-1], ensure_ascii=False).lower()


def test_agent_record_separates_voice_original_from_conversation_interpretation(tmp_path):
    raw_text = "羊ってどういういみ"
    semantic_text = "羊ってどういう意味？"
    payload = _agent_payload(
        "explain",
        purpose="understand_meaning",
        evidence=semantic_text,
        speech="ここは、春の風の中を歩く羊を置いたんや。",
    )
    service, session = _service_session(tmp_path, [payload])

    actions = _send(
        service,
        session,
        raw_text,
        semantic_text=semantic_text,
    )

    assert actions[0].text == payload["speech"]
    turns = service.memory._read_jsonl(service.memory.haiku_workshop_turns_path)
    assert turns[-1]["player_text"] == raw_text
    assert turns[-1]["semantic_player_text"] == semantic_text


def test_agent_inspects_meter_and_source_then_answers_from_observation(tmp_path):
    inspect = _agent_payload(
        "inspect",
        purpose="evaluate_verse",
        evidence="音数と出典を確認",
        checks=["meter", "source"],
    )
    explain = _agent_payload(
        "explain",
        purpose="evaluate_verse",
        evidence="音数と出典を確認",
        speech="音数は五・七・五で、三行とも出典の記録があるで。",
    )
    service, session = _service_session(tmp_path, [inspect, explain])

    actions = _send(service, session, "音数と出典を確認して")

    assert "五・七・五" in actions[0].text
    assert len(service.llm.requests) == 2
    second = service.llm.requests[1]
    observation = second.details["tool_observation"]
    assert observation["kind"] == "inspection"
    assert [row["mora_count"] for row in observation["lines"]] == [5, 7, 5]
    assert all(row["source_status"] == "recorded" for row in observation["lines"])
    assert second.details["phase"] == "after_inspection"
    assert len(second.details["turn_steps"]) == 1
    assert second.details["workshop_context"]["recent_agent_steps"] == []
    assert session.haiku_workshop is not None
    assert not session.haiku_workshop.awaiting_meaning_ack
    assert service.memory._read_jsonl(service.memory.haiku_lessons_path) == []


def test_agent_records_second_step_failure_and_falls_back_to_real_inspection(tmp_path):
    inspect = _agent_payload(
        "inspect",
        purpose="evaluate_verse",
        evidence="音数を確認",
        checks=["meter"],
    )
    deferred = _agent_payload(
        "defer_to_legacy",
        purpose="other",
        evidence="音数を確認",
    )
    service, session = _service_session(tmp_path, [inspect, deferred])

    actions = _send(service, session, "音数を確認して")

    assert actions[0].text == "音数は上から5・7・5やで。どの行を一緒に見よか？"
    assert len(service.llm.requests) == 2
    turns = service.memory._read_jsonl(service.memory.haiku_workshop_turns_path)
    assert turns[-1]["steps"][0]["validation_codes"] == ["agent_step_action_not_allowed"]
    assert session.haiku_workshop is not None
    assert session.haiku_workshop.agent_steps[-1]["validation_codes"] == [
        "agent_step_action_not_allowed"
    ]


def test_failed_revision_is_returned_once_and_becomes_improvement_record(tmp_path, monkeypatch):
    finding = {
        "line_index": 1,
        "fragment": "ひつじがあるく",
        "problem": "off_scene",
        "note": "今の場面に羊はいない",
        "confidence": 0.95,
    }
    propose = _agent_payload(
        "propose_revision",
        purpose="improve_wording",
        evidence="中七が場面と違うから直して",
        findings=[finding],
    )
    ask = _agent_payload(
        "ask",
        purpose="improve_wording",
        evidence="中七が場面と違うから直して",
        speech="羊の意味を残せんかったから、残したい景色を一つ教えてな。",
    )
    service, session = _service_session(tmp_path, [propose, ask])
    feedback = {
        "line_failures": [
            {
                "line_index": 1,
                "failure_reasons": ["meaning_not_retained"],
                "assessment_comment": "羊の意味が消えている。",
            }
        ]
    }
    monkeypatch.setattr(
        "dogido_server.service.generate_workshop_revision",
        lambda *args, **kwargs: WorkshopRevisionResult(
            None,
            False,
            failure_reason="invalid_revision",
            retry_feedback=feedback,
        ),
    )

    actions = _send(service, session, "中七が場面と違うから直して")

    assert "意味を残せんかった" in actions[0].text
    assert session.haiku_workshop is not None
    assert session.haiku_workshop.display_line() == VERSE
    assert session.haiku_workshop.hud_editing
    assert session.haiku_workshop.hud_selected_line == 1
    assert service.workshop_hud.get(session.session_id)["selected_line"] == 1
    assert session.haiku_workshop.pending_revision is None
    second = service.llm.requests[1]
    observation = second.details["tool_observation"]
    assert observation["status"] == "rejected"
    assert "meaning_not_retained" in observation["validation_codes"]
    assert second.details["phase"] == "after_validation"
    turns = service.memory._read_jsonl(service.memory.haiku_workshop_turns_path)
    assert "meaning_not_retained" in turns[-1]["steps"][0]["validation_codes"]


def test_validated_revision_is_staged_but_not_saved_before_explicit_adoption(tmp_path, monkeypatch):
    revised = "はるのかぜ\nつよいあめふる\nよるのつき"
    finding = {
        "line_index": 1,
        "fragment": "ひつじがあるく",
        "problem": "off_scene",
        "note": "雨の場面と違う",
        "confidence": 0.95,
    }
    propose = _agent_payload(
        "propose_revision",
        purpose="improve_wording",
        evidence="中七を雨に合うよう直して",
        findings=[finding],
    )
    show = _agent_payload(
        "show_current",
        purpose="show_verse",
        evidence="中七を雨に合うよう直して",
    )
    service, session = _service_session(tmp_path, [propose, show])
    atoms = _emission().materials["source_atoms"]
    line_sources = tuple(
        {
            "line_index": index,
            "text": line,
            "atom_ids": [atoms[index]["atom_id"]],
            "sources": [atoms[index]],
        }
        for index, line in enumerate(revised.splitlines())
    )
    monkeypatch.setattr(
        "dogido_server.service.generate_workshop_revision",
        lambda *args, **kwargs: WorkshopRevisionResult(
            revised,
            True,
            line_sources=line_sources,
            base_text=VERSE,
            edits=(
                WorkshopLineEdit(
                    line_index=1,
                    expected_text="ひつじがあるく",
                    replacement_text="つよいあめふる",
                    atom_ids=(str(atoms[1]["atom_id"]),),
                ),
            ),
            edit_contract=LINE_EDIT_CONTRACT_VERSION,
        ),
    )

    actions = _send(service, session, "中七を雨に合うよう直して")

    assert "未採用の案" in actions[0].text
    assert revised in actions[0].text
    assert "その案で" in actions[0].text
    assert session.haiku_workshop is not None
    assert session.haiku_workshop.display_line() == VERSE
    assert session.haiku_workshop.pending_revision == revised
    revisions = service.memory._read_jsonl(service.memory.haiku_revisions_path)
    assert revisions == []


def test_natural_agent_adoption_still_requires_current_pending_cas(tmp_path):
    text = "前よりこの案を残しておいて"
    accept = _agent_payload(
        "accept_pending",
        purpose="adopt_pending",
        evidence=text,
    )
    service, session = _service_session(tmp_path, [accept])
    workshop = session.haiku_workshop
    assert workshop is not None
    staged = build_player_line_revision(
        workshop,
        PlayerLineReplacement(text="こひつじあるく", explicit_line_index=1),
    )
    assert staged.text is not None
    workshop.pending_revision = staged.text
    workshop.pending_revision_surface_text = staged.surface_text
    workshop.pending_revision_lines = staged.lines
    workshop.pending_revision_base_text = staged.base_text
    workshop.pending_revision_edits = [dict(edit) for edit in staged.edits]
    workshop.pending_revision_edit_contract = PLAYER_LINE_EDIT_CONTRACT_VERSION
    workshop.pending_revision_source = "player_line_confirmed"

    actions = _send(service, session, text)

    assert "覚え" in actions[0].text
    assert workshop.pending_revision is None
    assert workshop.display_line() == staged.text
    revisions = service.memory._read_jsonl(service.memory.haiku_revisions_path)
    assert len(revisions) == 1
    assert revisions[0]["base_text"] == VERSE
    assert revisions[0]["revised_text"] == staged.text


def test_agent_feedback_record_uses_pending_verse_as_reviewed_surface(tmp_path):
    pending = "はるのかぜ\nこひつじあるく\nよるのつき"
    text = "元の句とこの案を比べて"
    compare = _agent_payload(
        "compare",
        purpose="review_pending",
        evidence=text,
        speech="この案は真ん中が具体的になったけど、採るかはあなたの好みやで。",
    )
    service, session = _service_session(tmp_path, [compare])
    assert session.haiku_workshop is not None
    session.haiku_workshop.pending_revision = pending
    session.haiku_workshop.pending_revision_base_text = VERSE

    actions = _send(service, session, text)

    assert actions[0].text == compare["speech"]
    critiques = service.memory._read_jsonl(service.memory.haiku_critiques_path)
    assert critiques[-1]["surface_at_time"] == pending


def test_agent_can_save_pending_and_close_as_one_verified_action(tmp_path):
    text = "その案で終わりにしよう"
    accept = _agent_payload(
        "accept_pending",
        purpose="adopt_pending",
        evidence=text,
        close_after_action=True,
        close_evidence=text,
    )
    service, session = _service_session(tmp_path, [accept])
    workshop = session.haiku_workshop
    assert workshop is not None
    staged = build_player_line_revision(
        workshop,
        PlayerLineReplacement(text="こひつじあるく", explicit_line_index=1),
    )
    assert staged.text is not None
    workshop.pending_revision = staged.text
    workshop.pending_revision_surface_text = staged.surface_text
    workshop.pending_revision_lines = staged.lines
    workshop.pending_revision_base_text = staged.base_text
    workshop.pending_revision_edits = [dict(edit) for edit in staged.edits]
    workshop.pending_revision_edit_contract = PLAYER_LINE_EDIT_CONTRACT_VERSION
    workshop.pending_revision_source = "player_line_confirmed"

    actions = _send(service, session, text)

    assert "覚えといた" in actions[0].text
    assert session.haiku_workshop is None
    revisions = service.memory._read_jsonl(service.memory.haiku_revisions_path)
    assert len(revisions) == 1
    assert revisions[0]["revised_text"] == staged.text
    turns = service.memory._read_jsonl(service.memory.haiku_workshop_turns_path)
    assert turns[-1]["pending_after"] is None
    assert turns[-1]["steps"][-1]["outcome"] == "pending_saved_and_closed"
    assert turns[-1]["steps"][-1]["close_evidence"] == text


def test_agent_can_reject_pending_and_close_as_one_verified_action(tmp_path):
    text = "その案は捨てて、相談はここまで"
    reject = _agent_payload(
        "reject_pending",
        purpose="discard_pending",
        evidence="その案は捨てて",
        close_after_action=True,
        close_evidence="相談はここまで",
    )
    service, session = _service_session(tmp_path, [reject])
    workshop = session.haiku_workshop
    assert workshop is not None
    workshop.pending_revision = "はるのかぜ\nこひつじあるく\nよるのつき"
    workshop.pending_revision_base_text = VERSE

    actions = _send(service, session, text)

    assert "案は使わず" in actions[0].text
    assert session.haiku_workshop is None
    assert service.memory._read_jsonl(service.memory.haiku_revisions_path) == []
    turns = service.memory._read_jsonl(service.memory.haiku_workshop_turns_path)
    assert turns[-1]["steps"][-1]["outcome"] == "pending_rejected_and_closed"


def test_agent_can_close_natural_workshop_phrase_without_mutating_verse(tmp_path):
    text = "この相談に区切りをつけよう"
    close = _agent_payload(
        "close_workshop",
        purpose="finish_workshop",
        evidence=text,
    )
    service, session = _service_session(tmp_path, [close])

    actions = _send(service, session, text)

    assert actions[0].text == "おけ、この句の話はここまでや。"
    assert session.haiku_workshop is None
    assert service.memory._read_jsonl(service.memory.haiku_revisions_path) == []


def test_agent_adoption_discards_stale_edit_instead_of_saving_it(tmp_path):
    text = "前よりこの案を残しておいて"
    accept = _agent_payload(
        "accept_pending",
        purpose="adopt_pending",
        evidence=text,
    )
    service, session = _service_session(tmp_path, [accept])
    workshop = session.haiku_workshop
    assert workshop is not None
    workshop.pending_revision = "はるのかぜ\nこひつじあるく\nよるのつき"
    workshop.pending_revision_base_text = "別の元句"
    workshop.pending_revision_edit_contract = PLAYER_LINE_EDIT_CONTRACT_VERSION
    workshop.pending_revision_edits = [
        {
            "line_index": 1,
            "expected_text": "ひつじがあるく",
            "replacement_text": "こひつじあるく",
        }
    ]

    actions = _send(service, session, text)

    assert "合わんくなった" in actions[0].text
    assert workshop.pending_revision is None
    assert service.memory._read_jsonl(service.memory.haiku_revisions_path) == []


def test_agent_player_line_edit_uses_grounded_text_and_returns_confirmed_reading(
    tmp_path,
):
    text = "真ん中の『ひつじがあるく』を『こひつじあるく』にして"
    stage = _agent_payload(
        "stage_player_edit",
        purpose="improve_wording",
        evidence=text,
        line_reference={
            "found": True,
            "concept_id": "line_2",
            "evidence": "真ん中",
            "confidence": 0.95,
        },
        line_proposal={
            "found": True,
            "target_fragment": "ひつじがあるく",
            "replacement_text": "こひつじあるく",
            "evidence": "『こひつじあるく』にして",
            "confidence": 0.95,
        },
    )
    service, session = _service_session(tmp_path, [stage])

    actions = _send(service, session, text)

    assert actions[0].text == "はるのかぜ\nこひつじあるく\nよるのつき"
    assert session.haiku_workshop is not None
    assert session.haiku_workshop.pending_revision == actions[0].text
    assert service.memory._read_jsonl(service.memory.haiku_revisions_path) == []


def test_agent_save_and_close_failure_keeps_pending_and_workshop_open(tmp_path, monkeypatch):
    text = "その案で終わりにしよう"
    accept = _agent_payload(
        "accept_pending",
        purpose="adopt_pending",
        evidence="その案で",
        close_after_action=True,
        close_evidence="終わりにしよう",
    )
    service, session = _service_session(tmp_path, [accept])
    workshop = session.haiku_workshop
    assert workshop is not None
    staged = build_player_line_revision(
        workshop,
        PlayerLineReplacement(text="こひつじあるく", explicit_line_index=1),
    )
    assert staged.text is not None
    workshop.pending_revision = staged.text
    workshop.pending_revision_surface_text = staged.surface_text
    workshop.pending_revision_lines = staged.lines
    workshop.pending_revision_base_text = staged.base_text
    workshop.pending_revision_edits = [dict(edit) for edit in staged.edits]
    workshop.pending_revision_edit_contract = PLAYER_LINE_EDIT_CONTRACT_VERSION
    workshop.pending_revision_source = "player_line_confirmed"
    monkeypatch.setattr(
        service.memory,
        "save_haiku_feedback",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    actions = _send(service, session, text)

    assert "保存だけ失敗" in actions[0].text
    assert session.haiku_workshop is workshop
    assert workshop.open
    assert workshop.pending_revision == staged.text
    turns = service.memory._read_jsonl(service.memory.haiku_workshop_turns_path)
    assert turns[-1]["steps"][-1]["outcome"] == "pending_save_failed"


def test_agent_step_rejects_false_success_and_mutation_without_grounded_evidence():
    workshop = open_from_emission(_emission())
    details = build_workshop_agent_details(
        workshop,
        "中七を直したい",
        phase="decide",
    )
    false_claim = _agent_payload(
        "respond",
        purpose="improve_wording",
        evidence="中七を直したい",
        speech="中七はもう直したで。",
    )
    step, reason = finalize_workshop_agent_step(false_claim, details=details)
    assert step is None and reason == "false_revision_claim"

    unsupported_close = _agent_payload(
        "close_workshop",
        purpose="finish_workshop",
        evidence="終わろう",
    )
    step, reason = finalize_workshop_agent_step(unsupported_close, details=details)
    assert step is None and reason == "ungrounded_evidence"


def test_agent_step_rejects_negated_quoted_or_questioned_state_changes():
    workshop = open_from_emission(_emission())
    workshop.pending_revision = "はるのかぜ\nこひつじあるく\nよるのつき"

    cases = (
        (
            "accept_pending",
            "この案を採用しないで",
            "この案を採用",
            "adopt_pending",
        ),
        (
            "reject_pending",
            "その案は捨てないで",
            "その案は捨てないで",
            "discard_pending",
        ),
        (
            "accept_pending",
            "さっき『その案でいこう』って言ったよな",
            "その案でいこう",
            "adopt_pending",
        ),
    )
    for action, player_text, evidence, purpose in cases:
        details = build_workshop_agent_details(
            workshop,
            player_text,
            phase="decide",
        )
        step, reason = finalize_workshop_agent_step(
            _agent_payload(
                action,
                purpose=purpose,
                evidence=evidence,
            ),
            details=details,
        )
        assert step is None and reason == "unsafe_state_change_evidence"

    workshop.pending_revision = None
    for question in ("ここで終わる？", "ここで終わりでいいかな"):
        details = build_workshop_agent_details(workshop, question, phase="decide")
        step, reason = finalize_workshop_agent_step(
            _agent_payload(
                "close_workshop",
                purpose="finish_workshop",
                evidence=question,
            ),
            details=details,
        )
        assert step is None and reason == "unsafe_state_change_evidence"


def test_agent_step_requires_explicit_action_and_matching_mutation_purpose():
    workshop = open_from_emission(_emission())
    workshop.pending_revision = "はるのかぜ\nこひつじあるく\nよるのつき"

    details = build_workshop_agent_details(
        workshop,
        "今日は春らしいな",
        phase="decide",
    )
    step, reason = finalize_workshop_agent_step(
        _agent_payload(
            "accept_pending",
            purpose="adopt_pending",
            evidence="今日は春らしいな",
        ),
        details=details,
    )
    assert step is None and reason == "state_change_intent_not_explicit"

    details = build_workshop_agent_details(
        workshop,
        "この案を採用して",
        phase="decide",
    )
    step, reason = finalize_workshop_agent_step(
        _agent_payload(
            "accept_pending",
            purpose="review_pending",
            evidence="この案を採用して",
        ),
        details=details,
    )
    assert step is None and reason == "action_purpose_mismatch"

    details = build_workshop_agent_details(
        workshop,
        "その案で、明日は晴れそうやな",
        phase="decide",
    )
    step, reason = finalize_workshop_agent_step(
        _agent_payload(
            "accept_pending",
            purpose="adopt_pending",
            evidence="その案で",
            close_after_action=True,
            close_evidence="明日は晴れそうやな",
        ),
        details=details,
    )
    assert step is None and reason == "close_intent_not_explicit"

    workshop.pending_revision = None
    details = build_workshop_agent_details(
        workshop,
        "次の行へ行こう",
        phase="decide",
    )
    step, reason = finalize_workshop_agent_step(
        _agent_payload(
            "close_workshop",
            purpose="finish_workshop",
            evidence="次の行へ行こう",
        ),
        details=details,
    )
    assert step is None and reason == "unsafe_state_change_evidence"


def test_agent_mutations_require_evidence_from_uninterpreted_player_text():
    workshop = open_from_emission(_emission())
    workshop.pending_revision = "はるのかぜ\nこひつじあるく\nよるのつき"
    details = build_workshop_agent_details(
        workshop,
        "この案を採用して",
        original_player_text="この匂いを観察して",
        phase="decide",
    )

    step, reason = finalize_workshop_agent_step(
        _agent_payload(
            "accept_pending",
            purpose="adopt_pending",
            evidence="この案を採用して",
        ),
        details=details,
    )

    assert step is None and reason == "mutation_evidence_not_in_original"

    workshop.pending_revision = None
    details = build_workshop_agent_details(
        workshop,
        "真ん中は、こひつじあるくにして",
        original_player_text="真ん中は、小羊アルクにして",
        phase="decide",
    )
    step, reason = finalize_workshop_agent_step(
        _agent_payload(
            "stage_player_edit",
            purpose="improve_wording",
            evidence="真ん中",
            line_reference={
                "found": True,
                "concept_id": "line_2",
                "evidence": "真ん中",
                "confidence": 0.95,
            },
            line_proposal={
                "found": True,
                "target_fragment": "ひつじがあるく",
                "replacement_text": "こひつじあるく",
                "evidence": "こひつじあるくにして",
                "confidence": 0.95,
            },
        ),
        details=details,
    )
    assert step is None and reason == "player_edit_not_in_original"


def test_legacy_fallback_cannot_save_from_interpreted_only_adoption(tmp_path):
    raw_text = "この匂いを観察して"
    semantic_text = "この案を採用して"
    service, session = _service_session(tmp_path, [])
    workshop = session.haiku_workshop
    assert workshop is not None
    staged = build_player_line_revision(
        workshop,
        PlayerLineReplacement(text="こひつじあるく", explicit_line_index=1),
    )
    assert staged.text is not None
    workshop.pending_revision = staged.text
    workshop.pending_revision_surface_text = staged.surface_text
    workshop.pending_revision_lines = staged.lines
    workshop.pending_revision_base_text = staged.base_text
    workshop.pending_revision_edits = [dict(edit) for edit in staged.edits]
    workshop.pending_revision_edit_contract = PLAYER_LINE_EDIT_CONTRACT_VERSION
    workshop.pending_revision_source = "player_line_confirmed"
    service.llm = _AgentThenLegacyMutationLLM()

    actions = _send(
        service,
        session,
        raw_text,
        semantic_text=semantic_text,
    )

    assert actions
    assert workshop.pending_revision == staged.text
    assert workshop.display_line() == VERSE
    assert service.memory._read_jsonl(service.memory.haiku_revisions_path) == []
    assert [request.kind for request in service.llm.requests[:2]] == [
        "haiku_workshop_agent_step",
        "haiku_workshop_pending_decision",
    ]


def test_agent_step_allows_quoted_line_name_before_explicit_adoption():
    workshop = open_from_emission(_emission())
    workshop.pending_revision = "はるのかぜ\nこひつじあるく\nよるのつき"
    player_text = "『こひつじあるく』に直した案を採用して"
    details = build_workshop_agent_details(workshop, player_text, phase="decide")

    step, reason = finalize_workshop_agent_step(
        _agent_payload(
            "accept_pending",
            purpose="adopt_pending",
            evidence="直した案を採用して",
        ),
        details=details,
    )

    assert step is not None and reason == "accepted"


def test_agent_step_requires_real_inspection_before_reporting_meter_or_source():
    workshop = open_from_emission(_emission())
    details = build_workshop_agent_details(
        workshop,
        "音数と出典を確認して",
        phase="decide",
    )
    premature = _agent_payload(
        "explain",
        purpose="evaluate_verse",
        evidence="音数と出典を確認",
        speech="音数は五・七・五で、出典の記録もあるで。",
    )

    step, reason = finalize_workshop_agent_step(premature, details=details)

    assert step is None and reason == "unverified_meter_claim"

    inspected = inspect_workshop(workshop, ("meter", "source"))
    inspected_details = build_workshop_agent_details(
        workshop,
        "音数と出典を確認して",
        phase="after_inspection",
        observation=inspected,
    )
    step, reason = finalize_workshop_agent_step(premature, details=inspected_details)
    assert step is not None and reason == "accepted"

    subtle_unverified = _agent_payload(
        "respond",
        purpose="evaluate_verse",
        evidence="音数と出典を確認",
        speech="うん、きっちり合っとるで。",
    )
    step, reason = finalize_workshop_agent_step(subtle_unverified, details=details)
    assert step is None and reason == "requested_inspection_not_completed"


def test_agent_unrelated_decision_participates_in_existing_two_turn_drift(tmp_path):
    text = "これは句とは別の話やねん"
    unrelated = _agent_payload(
        "unrelated",
        purpose="other",
        evidence=text,
    )
    service, session = _service_session(tmp_path, [unrelated, unrelated])

    first = _send(service, session, text, sequence=1)
    assert first and first[0].route_owner == "player_chat"
    assert session.haiku_workshop is not None
    assert session.haiku_workshop.drift_count == 1
    assert session.haiku_workshop.dialogue.prompt_turns() == []
    registered = service._register_main_player_reply(
        session,
        session.machine.player_input,
        first,
        now=NOW + timedelta(seconds=1),
        source="text",
    )
    assert registered
    assert session.dialogue_turns.snapshot()[-1]["route"] == "casual"

    second = _send(service, session, text, sequence=2)
    assert second and second[0].route_owner == "player_chat"
    assert session.haiku_workshop is None


def test_agent_unrelated_does_not_advance_drift_without_main_chat_reply(tmp_path, monkeypatch):
    text = "これは句とは別の話やねん"
    unrelated = _agent_payload(
        "unrelated",
        purpose="other",
        evidence=text,
    )
    service, session = _service_session(tmp_path, [unrelated])
    monkeypatch.setattr(session.machine, "_render_player_chat_reply", lambda _event: None)

    actions = _send(service, session, text)

    assert actions == []
    assert session.haiku_workshop is not None
    assert session.haiku_workshop.drift_count == 0
    turns = service.memory._read_jsonl(service.memory.haiku_workshop_turns_path)
    assert turns[-1]["steps"][-1]["outcome"] == "main_chat_unavailable"


def test_inspection_reads_canonical_records_without_mutating_workshop():
    workshop = open_from_emission(_emission())
    before = workshop.display_line(), list(workshop.agent_steps)

    result = inspect_workshop(workshop, ("reading", "meter", "source"))

    assert [row["mora_count"] for row in result["lines"]] == [5, 7, 5]
    assert all(row["reading_status"] == "known" for row in result["lines"])
    assert result["validation_codes"] == []
    assert (workshop.display_line(), workshop.agent_steps) == before


def test_inspection_never_borrows_canonical_sources_for_unrecorded_pending():
    workshop = open_from_emission(_emission())
    workshop.pending_revision = "はるのかぜ\nこひつじあるく\nよるのつき"
    workshop.pending_revision_base_text = VERSE

    result = inspect_workshop(workshop, ("source",))

    assert result["verse_kind"] == "pending"
    assert [row["source_status"] for row in result["lines"]] == [
        "unavailable",
        "unavailable",
        "unavailable",
    ]
    assert result["validation_codes"] == [
        "source_unavailable_line_0",
        "source_unavailable_line_1",
        "source_unavailable_line_2",
    ]
