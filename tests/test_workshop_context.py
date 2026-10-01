"""対話・見どころ・修正結果の受け渡し。ネットワーク・マイク・TTSは使わない。"""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from dogido_server.config import Settings
from dogido_server.haiku.generation import (
    WorkshopRevisionResult,
    generate_grounded_haiku,
    generate_workshop_revision,
)
from dogido_server.haiku.source_atoms import HaikuSourceAtom
from dogido_server.haiku.workshop import (
    WorkshopAnalysis,
    WorkshopEvaluation,
    WorkshopFinding,
    build_pending_revision_llm_details,
    build_workshop_intent_llm_details,
    open_from_emission,
    pause_workshop_for_combat,
    resume_workshop_after_combat,
)
from dogido_server.haiku.workshop_context import workshop_context_details
from dogido_server.haiku.verse import build_haiku_lines
from dogido_server.llm.prompts import build_messages
from dogido_server.llm.client import DogidoLLM
from dogido_server.llm.types import LeafGenerationRequest, StructuredGenerationRequest
from dogido_server.memory_types import HaikuEmission
from dogido_server.models import (
    AdapterSessionCreateRequest,
    EventDescriptor,
    EventName,
    GameEvent,
    MetaState,
    PlayerState,
    Position,
    WorldState,
)
from dogido_server.player_input.routing import route_player_input
from dogido_server.service import DogidoService


NOW = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
VERSE = "はるのかぜ\nひつじがあるく\nよるのつき"
SCENE = "春の風の中で歩く羊と、夜空に浮かぶ月の取り合わせ。"


def atoms():
    return tuple(
        HaikuSourceAtom(
            atom_id=f"observation:test:{i}",
            text=text,
            source_ref=f"observation:test:{i}",
            field_path="observed_label",
            observation_role="test",
            kind="observation",
            claim_class="factual",
            claim_scopes=("observed_state",),
        )
        for i, text in enumerate(("春の風", "歩く羊", "夜の月", "強い雨"))
    )


def emission():
    sources = atoms()
    return HaikuEmission(
        created_at=NOW,
        text=VERSE,
        interpretation=SCENE,
        preface="ここで一句。",
        biome="plains",
        structure=None,
        time_phase="night",
        dimension="overworld",
        event_sequence=1,
        materials={
            "source_atoms": [atom.to_prompt_dict() for atom in sources],
            "line_sources": [
                {
                    "line_index": i,
                    "text": text,
                    "atom_ids": [sources[i].atom_id],
                    "sources": [sources[i].to_prompt_dict()],
                }
                for i, text in enumerate(VERSE.splitlines())
            ],
        },
    )


@pytest.fixture
def workshop():
    return open_from_emission(emission())


def test_context_is_a_bounded_snapshot_and_preserves_materials(workshop):
    before = deepcopy(workshop.materials)
    for i in range(6):
        workshop.dialogue.add_player(f"質問{i}")
        workshop.dialogue.add_dogido(f"返答{i}")
    context = workshop_context_details(workshop)
    assert context["interpretation"] == SCENE
    assert "質問0" not in context["recent_dialogue"]
    assert "プレイヤー: 質問5" in context["recent_dialogue"]
    assert "ドギド: 返答5" in context["recent_dialogue"]
    assert len(context["recent_dialogue"].splitlines()) == 8
    context["source_atoms"][0]["text"] = "変更しても元の材料へ戻らない"
    assert workshop.materials == before


def test_context_survives_combat_but_not_a_new_poem(workshop):
    workshop.dialogue.add_player("ここはまだ意味が分からない")
    workshop.dialogue.add_dogido("もう少し一緒に考えよか。")
    before = workshop_context_details(workshop)["recent_dialogue"]
    pause_workshop_for_combat(workshop, now=NOW, hostile_types=["zombie"])
    resume_workshop_after_combat(
        workshop, now=NOW + timedelta(seconds=20), reason="disengaged", ask_confirmation=True
    )
    assert workshop_context_details(workshop)["recent_dialogue"] == before
    assert workshop_context_details(open_from_emission(emission()))["recent_dialogue"] == ""


