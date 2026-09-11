from __future__ import annotations

from dogido_server.dialogue.player_chat_planner import (
    PlayerChatPlan,
    PlayerChatPlanEvidence,
    fixed_grounded_player_chat_reply,
    ground_player_chat_entity,
    plan_player_chat,
)
from dogido_server.entry_catalog import find_catalog_topics
from dogido_server.llm.prompts import build_messages
from dogido_server.llm.structured_contracts import validate_structured_payload
from dogido_server.llm.types import StructuredGenerationRequest
from dogido_server.player_chat_policy import filter_usable_topic_hits


class FixedPlannerLLM:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.requests = []

    def generate_structured_json(self, request):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        return dict(self.payload)


def test_planner_resolves_continuation_before_assistant_world_claim() -> None:
    llm = FixedPlannerLLM(
        {
            "action": "continue_conversation",
            "focus": "プレイヤーの安心した感想",
            "entity_query": "",
            "evidence": [{"turn_id": "current", "quote": "大丈夫そうですね"}],
            "confidence": 0.94,
            "__dogido_status": "accepted",
        }
    )
    plan = plan_player_chat(
        llm,
        user_text="よしよし、大丈夫そうですね",
        conversation_turns=[
            {"turn_id": "t1", "role": "user", "text": "こっちはおるよ"},
            {
                "turn_id": "t1:reply",
                "role": "assistant",
                "text": "ラバおるなら大丈夫そうやな",
            },
        ],
        observation_summary="",
        observed_entities=[],
    )

    assert plan.action == "continue_conversation"
    assert not plan.requests_catalog
    assert llm.requests[0].kind == "player_chat_plan"


def test_fallback_does_not_turn_plain_presence_report_into_question() -> None:
    for text in (
        "ラバがいる",
        "ラバがまだいる",
        "ラバがいない",
        "前哨基地がある",
        "前哨基地がない",
        "前哨基地があるのはタイガや",
    ):
        plan = plan_player_chat(
            None,
            user_text=text,
            conversation_turns=[],
            observation_summary="",
            observed_entities=[],
        )

        assert plan.action == "continue_conversation"
        assert not plan.requests_catalog


def test_inventory_routing_rejects_model_continuation_action() -> None:
    llm = FixedPlannerLLM(
        {
            "action": "continue_conversation",
            "focus": "直近の会話の続き",
            "entity_query": "",
            "evidence": [{"turn_id": "current", "quote": "石炭何個ある？"}],
            "confidence": 0.95,
            "__dogido_status": "accepted",
        }
    )

    plan = plan_player_chat(
        llm,
        user_text="石炭何個ある？",
        conversation_turns=[],
        observation_summary="",
        observed_entities=[],
        inventory_question=True,
    )

    assert plan.action == "answer_observation"
    assert not plan.requests_catalog


def test_contract_rejects_entity_action_for_inventory_routing_hint() -> None:
    details = {
        "allowed_actions": ["identify_entity", "answer_observation"],
        "history": [],
        "current": {
            "turn_id": "current",
            "role": "user",
            "text": "石炭何個ある？",
        },
        "routing_hints": {"inventory_question": True, "sound_question": False},
    }
    result = validate_structured_payload(
        "player_chat_plan",
        {
            "action": "identify_entity",
            "focus": "石炭の同定",
            "entity_query": "石炭",
            "evidence": [{"turn_id": "current", "quote": "石炭何個ある？"}],
            "confidence": 0.95,
        },
        details=details,
    )

    assert not result.accepted
    assert "action:routing_hint_requires_answer_observation" in result.errors


def test_explicit_presence_question_cannot_be_downgraded_to_continuation() -> None:
    llm = FixedPlannerLLM(
        {
            "action": "continue_conversation",
            "focus": "ラバの話の続き",
            "entity_query": "",
            "evidence": [{"turn_id": "current", "quote": "ラバいる？"}],
            "confidence": 0.95,
            "__dogido_status": "accepted",
        }
    )

    plan = plan_player_chat(
        llm,
        user_text="ラバいる？",
        conversation_turns=[],
        observation_summary="",
        observed_entities=[],
    )

    assert plan.action == "check_entity_presence"
    assert plan.source == "fallback"


def test_plain_presence_report_cannot_be_promoted_by_model() -> None:
    llm = FixedPlannerLLM(
        {
            "action": "check_entity_presence",
            "focus": "ラバの在否",
            "entity_query": "ラバ",
            "evidence": [{"turn_id": "current", "quote": "ラバがいる"}],
            "confidence": 0.95,
            "__dogido_status": "accepted",
        }
    )

    plan = plan_player_chat(
        llm,
        user_text="ラバがいる",
        conversation_turns=[],
        observation_summary="",
        observed_entities=[],
    )

    assert plan.action == "continue_conversation"
    assert plan.source == "fallback"


def test_entity_presence_is_grounded_without_treating_catalog_as_observation() -> None:
    plan = plan_player_chat(
        None,
        user_text="近くにラバがいるの？",
        conversation_turns=[],
        observation_summary="",
        observed_entities=[],
    )
    grounding = ground_player_chat_entity(
        plan,
        topic_hits=filter_usable_topic_hits(find_catalog_topics(plan.entity_query)),
        observed_entities=[],
    )

    assert plan.action == "check_entity_presence"
    assert grounding.candidate_ids == ("mule",)
    assert grounding.status == "not_observed"
    reply = fixed_grounded_player_chat_reply(plan, grounding)
    assert reply == "今の観測では、ラバは確認できてへんわ。"
    assert "いない" not in reply


