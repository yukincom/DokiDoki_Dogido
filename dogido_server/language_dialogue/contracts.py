"""会話モデルは解釈と返答を提案する。状態遷移の命令は受け取らない。"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TurnEvidence(StrictRecord):
    turn_id: str
    quote: Annotated[str, Field(min_length=1, max_length=500)]


class Interpretation(StrictRecord):
    dialogue_act: Literal["information_request", "casual", "other"]
    question: Annotated[str, Field(max_length=500)]
    target: Annotated[str, Field(max_length=100)]
    facet: Literal[
        "grade",
        "mora_count",
        "reading",
        "meaning",
        "spelling",
        "grammar",
        "usage",
        "etymology",
        "translation",
        "comparison",
        "classification",
        "other",
    ]
    topic: Literal["language", "minecraft", "general", "unclear"]
    relation: Literal["new", "continue", "correct", "switch", "end", "resume"]
    target_status: Literal["explicit", "contextual", "ambiguous"]
    alternatives: Annotated[list[str], Field(max_length=3)]
    evidence: Annotated[list[TurnEvidence], Field(max_length=4)]
    search_terms: Annotated[list[str], Field(max_length=4)]
    clarification: Annotated[str, Field(max_length=160)]
    lookup_requested: bool = False
    web_query: Annotated[str, Field(max_length=140)] = ""


class GroundedReply(StrictRecord):
    status: Literal["answer", "partial", "unsupported"]
    text: Annotated[str, Field(min_length=1, max_length=420)]
    fact_ids: Annotated[list[str], Field(max_length=6)]
    application: Annotated[str, Field(max_length=300)]
    missing: Annotated[str, Field(max_length=200)]
    missing_kind: Literal["none", "evidence", "context"] = "none"
    clarification: Annotated[str, Field(max_length=160)] = ""


class PageQuote(StrictRecord):
    page_id: str
    quote: Annotated[str, Field(min_length=8, max_length=240)]


class ResearchIntent(StrictRecord):
    intent: Literal["report", "discuss", "uncertain", "continue", "return", "new_question", "acknowledge", "other"]
    evidence: Annotated[str, Field(min_length=1, max_length=500)]


class ResearchReading(StrictRecord):
    perspective: Annotated[str, Field(max_length=240)]
    quotes: Annotated[list[PageQuote], Field(max_length=2)]


class WebConsent(StrictRecord):
    intent: Literal["accept", "decline", "uncertain", "new_question"]
    evidence: Annotated[str, Field(min_length=1, max_length=500)]
    confidence: Annotated[float | int, Field(ge=0, le=1)]


class ExpectedContinuation(StrictRecord):
    pattern_id: Annotated[str, Field(pattern=r"^p[1-5]$")]
    description: Annotated[str, Field(min_length=1, max_length=120)]


class ParticipationForecast(StrictRecord):
    reaction: Annotated[str, Field(max_length=120)] = ""
    patterns: Annotated[list[ExpectedContinuation], Field(min_length=5, max_length=5)]

    @model_validator(mode="after")
    def _pattern_ids_are_unique(self):
        ids = [pattern.pattern_id for pattern in self.patterns]
        if len(ids) != len(set(ids)):
            raise ValueError("pattern_id must be unique")
        if set(ids) != {"p1", "p2", "p3", "p4", "p5"}:
            raise ValueError("pattern_id must contain p1 through p5")
        return self


class ParticipationAssessment(StrictRecord):
    relation: Literal["expected", "topic_shift", "possibly_not_addressed", "uncertain"]
    matched_pattern_ids: Annotated[list[str], Field(max_length=5)]
    topic_changed: bool
    clear_question: bool
    minecraft_topic: bool
    evidence: Annotated[str, Field(min_length=1, max_length=500)]
    confidence: Annotated[float | int, Field(ge=0, le=1)]