def test_context_keeps_current_and_pending_separate_and_ignores_stale_feedback(workshop):
    workshop.pending_revision = "はるのかぜ\nあめつよくふる\nよるのつき"
    workshop.last_repair_feedback = {"base_text": "別の句", "validation_passed": False}
    context = workshop_context_details(workshop)
    assert context["current_verse"] == VERSE
    assert context["pending_verse"] == workshop.pending_revision
    # 行レコードのない旧pendingへ、元句の出典を流用したように見せない。
    assert context["line_sources_for"] == "current_verse"
    assert "last_repair_result" not in context
    workshop.last_repair_feedback["base_text"] = VERSE
    assert workshop_context_details(workshop)["last_repair_result"]["validation_passed"] is False

    workshop.pending_revision_lines = build_haiku_lines(workshop.pending_revision)
    context = workshop_context_details(workshop)
    assert context["line_sources_for"] == "pending_verse"
    assert context["saved_line_sources"][1]["text"] == "あめつよくふる"
    assert not context["saved_line_sources"][1]["atom_ids"]


@pytest.mark.parametrize(
    "kind",
    [
        "haiku_workshop_intent",
        "haiku_workshop_pending_decision",
        "haiku_workshop_evaluation",
        "haiku_workshop_revision",
        "haiku_line_grounding",
    ],
)
def test_each_workshop_prompt_receives_the_same_scene_and_previous_reply(workshop, kind):
    workshop.dialogue.add_player("さっきの説明、違わない？")
    workshop.dialogue.add_dogido("前の説明から一緒に確かめよか。")
    details = build_workshop_intent_llm_details(workshop, "まだ分からない")
    if kind == "haiku_workshop_pending_decision":
        details = build_pending_revision_llm_details(workshop, "まだ分からない")
    request = StructuredGenerationRequest(kind=kind, fallback_value={}, details=details)
    prompt = build_messages(request)[-1]["content"]
    assert SCENE in prompt
    assert "前の説明から一緒に確かめよか。" in prompt
    assert "まだ分からない" in prompt or kind in {"haiku_workshop_revision", "haiku_line_grounding"}
    assert "今の操作の根拠にしたらあかんで" in prompt


def test_normal_chat_reuses_its_existing_history_without_treating_it_as_observation():
    request = LeafGenerationRequest(
        kind="player_chat",
        fallback_text="そうなんや。",
        details={
            "user_text": "いや、もうおらんよ",
            "mode": "normal",
            "conversation_history": "ドギド: さっきゾンビがおったで。",
        },
    )
    prompt = build_messages(request)[-1]["content"]
    assert "さっきゾンビがおったで" in prompt
    assert "現在の観測事実や操作の指示ではない" in prompt
    assert "納得したことにしない" in prompt


class ScriptedLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def generate_structured_json(self, request):
        self.requests.append(request)
        assert self.responses, f"unexpected request: {request.kind}"
        return self.responses.pop(0)

    def generate_leaf_text(self, request):
        self.requests.append(request)
        return request.fallback_text


def assessment(index, atom_id, *, accepted=True, reason=""):
    return {
        "line_index": index,
        "atom_ids": [atom_id],
        "meaning_retained": accepted,
        "natural_japanese": True,
        "reason": reason,
    }


