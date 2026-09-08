"""会話モデルは解釈と返答を提案する。状態遷移の命令は受け取らない。"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TurnEvidence(StrictRecord):
    turn_id: str
    quote: Annotated[str, Field(min_length=1, max_length=500)]


class Interpretation(StrictRecord):
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
