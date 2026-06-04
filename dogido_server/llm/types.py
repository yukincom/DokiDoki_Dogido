# llm/types.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(slots=True)
class LeafGenerationRequest:
    kind: str
    fallback_text: str
    details: dict[str, Any]
    temperature: float = 0.2
    route: str | None = None
    max_tokens: int | None = None


@dataclass(slots=True)
class StructuredGenerationRequest:
    kind: str
    fallback_value: dict[str, Any]
    details: dict[str, Any]
    temperature: float = 0.2
    route: str | None = None
    max_tokens: int | None = None


class LLMFrontend(Protocol):
    def preload(self) -> bool:
        ...

    def generate_leaf_text(self, request: LeafGenerationRequest) -> str:
        ...

    def generate_structured_json(self, request: StructuredGenerationRequest) -> dict[str, Any]:
        ...