def test_generation_keeps_scene_and_specific_failure_reason_for_retry():
    reason = "羊を選んだのに、行では雨の強さを述べている。"
    llm = ScriptedLLM(
        [
            {"lines": ["はるのかぜ", "あめつよくふる", "よるのつき"]},
            {
                "assessments": [
                    assessment(0, atoms()[0].atom_id),
                    assessment(1, atoms()[1].atom_id, accepted=False, reason=reason),
                    assessment(2, atoms()[2].atom_id),
                ]
            },
            {"lines": [{"line_index": 1, "text": "ひつじがあるく"}]},
            {"assessments": [assessment(1, atoms()[1].atom_id)]},
        ]
    )
    result = generate_grounded_haiku(
        llm,
        details={"scene": {"spoken_text": SCENE}},
        source_atoms=atoms(),
        fallback_text="まとまらんかった。",
        max_tokens=192,
    )
    assert result.accepted and result.regeneration_rounds == 1
    assert result.text == VERSE
    assert SCENE in build_messages(llm.requests[1])[-1]["content"]
    retry = llm.requests[2]
    row = retry.details["current_lines"][1]
    assert row["assessment_comment"] == reason
    assert "meaning_not_retained" in row["failure_reasons"]
    assert reason in build_messages(retry)[-1]["content"]
    assert SCENE in build_messages(retry)[-1]["content"]


def test_workshop_retry_keeps_failed_text_and_comment_without_editing_other_lines(workshop):
    reason = "この行は羊ではなく雨を述べている。"
    edit = {
        "line_index": 1,
        "expected_text": "ひつじがあるく",
        "replacement_text": "あめつよくふる",
        "atom_ids": [atoms()[1].atom_id],
    }
    llm = ScriptedLLM(
        [
            {"lines": [edit]},
            {"assessments": [assessment(1, atoms()[1].atom_id, accepted=False, reason=reason)]},
            {
                "lines": [
                    {**edit, "replacement_text": "つよいあめふる", "atom_ids": [atoms()[3].atom_id]}
                ]
            },
            {"assessments": [assessment(1, atoms()[3].atom_id)]},
        ]
    )
    before = deepcopy(workshop.materials)
    result = generate_workshop_revision(
        llm,
        original_text=VERSE,
        target_indices=(1,),
        findings=(),
        source_atoms=atoms(),
        original_line_sources={i: (atoms()[i].atom_id,) for i in range(3)},
        details={"interpretation": SCENE, "workshop_context": workshop_context_details(workshop)},
        max_tokens=192,
    )
    assert result.accepted
    assert result.text == "はるのかぜ\nつよいあめふる\nよるのつき"
    assert reason in build_messages(llm.requests[2])[-1]["content"]
    assert "あめつよくふる" in build_messages(llm.requests[2])[-1]["content"]
    assert result.retry_feedback["line_failures"][0]["assessment_comment"] == reason
    assert workshop.materials == before


