"""次発話予測・抑止候補の閉じた契約。実モデルや音声は使わない。"""

from copy import deepcopy

import pytest

from dogido_server.language_dialogue.participation import (
    companion_reaction,
    corrects_false_suppression,
    has_topic_shift_cue,
    is_obvious_minecraft_topic,
    is_obvious_question,
    is_playful_vocalization,
    resolves_side_conversation,
)
from dogido_server.language_dialogue.participation_planner import (
    ParticipationPlanner,
    fallback_forecast,
)


def forecast_payload(**changes):
    value = {
        "reaction": "歌みたいやな。ご機嫌やな〜。",
        "patterns": [
            {"pattern_id": f"p{index}", "description": f"意味上の候補{index}"}
            for index in range(1, 6)
        ],
    }
    value.update(changes)
    return value


def assessment_payload(**changes):
    value = {
        "relation": "expected",
        "matched_pattern_ids": ["p1"],
        "topic_changed": False,
        "clear_question": False,
        "minecraft_topic": False,
        "evidence": "その続きやで",
        "confidence": 0.92,
    }
    value.update(changes)
    return value


class LLM:
    def __init__(self, *values):
        self.values = list(values)
        self.requests = []

    def generate_structured_json(self, request):
        self.requests.append(deepcopy(request))
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def test_playful_first_reaction_and_fallback_forecast_have_five_semantic_patterns():
    text = "プリプリプリプリチプリキプリキ"
    result = fallback_forecast(
        text, "", response_status="casual_reaction", needs_reaction=True,
    )
    assert is_playful_vocalization(text)
    assert companion_reaction(text) == "歌かいな。ご機嫌やな〜。"
    assert result.reaction == "歌かいな。ご機嫌やな〜。"
    assert [item.pattern_id for item in result.patterns] == ["p1", "p2", "p3", "p4", "p5"]
    assert any("声遊び" in item.description for item in result.patterns)
    assert not is_playful_vocalization("どこどこにあるの？")


def test_forecast_uses_model_shape_and_discards_unrequested_reaction():
    llm = LLM(forecast_payload())
    forecast, status = ParticipationPlanner(llm).forecast(
        "今日は楽しかった", "そっか。", response_status="casual",
        needs_reaction=False,
    )
    assert status == "accepted" and forecast.reaction == ""
    assert len(forecast.patterns) == 5
    assert llm.requests[0].kind == "language_participation_forecast"
    assert llm.requests[0].details["current"] == {"text": "今日は楽しかった"}
    assert llm.requests[0].details["needs_reaction"] is False


def test_valid_patterns_remain_usable_when_only_required_reaction_falls_back():
    forecast, status = ParticipationPlanner(LLM(forecast_payload(reaction=""))).forecast(
        "今日は楽しかった", "", response_status="handoff", needs_reaction=True,
    )
    assert status == "accepted_reaction_fallback"
    assert forecast.reaction == "おっ、聞いてるで。"
    assert len(forecast.patterns) == 5


@pytest.mark.parametrize("value,status", [
    ({"reaction": "", "patterns": []}, "invalid_payload"),
    ({"__dogido_status": "schema_contract_error"}, "schema_contract_error"),
    (RuntimeError("test"), "generation_error:RuntimeError"),
])
def test_invalid_forecast_uses_untrusted_fallback(value, status):
    forecast, actual = ParticipationPlanner(LLM(value)).forecast(
        "プリプリ", "", response_status="casual", needs_reaction=True,
    )
    assert actual == status
    assert len(forecast.patterns) == 5 and forecast.reaction


def test_assessment_requires_current_evidence_and_known_prediction_ids():
    base = fallback_forecast("前の話", "聞いてるで。", response_status="casual", needs_reaction=False)
    planner = ParticipationPlanner(LLM(
        assessment_payload(evidence="発話にない"),
        assessment_payload(matched_pattern_ids=["p9"]),
        assessment_payload(),
    ))
    assert planner.assess(
        "その続きやで", turn_id="t1", last_accepted={}, forecast=base,
    )[1] == "ungrounded_evidence"
    assert planner.assess(
        "その続きやで", turn_id="t2", last_accepted={}, forecast=base,
    )[1] == "unknown_pattern_id"
    accepted, status = planner.assess(
        "その続きやで", turn_id="t3", last_accepted={}, forecast=base,
    )
    assert status == "accepted" and accepted.matched_pattern_ids == ["p1"]


@pytest.mark.parametrize("text", ["ところで晩ごはんやで", "さて、次の話", "そういえばさ"])
def test_explicit_topic_shift_cues_are_closed_and_observable(text):
    assert has_topic_shift_cue(text)


def test_fail_open_question_minecraft_and_resolution_cues_cover_observed_voice_forms():
    assert is_obvious_question("掘るっていう漢字は何年生で習うのかな")
    assert is_obvious_minecraft_topic("今エンダーマン")
    assert resolves_side_conversation("うるさくてごめんね")
    assert resolves_side_conversation("待たせたね")
    assert corrects_false_suppression("ドギドに言ったんだけど")
    assert corrects_false_suppression("聞いてますか？")
    assert not corrects_false_suppression("ドギドの声が聞こえた")
