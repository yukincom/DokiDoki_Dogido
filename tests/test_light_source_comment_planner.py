from __future__ import annotations

from dogido_server.dialogue.light_source_comment_planner import (
    LightSourceCommentContext,
    plan_light_source_comment,
)
from dogido_server.llm.prompts import build_messages
from dogido_server.llm.structured_contracts import validate_structured_payload
from dogido_server.llm.types import LeafGenerationRequest, StructuredGenerationRequest


class FixedPlannerLLM:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.requests = []

    def generate_structured_json(self, request):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        return dict(self.payload)


def _context(**overrides: object) -> LightSourceCommentContext:
    values: dict[str, object] = {
        "previous_count": 0,
        "current_count": 4,
        "surroundings_reasonably_lit": True,
        "severe_darkness": False,
        "nearby_light_present": False,
        "dark_push_context_before": False,
        "dark_push_recovered": False,
        "recent_comment": False,
    }
    values.update(overrides)
    return LightSourceCommentContext(**values)  # type: ignore[arg-type]


def test_abundant_supply_and_reasonable_light_only_allow_silence() -> None:
    llm = FixedPlannerLLM(
        {
            "action": "acknowledge_supply_gain",
            "basis_ids": ["light_source_gain_observed"],
            "confidence": 0.98,
            "__dogido_status": "accepted",
        }
    )

    plan = plan_light_source_comment(
        llm,
        context=_context(previous_count=44, current_count=45),
    )

    assert plan.action == "stay_silent"
    assert plan.source == "fallback"
    assert plan.status == "code_silent"
    assert llm.requests == []


def test_recent_comment_only_allows_silence() -> None:
    context = _context(
        surroundings_reasonably_lit=False,
        recent_comment=True,
    )

    assert context.allowed_actions() == ("stay_silent",)


def test_first_supply_can_be_acknowledged_with_valid_basis() -> None:
    llm = FixedPlannerLLM(
        {
            "action": "acknowledge_supply_gain",
            "basis_ids": ["first_light_supply", "supply_after"],
            "confidence": 0.91,
            "__dogido_status": "accepted",
        }
    )

    plan = plan_light_source_comment(llm, context=_context())

    assert plan.action == "acknowledge_supply_gain"
    assert plan.should_speak
    assert plan.basis_ids == ("first_light_supply", "supply_after")


def test_low_confidence_or_unknown_basis_fails_silent() -> None:
    low_confidence = FixedPlannerLLM(
        {
            "action": "acknowledge_supply_gain",
            "basis_ids": ["first_light_supply"],
            "confidence": 0.6,
            "__dogido_status": "accepted",
        }
    )
    invented_basis = FixedPlannerLLM(
        {
            "action": "acknowledge_supply_gain",
            "basis_ids": ["player_crafted_torches"],
            "confidence": 0.95,
            "__dogido_status": "accepted",
        }
    )

    assert plan_light_source_comment(low_confidence, context=_context()).action == "stay_silent"
    assert plan_light_source_comment(invented_basis, context=_context()).action == "stay_silent"


def test_actual_dark_push_recovery_can_select_relief() -> None:
    llm = FixedPlannerLLM(
        {
            "action": "relief_after_darkness",
            "basis_ids": ["dark_push_context_before", "dark_push_recovered"],
            "confidence": 0.93,
            "__dogido_status": "accepted",
        }
    )

    plan = plan_light_source_comment(
        llm,
        context=_context(
            surroundings_reasonably_lit=False,
            dark_push_context_before=True,
            dark_push_recovered=True,
        ),
    )

    assert plan.action == "relief_after_darkness"


def test_dark_push_recovery_remains_an_option_with_abundant_supply() -> None:
    context = _context(
        previous_count=44,
        current_count=45,
        dark_push_context_before=True,
        dark_push_recovered=True,
    )

    assert context.allowed_actions() == ("stay_silent", "relief_after_darkness")


def test_contract_rejects_disallowed_action_and_unknown_basis() -> None:
    details = {
        "allowed_actions": ["stay_silent"],
        "facts": [
            {"basis_id": "supply_after", "value": "abundant"},
            {"basis_id": "surroundings_light", "value": "reasonably_lit"},
        ],
    }
    disallowed = validate_structured_payload(
        "light_source_comment_plan",
        {
            "action": "acknowledge_supply_gain",
            "basis_ids": ["supply_after"],
            "confidence": 0.9,
        },
        details=details,
    )
    unknown = validate_structured_payload(
        "light_source_comment_plan",
        {
            "action": "stay_silent",
            "basis_ids": ["exact_torch_count"],
            "confidence": 0.9,
        },
        details=details,
    )
    missing_recovery_basis = validate_structured_payload(
        "light_source_comment_plan",
        {
            "action": "relief_after_darkness",
            "basis_ids": ["supply_after"],
            "confidence": 0.9,
        },
        details={
            "allowed_actions": ["stay_silent", "relief_after_darkness"],
            "facts": [
                {"basis_id": "supply_after", "value": "enough"},
                {"basis_id": "dark_push_recovered", "value": "true"},
            ],
        },
    )

    assert not disallowed.accepted
    assert "action:not_allowed" in disallowed.errors
    assert not unknown.accepted
    assert any(error.startswith("basis_ids:unknown=") for error in unknown.errors)
    assert not missing_recovery_basis.accepted
    assert "basis_ids:dark_push_recovered_required" in missing_recovery_basis.errors


def test_prompt_forbids_craft_inference_and_exact_count_commentary() -> None:
    messages = build_messages(
        StructuredGenerationRequest(
            kind="light_source_comment_plan",
            fallback_value={},
            details={
                "allowed_actions": ["stay_silent"],
                "facts": [
                    {"basis_id": "supply_after", "value": "abundant"},
                    {
                        "basis_id": "surroundings_light",
                        "value": "reasonably_lit",
                    },
                ],
            },
        )
    )
    content = messages[1]["content"]

    assert "クラフトした、置いた、拾った、持ち替えた" in content
    assert "発話文は生成せず" in content
    assert "supply_after=abundant" in content
    assert "/no_think" not in content

    leaf_messages = build_messages(
        LeafGenerationRequest(
            kind="light_source_gain",
            fallback_text="fallback",
            details={
                "player_name": "プレイヤー",
                "biome": "洞窟",
                "time_phase": "night",
                "comment_action": "acknowledge_supply_gain",
                "surroundings_light": "not_reasonably_lit",
            },
        )
    )
    leaf_content = leaf_messages[1]["content"]
    assert "所持数や増加数を数字・漢数字" in leaf_content
    assert "作った・クラフトした・置いた・拾ったとは言わない" in leaf_content
    assert "light_count" not in leaf_content
