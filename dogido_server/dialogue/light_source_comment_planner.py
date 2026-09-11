"""照明器具の所持数増加に、ひと言が必要かを決める限定 planner。

inventory snapshot から分かるのは「所持数が増えた」ことだけで、クラフトや設置の
成否までは分からない。この層はコードが許した候補から発話要否を一件だけ選び、
台詞生成・暗所状態の解除・ゲーム操作は行わない。
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Literal

from dogido_server.llm.types import StructuredGenerationRequest


LOGGER = logging.getLogger("uvicorn.error")

LightSourceCommentAction = Literal[
    "stay_silent",
    "acknowledge_supply_gain",
    "relief_after_darkness",
]

LIGHT_SOURCE_ABUNDANT_COUNT = 32
_MIN_SPEECH_CONFIDENCE = 0.78


@dataclass(frozen=True, slots=True)
class LightSourceCommentContext:
    previous_count: int
    current_count: int
    surroundings_reasonably_lit: bool
    severe_darkness: bool
    nearby_light_present: bool
    dark_push_context_before: bool
    dark_push_recovered: bool
    recent_comment: bool

    @property
    def delta(self) -> int:
        return max(0, self.current_count - self.previous_count)

    @property
    def first_supply(self) -> bool:
        return self.previous_count <= 0 < self.current_count

    @property
    def abundant_supply(self) -> bool:
        return self.current_count >= LIGHT_SOURCE_ABUNDANT_COUNT

    def allowed_actions(self) -> tuple[LightSourceCommentAction, ...]:
        """安全・連発防止の hard gate 後にモデルへ見せる候補を返す。"""

        actions: list[LightSourceCommentAction] = ["stay_silent"]
        if self.delta <= 0 or self.recent_comment:
            return tuple(actions)
        # 実際の明るさ回復でdark_pushが止まる場面は、所持数が多くても
        # 「基本は無言」の例外候補にできる。話すかはplannerが選ぶ。
        if self.dark_push_recovered and self.dark_push_context_before:
            actions.append("relief_after_darkness")
            return tuple(actions)
        if self.abundant_supply and self.surroundings_reasonably_lit:
            return tuple(actions)
        # 現在も危険な暗さなら、低優先コメントで暗所警告を横取りしない。
        if self.severe_darkness and not self.dark_push_recovered:
            return tuple(actions)
        # 初めて明かりを得た、またはまだ十分でない暗所だけを小さな節目にする。
        if self.first_supply or (
            not self.abundant_supply and not self.surroundings_reasonably_lit
        ):
            actions.append("acknowledge_supply_gain")
        return tuple(actions)

    def basis_rows(self) -> list[dict[str, str]]:
        rows = [
            {"basis_id": "light_source_gain_observed", "value": "true"},
            {
                "basis_id": "supply_before",
                "value": _supply_band(self.previous_count),
            },
            {
                "basis_id": "supply_after",
                "value": _supply_band(self.current_count),
            },
            {
                "basis_id": "surroundings_light",
                "value": (
                    "reasonably_lit"
                    if self.surroundings_reasonably_lit
                    else "not_reasonably_lit"
                ),
            },
            {
                "basis_id": "dangerous_darkness",
                "value": "true" if self.severe_darkness else "false",
            },
            {
                "basis_id": "nearby_light_present",
                "value": "true" if self.nearby_light_present else "false",
            },
            {
                "basis_id": "dark_push_context_before",
                "value": "true" if self.dark_push_context_before else "false",
            },
            {
                "basis_id": "dark_push_recovered",
                "value": "true" if self.dark_push_recovered else "false",
            },
            {
                "basis_id": "recent_light_comment",
                "value": "true" if self.recent_comment else "false",
            },
        ]
        if self.first_supply:
            rows.append({"basis_id": "first_light_supply", "value": "true"})
        return rows


@dataclass(frozen=True, slots=True)
class LightSourceCommentPlan:
    action: LightSourceCommentAction
    basis_ids: tuple[str, ...]
    confidence: float
    source: Literal["model", "fallback"]
    status: str

    @property
    def should_speak(self) -> bool:
        return self.action != "stay_silent"


def plan_light_source_comment(
    llm: object | None,
    *,
    context: LightSourceCommentContext,
) -> LightSourceCommentPlan:
    """コード事実から許された発話要否だけを一件選ぶ。失敗時は無言。"""

    basis_rows = context.basis_rows()
    allowed_actions = context.allowed_actions()
    fallback = LightSourceCommentPlan(
        action="stay_silent",
        basis_ids=("light_source_gain_observed",),
        confidence=0.0,
        source="fallback",
        status="code_silent" if allowed_actions == ("stay_silent",) else "fallback",
    )
    # コード事実だけで無言が確定しているとき、ゲームevent workerをモデル待ちにしない。
    if allowed_actions == ("stay_silent",):
        LOGGER.info(
            "light_source_comment_plan result=code_silent supply=%s surroundings_lit=%s "
            "severe_darkness=%s recent=%s",
            _supply_band(context.current_count),
            context.surroundings_reasonably_lit,
            context.severe_darkness,
            context.recent_comment,
        )
        return fallback
    generate = getattr(llm, "generate_structured_json", None)
    if not callable(generate):
        return fallback

    details: dict[str, Any] = {
        "allowed_actions": list(allowed_actions),
        "facts": basis_rows,
    }
    fallback_payload = {
        "action": fallback.action,
        "basis_ids": list(fallback.basis_ids),
        "confidence": fallback.confidence,
    }
    try:
        payload = generate(
            StructuredGenerationRequest(
                kind="light_source_comment_plan",
                fallback_value=fallback_payload,
                details=details,
                temperature=0.0,
                route="chat",
                max_tokens=160,
            )
        )
    except Exception as exc:
        LOGGER.warning(
            "light_source_comment_plan result=fallback reason=generation_error detail=%s",
            str(exc)[:180],
        )
        return fallback

    parsed = _parse_plan(payload, allowed_actions=allowed_actions, facts=basis_rows)
    if parsed is None:
        status = (
            str(payload.get("__dogido_status") or "invalid_payload")
            if isinstance(payload, dict)
            else "invalid_payload"
        )
        LOGGER.warning("light_source_comment_plan result=fallback reason=%s", status)
        return fallback
    LOGGER.warning(
        "light_source_comment_plan result=accepted action=%s basis=%s confidence=%.2f",
        parsed.action,
        ",".join(parsed.basis_ids),
        parsed.confidence,
    )
    return parsed


def _parse_plan(
    payload: object,
    *,
    allowed_actions: tuple[LightSourceCommentAction, ...],
    facts: list[dict[str, str]],
) -> LightSourceCommentPlan | None:
    if not isinstance(payload, dict):
        return None
    status = str(payload.get("__dogido_status") or "accepted")
    if status != "accepted":
        return None
    action = str(payload.get("action") or "")
    if action not in allowed_actions:
        return None
    basis_ids = payload.get("basis_ids")
    if not isinstance(basis_ids, list) or not 1 <= len(basis_ids) <= 3:
        return None
    allowed_basis = {row["basis_id"] for row in facts}
    normalized_basis: list[str] = []
    for value in basis_ids:
        basis_id = str(value or "").strip()
        if not basis_id or basis_id in normalized_basis or basis_id not in allowed_basis:
            return None
        normalized_basis.append(basis_id)
    if action == "relief_after_darkness" and "dark_push_recovered" not in normalized_basis:
        return None
    if action == "acknowledge_supply_gain" and not {
        "first_light_supply",
        "supply_before",
        "surroundings_light",
    }.intersection(normalized_basis):
        return None
    try:
        confidence = float(payload.get("confidence") or 0.0)
    except (TypeError, ValueError):
        return None
    if confidence < 0.0 or confidence > 1.0:
        return None
    if action != "stay_silent" and confidence < _MIN_SPEECH_CONFIDENCE:
        return None
    return LightSourceCommentPlan(
        action=action,  # type: ignore[arg-type]
        basis_ids=tuple(normalized_basis),
        confidence=confidence,
        source="model",
        status=status,
    )


def _supply_band(count: int) -> str:
    if count <= 0:
        return "empty"
    if count < 8:
        return "low"
    if count < LIGHT_SOURCE_ABUNDANT_COUNT:
        return "enough"
    return "abundant"
