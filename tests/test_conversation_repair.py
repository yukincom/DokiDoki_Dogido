"""本人の訂正と生成された推測を区別し、短期会話への反映を検証する。"""
from copy import deepcopy

import pytest

from dogido_server.dialogue.conversation_repair import parse_conversation_repair, pending_repair
from dogido_server.dialogue.player_chat_planner import plan_player_chat
from dogido_server.dialogue_context import DialogueContext
from dogido_server.llm.structured_contracts import validate_structured_payload


HISTORY = [
    {"turn_id": "old", "role": "user", "text": "1位になるのは無理やな"},
    {"turn_id": "old:reply", "role": "assistant", "text": "1位になれんでもええやん。"},
]
CORRECTION = "違う、仲間になるのは無理ってこと"


def payload(action="repair_conversation", replacement="仲間になるのは無理ってこと", signal="違う"):
    return {
        "action": action, "focus": "本人の言い直し", "entity_query": "",
        "evidence": [{"turn_id": "current", "quote": signal or replacement},
                     {"turn_id": "old:reply", "quote": "1位になれんでもええやん。"}],
        "confidence": 0.95,
        "repair": {"target_turn_id": "old:reply", "target_quote": "1位になれんでもええやん。",
                   "signal_quote": signal, "replacement_quote": replacement},
    }


class FixedLLM:
    def __init__(self, value):
        self.value = value
        self.requests = []

    def generate_structured_json(self, request):
        self.requests.append(request)
        return deepcopy(self.value)


def plan(value, text=CORRECTION, history=None, **kwargs):
    llm = FixedLLM(value)
    result = plan_player_chat(llm, user_text=text, conversation_turns=history or HISTORY,
                             observation_summary="", observed_entities=[], **kwargs)
    return result, llm.requests[0].details


def test_explicit_repair_is_validated_by_schema_and_consumer():
    result, details = plan(payload())
    assert result.action == "repair_conversation"
    assert result.repair.current_text == CORRECTION
    assert validate_structured_payload("player_chat_plan", payload(), details=details).accepted


@pytest.mark.parametrize("field,value", [
    ("target_turn_id", "invented"), ("target_turn_id", "current"),
    ("target_quote", "一員になれない"), ("signal_quote", "聞き間違い"),
    ("replacement_quote", "一員になりたい"), ("replacement_quote", "違う"),
    ("replacement_quote", ""),
])
def test_invented_or_insufficient_repair_is_rejected_at_both_boundaries(field, value):
    invalid = payload()
    invalid["repair"][field] = value
    result, details = plan(invalid)
    assert result.repair is None
    assert not validate_structured_payload("player_chat_plan", invalid, details=details).accepted


@pytest.mark.parametrize("confidence", [0.81, float("nan"), float("inf")])
def test_untrusted_confidence_cannot_accept_repair(confidence):
    value = payload()
    value["confidence"] = confidence
    assert plan(value)[0].repair is None


def test_semantic_asr_interpretation_cannot_supply_missing_raw_evidence():
    assert plan(payload(), raw_user_text="仲間にはなれないかな")[0].repair is None


def test_quoted_negation_cannot_be_evidence_for_the_speakers_correction():
    text = "『違う』って言うキャラが好きなんだ"
    value = payload(replacement=text, signal="『違う』って言う")
    result, details = plan(value, text=text)
    assert result.repair is None
    assert "repair_conversation" not in details["allowed_actions"]
    assert not validate_structured_payload("player_chat_plan", value, details=details).accepted


def test_unquoted_signal_can_correct_quoted_words():
    text = "『1位』じゃなく『一員』だよ"
    value = payload(replacement="一員", signal="じゃなく")
    result, _ = plan(value, text=text)
    assert result.repair is not None


def test_natural_meaning_correction_is_not_intercepted_as_a_reading_lesson():
    from dogido_server.player_input import route_player_input

    for text in ("違う、村の一員にはなれないってこと", "村の一員にはなれないってこと"):
        assert route_player_input(text).reading_correction is None
    assert route_player_input("一員はいちいん").reading_correction is not None
    assert route_player_input("一員の読みはいちいん").reading_correction is not None


def test_workshop_disables_general_conversation_repair():
    result, details = plan(payload(), repair_enabled=False)
    assert result.repair is None
    assert "repair_conversation" not in details["allowed_actions"]