def test_entity_presence_accepts_matching_code_observation() -> None:
    plan = plan_player_chat(
        None,
        user_text="近くにラバがいるの？",
        conversation_turns=[],
        observation_summary="近くの生き物: ラバ",
        observed_entities=[{"entity_id": "mule", "label": "ラバ"}],
    )
    grounding = ground_player_chat_entity(
        plan,
        topic_hits=filter_usable_topic_hits(find_catalog_topics(plan.entity_query)),
        observed_entities=[{"entity_id": "mule", "label": "ラバ"}],
    )

    assert grounding.status == "observed"
    assert grounding.observed_ids == ("mule",)
    assert fixed_grounded_player_chat_reply(plan, grounding) == ""


def test_structure_presence_fallback_is_grounded_as_the_named_structure() -> None:
    plan = plan_player_chat(
        None,
        user_text="前哨基地ある？",
        conversation_turns=[],
        observation_summary="構造物: ピリジャーぜんしょう基地",
        observed_entities=[
            {"entity_id": "pillager_outpost", "label": "ピリジャーぜんしょう基地"}
        ],
    )
    grounding = ground_player_chat_entity(
        plan,
        topic_hits=filter_usable_topic_hits(find_catalog_topics(plan.entity_query)),
        observed_entities=[
            {"entity_id": "pillager_outpost", "label": "ピリジャーぜんしょう基地"}
        ],
    )

    assert plan.action == "check_entity_presence"
    assert grounding.status == "observed"
    assert grounding.observed_ids == ("pillager_outpost",)


def test_related_observed_mob_does_not_prove_named_structure_presence() -> None:
    plan = plan_player_chat(
        None,
        user_text="前哨基地ある？",
        conversation_turns=[],
        observation_summary="近くの生き物: ピリジャー",
        observed_entities=[{"entity_id": "pillager", "label": "ピリジャー"}],
    )
    grounding = ground_player_chat_entity(
        plan,
        topic_hits=filter_usable_topic_hits(find_catalog_topics(plan.entity_query)),
        observed_entities=[{"entity_id": "pillager", "label": "ピリジャー"}],
    )

    assert grounding.candidate_ids == ("pillager_outpost",)
    assert grounding.status == "not_observed"
    assert grounding.observed_ids == ()
    assert "ピリジャーぜんしょう基地は確認できてへん" in fixed_grounded_player_chat_reply(
        plan,
        grounding,
    )


def test_previous_reply_correction_stays_fixed_when_entity_is_now_observed() -> None:
    plan = PlayerChatPlan(
        action="correct_previous_reply",
        focus="過去の在否断言の訂正",
        entity_query="ラバ",
        evidence=(
            PlayerChatPlanEvidence(
                turn_id="old:reply",
                quote="ラバがすぐそこにおるで",
            ),
            PlayerChatPlanEvidence(
                turn_id="current",
                quote="さっきラバおるって言ったけど違うやん",
            ),
        ),
        confidence=0.98,
        source="model",
        status="accepted",
    )
    grounding = ground_player_chat_entity(
        plan,
        topic_hits=filter_usable_topic_hits(find_catalog_topics("ラバ")),
        observed_entities=[{"entity_id": "mule", "label": "ラバ"}],
    )

    reply = fixed_grounded_player_chat_reply(plan, grounding)
    assert grounding.status == "observed"
    assert "断言しすぎた" in reply
    assert "今の観測ではラバを確認できとる" in reply


def test_contract_rejects_non_exact_evidence_and_ungrounded_correction() -> None:
    details = {
        "allowed_actions": [
            "continue_conversation",
            "check_entity_presence",
            "identify_entity",
            "answer_observation",
            "clarify_reference",
            "correct_previous_reply",
        ],
        "history": [
            {"turn_id": "t1:reply", "role": "assistant", "text": "ラバがおるで"}
        ],
        "current": {"turn_id": "current", "role": "user", "text": "それ違うやん"},
    }
    invented = validate_structured_payload(
        "player_chat_plan",
        {
            "action": "continue_conversation",
            "focus": "訂正",
            "entity_query": "",
            "evidence": [{"turn_id": "current", "quote": "違うよ"}],
            "confidence": 0.9,
        },
        details=details,
    )
    missing_assistant = validate_structured_payload(
        "player_chat_plan",
        {
            "action": "correct_previous_reply",
            "focus": "在否の訂正",
            "entity_query": "それ",
            "evidence": [{"turn_id": "current", "quote": "それ違うやん"}],
            "confidence": 0.9,
        },
        details=details,
    )

    assert not invented.accepted
    assert "evidence.0.quote:not_exact" in invented.errors
    assert not missing_assistant.accepted
    assert "evidence:assistant_required" in missing_assistant.errors


def test_planner_prompt_marks_user_reports_and_assistant_claims_non_observational() -> None:
    messages = build_messages(
        StructuredGenerationRequest(
            kind="player_chat_plan",
            fallback_value={},
            details={
                "allowed_actions": ["continue_conversation"],
                "history": [
                    {
                        "turn_id": "t1:reply",
                        "role": "assistant",
                        "text": "ラバがおるで",
                    }
                ],
                "current": {
                    "turn_id": "current",
                    "role": "user",
                    "text": "大丈夫そうですね",
                },
                "observations": {"summary": "", "observed_entities": []},
            },
        )
    )
    content = messages[1]["content"]

    assert "assistant発話" in content
    assert "世界事実の根拠ではない" in content
    assert "本人の報告" in content
    assert "現在観測済みとは限らない" in content
    assert "単語や形容だけで対象を推定しない" in content
    assert "/no_think" not in content
