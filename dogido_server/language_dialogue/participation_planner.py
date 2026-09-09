"""次発話の意味予測と、話題断絶の限定抽出。状態変更と抑止判断は呼出元が行う。"""

from __future__ import annotations

import unicodedata

from pydantic import ValidationError

from dogido_server.llm.types import LLMFrontend, StructuredGenerationRequest
from .contracts import (
    ExpectedContinuation,
    ParticipationAssessment,
    ParticipationForecast,
)
from .participation import companion_reaction, is_playful_vocalization


def fallback_forecast(
    current_text: str,
    reply: str,
    *,
    response_status: str,
    needs_reaction: bool,
) -> ParticipationForecast:
    """モデル失敗時も次の見通しはログへ残すが、抑止根拠には使わない。"""
    if is_playful_vocalization(current_text):
        descriptions = [
            "同じような声遊びやリズムを続ける",
            "何の音や歌まねだったかを話す",
            "楽しいなど今の気分を伝える",
            "ドギドがどう思ったかを尋ねる",
            "Minecraftで音を出した出来事を話す",
        ]
    elif response_status in {"answer", "partial", "unsupported", "clarify"}:
        descriptions = [
            "今の説明へ相槌や感想を返す",
            "同じ対象について続きの質問をする",
            "対象や聞きたかった点を訂正する",
            "もっと易しい説明や言い換えを求める",
            "Minecraftの冒険へ話を戻す",
        ]
    else:
        descriptions = [
            "今の発話を続けたり言い換えたりする",
            "今の発話の理由やきっかけを話す",
            "気分や感想を伝える",
            "ドギドへ反応や意見を尋ねる",
            "Minecraftで今起きていることを話す",
        ]
    return ParticipationForecast(
        reaction=companion_reaction(current_text) if needs_reaction else "",
        patterns=[
            ExpectedContinuation(pattern_id=f"p{index}", description=description)
            for index, description in enumerate(descriptions, start=1)
        ],
    )


class ParticipationPlanner:
    """LLMには閉じた予測・分類だけを求め、結果を状態命令として扱わない。"""

    def __init__(self, llm: LLMFrontend):
        self.llm = llm

    def forecast(
        self,
        current_text: str,
        reply: str,
        *,
        response_status: str,
        handoff_topic: str = "",
        previous_accepted: dict[str, str] | None = None,
        needs_reaction: bool = False,
        cancelled=None,
    ) -> tuple[ParticipationForecast, str]:
        fallback = fallback_forecast(
            current_text,
            reply,
            response_status=response_status,
            needs_reaction=needs_reaction,
        )
        if cancelled is not None and cancelled():
            return fallback, "interrupted"
        request = StructuredGenerationRequest(
            kind="language_participation_forecast",
            fallback_value=fallback.model_dump(),
            details={
                "current": {"text": current_text},
                "dogido_reply": reply,
                "response_status": response_status,
                "handoff_topic": handoff_topic,
                "previous_accepted": dict(previous_accepted or {}),
                "needs_reaction": needs_reaction,
            },
            route="chat",
            temperature=0.1,
            max_tokens=500,
        )
        try:
            payload = dict(self.llm.generate_structured_json(request))
            status = payload.pop("__dogido_status", "accepted")
            if status != "accepted":
                return fallback, status
            forecast = ParticipationForecast.model_validate(payload, strict=True)
        except (ValidationError, TypeError, ValueError):
            return fallback, "invalid_payload"
        except Exception as exc:
            return fallback, f"generation_error:{type(exc).__name__}"
        if cancelled is not None and cancelled():
            return fallback, "interrupted"
        result_status = "accepted"
        if needs_reaction and not forecast.reaction.strip():
            forecast = forecast.model_copy(update={"reaction": fallback.reaction})
            result_status = "accepted_reaction_fallback"
        elif not needs_reaction and forecast.reaction:
            forecast = forecast.model_copy(update={"reaction": ""})
        return forecast, result_status

    def assess(
        self,
        text: str,
        *,
        turn_id: str,
        last_accepted: dict[str, str],
        forecast: ParticipationForecast,
        cancelled=None,
    ) -> tuple[ParticipationAssessment | None, str]:
        if cancelled is not None and cancelled():
            return None, "interrupted"
        details = {
            "current": {"turn_id": turn_id, "text": text},
            "last_accepted": dict(last_accepted),
            "expected_continuations": [pattern.model_dump() for pattern in forecast.patterns],
        }
        request = StructuredGenerationRequest(
            kind="language_participation_assessment",
            fallback_value={},
            details=details,
            route="chat",
            temperature=0.0,
            max_tokens=350,
        )
        try:
            payload = dict(self.llm.generate_structured_json(request))
            status = payload.pop("__dogido_status", "accepted")
            if status != "accepted":
                return None, status
            assessment = ParticipationAssessment.model_validate(payload, strict=True)
        except (ValidationError, TypeError, ValueError):
            return None, "invalid_payload"
        except Exception as exc:
            return None, f"generation_error:{type(exc).__name__}"
        if cancelled is not None and cancelled():
            return None, "interrupted"
        normalized_text = unicodedata.normalize("NFKC", text)
        if unicodedata.normalize("NFKC", assessment.evidence) not in normalized_text:
            return None, "ungrounded_evidence"
        expected_ids = {pattern.pattern_id for pattern in forecast.patterns}
        if any(pattern_id not in expected_ids for pattern_id in assessment.matched_pattern_ids):
            return None, "unknown_pattern_id"
        if assessment.relation == "possibly_not_addressed" and assessment.matched_pattern_ids:
            return None, "contradictory_match"
        return assessment, "accepted"