def test_clarification_waits_for_playback_then_accepts_specific_explanation():
    context = DialogueContext()
    context.add_player(HISTORY[0]["text"], turn_id="old")
    context.add_dogido(HISTORY[1]["text"], turn_id="old")
    result, _ = plan(payload("clarify_repair", ""), text="違う")
    context.add_player("違う", turn_id="question")
    assert context.record_repair("question", result.repair)
    assert not pending_repair(context.prompt_turns())
    context.add_dogido("どういう意味やった？", turn_id="question")
    assert pending_repair(context.prompt_turns())
    answer = "仲間にはなれないってこと"
    repaired, _ = plan(payload(replacement=answer, signal=""), text=answer,
                       history=context.prompt_turns())
    assert repaired.repair is not None
    context.add_player(answer, turn_id="answer")
    assert context.record_repair("answer", repaired.repair)
    assert context.prompt_turns()[0]["text"] == HISTORY[0]["text"]
    assert "仲間にはなれない" in context.prompt_blocks()["conversation_history"]
    assert not pending_repair(context.prompt_turns())
    for i in range(10):
        context.add_player(f"別の話{i}", turn_id=f"new-{i}")
    assert "repair_target_quote" not in str(context.prompt_turns())


def test_pending_repair_does_not_accept_ack_or_survive_topic_change():
    history = deepcopy(HISTORY)
    history.extend([
        {"turn_id": "q", "role": "user", "text": "違う",
         "repair_action": "clarify_repair", "repair_target_turn_id": "old:reply",
         "repair_target_quote": HISTORY[1]["text"]},
        {"turn_id": "q:reply", "role": "assistant", "text": "どういう意味やった？"},
    ])
    assert plan(payload(replacement="うん", signal=""), text="うん", history=history)[0].repair is None
    history.append({"turn_id": "new", "role": "user", "text": "家に帰ろう"})
    assert not pending_repair(history)
    assert plan(payload(replacement="仲間", signal=""), text="仲間", history=history)[0].repair is None


def test_absent_model_preserves_original_utterance_without_guessing():
    result = plan_player_chat(None, user_text="1位になるのは無理やな", conversation_turns=HISTORY,
                             observation_summary="", observed_entities=[])
    assert result.repair is None


def test_malformed_repair_context_does_not_raise():
    assert parse_conversation_repair("repair_conversation", payload()["repair"],
                                     {"history": [None], "current": "bad"}) is None


@pytest.mark.parametrize("playback_status", ["completed", "failed"])
def test_main_service_repair_obeys_playback_boundary(playback_status):
    from test_main_dialogue_integration import event, make_service

    service, session_id = make_service(llm_enabled=True)
    try:
        session = service.sessions[session_id]
        session.dialogue.add_player(HISTORY[0]["text"], turn_id="old")
        session.dialogue.add_dogido(HISTORY[1]["text"], turn_id="old")

        class ReplyLLM(FixedLLM):
            def generate_structured_json(self, request):
                if request.kind != "player_chat_plan":
                    return request.fallback_value
                return super().generate_structured_json(request)

            def generate_leaf_text(self, request):
                return request.fallback_text

        llm = ReplyLLM(payload("clarify_repair", ""))
        session.machine.llm = llm
        response = service.process_event(event(1, user_text="違う"), session_id=session_id)
        reply = next(a for a in response.actions if a.layer == "speech")
        assert reply.text == "「1位になれんでもええやん。」のところ、どういう意味やった？"
        assert session.dialogue.prompt_turns()[-1]["repair_action"] == "clarify_repair"
        assert not pending_repair(session.dialogue.prompt_turns())
        service._on_audio_playback_event({
            "session_id": session_id, "utterance_id": reply.utterance_id,
            "turn_id": reply.conversation_turn_id, "route_owner": reply.route_owner,
            "status": playback_status, "resolution": "", "text": reply.text,
        })
        service.process_event(event(2), session_id=session_id)
        assert bool(pending_repair(session.dialogue.prompt_turns())) == (playback_status == "completed")
        assert session.machine.player_chat_repair is None
        if playback_status == "completed":
            answer = "仲間になるのは無理ってこと"
            llm.value = payload(replacement=answer, signal="")
            service.process_event(event(10, user_text=answer), session_id=session_id)
            current = session.dialogue.prompt_turns()[-1]
            assert current["text"] == answer
            assert current["repair_action"] == "repair_conversation"
            assert current["repair_replacement_quote"] == answer
    finally:
        service.shutdown()