@pytest.fixture
def service_session(tmp_path):
    service = DogidoService(
        Settings(
            llm_enabled=False,
            audio_enabled=False,
            memory_enabled=False,
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
    service._open_haiku_workshop(session, emission(), entry_id=None, now=NOW)
    service.llm = ScriptedLLM([])
    return service, session


def send(service, session, text, sequence=1):
    session.machine.player_input = route_player_input(text)
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


def test_actual_workshop_reply_is_available_to_the_next_turn(service_session, monkeypatch):
    service, session = service_session
    captured = []

    def analyze(workshop, text):
        captured.append(build_workshop_intent_llm_details(workshop, text))
        return WorkshopAnalysis(intent="ask_meaning", confidence=0.95), "test"

    monkeypatch.setattr(service, "_analyze_workshop_feedback", analyze)
    first = send(service, session, "羊ってどういう意味？")
    second = send(service, session, "今の説明はどういうこと？", sequence=2)
    assert first and second
    history = captured[-1]["workshop_context"]["recent_dialogue"]
    assert "プレイヤー: 羊ってどういう意味？" in history
    assert first[0].text in history
    assert "今の説明はどういうこと？" not in history
    assert len(session.haiku_workshop.dialogue.conversation_lines()) == 4


def test_negative_evidence_does_not_become_understanding(service_session, monkeypatch):
    service, session = service_session
    ws = session.haiku_workshop
    ws.awaiting_meaning_ack = True
    ws.dialogue.add_player("この句はどういう意味？")
    ws.dialogue.add_dogido("羊を元にした行やで。")
    text = "だいぶ崩れてるからちょっとどう直したらいいのか分からないな"
    monkeypatch.setattr(
        service,
        "_analyze_workshop_feedback",
        lambda *_: (
            WorkshopAnalysis(
                intent="ack",
                confidence=0.95,
                evaluation=WorkshopEvaluation(
                    sentiment="negative",
                    scope="whole_verse",
                    evidence="だいぶ崩れてる",
                    confidence=0.95,
                ),
            ),
            "test",
        ),
    )
    replies = send(service, session, text)
    assert replies and "伝わってよかった" not in replies[0].text
    assert ws.open and not ws.awaiting_close_confirmation
    assert ws.display_line() == VERSE and ws.pending_revision is None
    assert service.memory is None


def test_failed_repair_result_reaches_next_turn_without_rewriting_the_poem(
    service_session, monkeypatch
):
    service, session = service_session
    ws = session.haiku_workshop
    before = deepcopy(ws.materials)
    feedback = {
        "line_failures": [
            {
                "line_index": 1,
                "failure_reasons": ["meaning_not_retained"],
                "assessment_comment": "羊と雨を取り違えている。",
            }
        ],
        "rejected_replacements": [{"line_index": 1, "replacement_text": "あめつよくふる"}],
    }
    monkeypatch.setattr(
        "dogido_server.service.generate_workshop_revision",
        lambda *a, **kw: WorkshopRevisionResult(
            None, False, failure_reason="invalid_revision", retry_feedback=feedback
        ),
    )
    analysis = WorkshopAnalysis(
        intent="request_repair",
        confidence=0.95,
        repair_requested=True,
        findings=(
            WorkshopFinding(
                line_index=1,
                fragment="ひつじがあるく",
                problem="off_scene",
                note="羊じゃない",
                confidence=0.95,
            ),
        ),
    )
    reply, result = service._workshop_revision_reply(ws, analysis, "羊じゃないから中七を直して")
    assert result == "invalid_revision" and "元の句はそのまま" in reply
    next_turn = build_workshop_intent_llm_details(ws, "さっきは何が合わなかったの？")
    last_result = next_turn["workshop_context"]["last_repair_result"]
    assert last_result["validation_passed"] is False
    assert last_result["retry_feedback"] == feedback
    assert ws.display_line() == VERSE and ws.pending_revision is None
    assert ws.materials == before


def test_independent_knowledge_reply_does_not_enter_workshop_history(service_session, monkeypatch):
    service, session = service_session
    ws = session.haiku_workshop
    ws.dialogue.add_player("まだこの句を考えてるよ")
    ws.dialogue.add_dogido("一緒に考えよか。")
    before = workshop_context_details(ws)
    monkeypatch.setattr(session.machine, "_render_knowledge_reply", lambda: "枕詞の説明やで。")
    replies = send(service, session, "枕詞って何？")
    assert replies and "枕詞" in replies[0].text
    assert workshop_context_details(ws) == before
    assert ws.open and ws.drift_count == 0


@pytest.mark.parametrize("text,paused", [("", False), ("/help", False), ("この句を直したい", True)])
def test_unhandled_input_is_not_recorded_as_a_workshop_exchange(service_session, text, paused):
    service, session = service_session
    ws = session.haiku_workshop
    if paused:
        pause_workshop_for_combat(ws, now=NOW, hostile_types=["zombie"])
    assert not send(service, session, text)
    assert workshop_context_details(ws)["recent_dialogue"] == ""


def mismatched_workshop():
    """石炭の句を丸石へ誤対応させた記録。個別語の本体ルールは増やさない。"""
    sources = tuple(
        replace(atom, text=text)
        for atom, text in zip(atoms(), ("石炭鉱石", "丸石", "草地", "昼の空"), strict=True)
    )
    text = "くろいいし\nわらべがわに\nひるのそら"
    materials = {
        "source_atoms": [atom.to_prompt_dict() for atom in sources],
        "line_sources": [
            {
                "line_index": i,
                "text": line,
                "atom_ids": [sources[i + 1].atom_id],
                "sources": [sources[i + 1].to_prompt_dict()],
            }
            for i, line in enumerate(text.splitlines())
        ],
    }
    return open_from_emission(
        replace(
            emission(),
            text=text,
            surface_text=None,
            reading_text=None,
            lines=(),
            interpretation="明るい昼の草地に、黒い石炭鉱石が埋もれている静かな対比。",
            materials=materials,
        )
    )


def test_meaning_correction_uses_scene_and_dialogue_without_rewriting_sources(
    service_session, monkeypatch
):
    service, session = service_session
    ws = mismatched_workshop()
    session.haiku_workshop = ws
    ws.dialogue.add_player("くろいいしって何？")
    ws.dialogue.add_dogido("丸石を元にした行やで。")
    before_materials, before_lines = deepcopy(ws.materials), deepcopy(ws.current_lines)
    replies = iter(
        [
            "黒い石は石炭のことやな、丸石って説明したんはオレの取り違えやったわ。",
            "その言葉は意味が通らへんな、オレの表現が崩れてしもたわ。",
        ]
    )
    captured = []
    llm = DogidoLLM(Settings(llm_enabled=True, llm_backend="openai_compatible"))

    def generate(request):
        captured.append(request)
        return next(replies)

    monkeypatch.setattr(llm, "_generate_backend_text", generate)
    service.llm = llm
    monkeypatch.setattr(
        service,
        "_analyze_workshop_feedback",
        lambda *_: (
            WorkshopAnalysis(intent="ask_meaning", confidence=0.95),
            "test",
        ),
    )
    first = send(service, session, "黒い石は石炭じゃない？どういう意味？")
    assert "取り違え" in first[0].text and "石炭" in first[0].text
    first_request = captured[0]
    assert first_request.kind == "haiku_workshop_reply" and first_request.route == "chat"
    assert first_request.details["reply_goal"] == "explain_meaning"
    context = first_request.details["workshop_context"]
    assert context["interpretation"] == ws.interpretation
    assert context["saved_line_sources"][0]["sources"][0]["text"] == "丸石"
    assert context["source_atoms"][0]["text"] == "丸石"  # 誤対応も隠さず比較へ渡す
    assert any(atom["text"] == "石炭鉱石" for atom in context["source_atoms"])
    assert "丸石を元にした行やで。" in context["recent_dialogue"]
    prompt = build_messages(first_request)[-1]["content"]
    assert "正解とは限らない" in prompt and "取り違えを認めて" in prompt
    assert "狙いの一言:" not in prompt
    assert "意味の通らない言葉に、それらしい意味や由来を作らない" in prompt
    assert "当時の材料を現在の視界だと言い換えない" in prompt
    second = send(service, session, "わらべがわにって何？", sequence=2)
    assert "意味が通らへん" in second[0].text
    assert first[0].text in captured[1].details["workshop_context"]["recent_dialogue"]
    assert len(captured) == 2
    assert ws.materials == before_materials and ws.current_lines == before_lines
    assert ws.pending_revision is None and ws.open
    assert service.memory is None


def test_service_routes_meaning_then_inventory_without_losing_the_poem(
    service_session, monkeypatch
):
    service, session = service_session
    ws = mismatched_workshop()
    session.haiku_workshop = ws
    before = deepcopy(ws.materials)
    llm = DogidoLLM(Settings(llm_enabled=True, llm_backend="openai_compatible"))
    captured = []

    def generate(request):
        captured.append(request)
        if request.kind == "haiku_workshop_reply":
            return "黒い石は石炭のことやな、前の説明は取り違えやったわ。"
        return "石炭は8個持っとるで。"

    monkeypatch.setattr(llm, "_generate_backend_text", generate)
    service.llm = session.machine.llm = llm
    monkeypatch.setattr(
        service,
        "_analyze_workshop_feedback",
        lambda *_: (
            WorkshopAnalysis(intent="ask_meaning", confidence=0.95),
            "test",
        ),
    )
    event = GameEvent(
        schema_version="2026-05-24",
        adapter="test",
        sequence=1,
        observed_at=NOW + timedelta(seconds=1),
        event=EventDescriptor(
            name=EventName.STATUS_SNAPSHOT,
            source_kind="system",
            priority_hint="background",
            certainty="high",
        ),
        player=PlayerState(
            name="p", position=Position(x=0, y=64, z=0), dimension="minecraft:overworld"
        ),
        world=WorldState(
            time_phase="day",
            time_of_day=6000,
            weather="clear",
            biome="plains",
            local_light=15,
            sky_visible=True,
            ceiling_height=20,
            enclosure_score=0,
            overhead_cover_type="none",
            danger_darkness_score=0,
        ),
        inventory={"coal": 8},
        meta=MetaState(user_text="黒い石は石炭じゃない？どういう意味？"),
    )
    result = service.process_event(event, session_id=session.session_id)
    assert any("取り違え" in (action.text or "") for action in result.actions)
    assert [request.kind for request in captured] == ["haiku_workshop_reply"]
    assert not session.machine.player_input.asks_inventory
    dialogue = workshop_context_details(ws)["recent_dialogue"]

    inventory_event = event.model_copy(
        update={
            "sequence": 2,
            "observed_at": NOW + timedelta(seconds=10),
            "meta": MetaState(user_text="石炭何個ある？"),
        }
    )
    result = service.process_event(inventory_event, session_id=session.session_id)
    assert any("8個" in (action.text or "") for action in result.actions)
    assert captured[-1].kind == "player_chat"
    assert session.machine.player_input.asks_inventory
    assert "石炭×8" in captured[-1].details["inventory_summary"]
    assert workshop_context_details(ws)["recent_dialogue"] == dialogue
    assert ws.materials == before and ws.open and ws.pending_revision is None


def test_meaning_explanation_does_not_adopt_or_rewrite_player_pending(service_session, monkeypatch):
    service, _ = service_session
    ws = mismatched_workshop()
    ws.pending_revision = "はるのかぜ\nわらべがわに\nひるのそら"
    ws.pending_revision_lines = build_haiku_lines(
        ws.pending_revision, provenance="player_line_draft"
    )
    ws.dialogue.add_player("さっきの案はその案で")
    ws.dialogue.add_dogido("前の案は覚えたで。")
    before = deepcopy(ws)
    captured = []

    def generate(request):
        captured.append(request)
        return "新しく入れてくれた言葉やな、春の風を表してるんやろか。"

    monkeypatch.setattr(service.llm, "generate_leaf_text", generate)
    reply, path = service._ask_meaning_workshop_reply(ws, "今の案の上五、どういう意味？")
    assert path == "collaborator_llm" and "入れてくれた言葉" in reply
    context = captured[0].details["workshop_context"]
    assert context["line_sources_for"] == "pending_verse"
    assert context["pending_verse"] == ws.pending_revision
    assert context["current_verse"] == ws.display_surface()
    prompt = build_messages(captured[0])[-1]["content"]
    assert "自分の発句時の意図として説明しない" in prompt
    assert "採用・終了も決めない" in prompt
    assert ws == before


@pytest.mark.parametrize("failure", ["disabled", "exception", "empty", "unusable"])
def test_meaning_failure_never_repeats_a_possibly_wrong_source(
    service_session, monkeypatch, failure
):
    service, _ = service_session
    ws = mismatched_workshop()
    before = deepcopy(ws)
    llm = DogidoLLM(Settings(llm_enabled=failure != "disabled", llm_backend="openai_compatible"))
    captured = []

    def generate(request):
        captured.append(request)
        if failure == "exception":
            raise RuntimeError("test backend unavailable")
        return "" if failure == "empty" else "This is not a Japanese explanation."

    monkeypatch.setattr(llm, "_generate_backend_text", generate)
    service.llm = llm
    reply, path = service._ask_meaning_workshop_reply(ws, "くろいいしって何？")
    assert path == "template" and "説明できへん" in reply
    assert "丸石" not in reply and "石炭" not in reply
    assert ws == before
    assert len(captured) == (0 if failure == "disabled" else 1)
