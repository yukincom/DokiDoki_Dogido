# state_machine/haiku_context.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class HaikuFeature:
    source: str
    key: str
    label: str
    tags: tuple[str, ...] = ()

    def prompt_label(self) -> str:
        return f"{self.source} {self.label}".strip()


@dataclass(frozen=True, slots=True)
class IronyContext:
    found: bool = False
    kind: str = "none"
    description: str = ""
    elements: tuple[str, ...] = ()
    focus: tuple[str, ...] = ()
    confidence: float = 0.0

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> IronyContext:
        if not isinstance(payload, dict):
            return cls()
        found = bool(payload.get("found"))
        kind = str(payload.get("kind") or "none")
        description = str(payload.get("description") or "")
        elements = tuple(str(value) for value in payload.get("elements") or [] if value)
        focus = tuple(str(value) for value in payload.get("focus") or [] if value)
        try:
            confidence = float(payload.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if not found:
            return cls()
        return cls(
            found=True,
            kind=kind,
            description=description,
            elements=elements,
            focus=focus,
            confidence=max(0.0, min(1.0, confidence)),
        )


@dataclass(frozen=True, slots=True)
class HaikuContext:
    player_name: str
    biome_id: str
    biome_label: str
    biome_group: str
    biome_traits: tuple[str, ...]
    time_phase: str
    time_label: str
    weather: str
    weather_label: str
    z_value: int
    held_item: str
    inventory_items: tuple[str, ...]
    nearby_blocks: tuple[str, ...]
    peaceful_mobs: tuple[str, ...]
    haiku_tags: tuple[str, ...]
    feature_candidates: tuple[HaikuFeature, ...]
    candidate_tensions: tuple[str, ...]

    def feature_candidate_labels(self) -> list[str]:
        return [feature.prompt_label() for feature in self.feature_candidates]

    def irony_details(self) -> dict[str, object]:
        return {
            "player_name": self.player_name,
            "biome": self.biome_label,
            "biome_id": self.biome_id,
            "biome_group": self.biome_group,
            "time_phase": self.time_phase,
            "time_label": self.time_label,
            "weather": self.weather,
            "weather_label": self.weather_label,
            "held_item": self.held_item,
            "inventory_items": list(self.inventory_items),
            "nearby_blocks": list(self.nearby_blocks),
            "peaceful_mobs": list(self.peaceful_mobs),
            "haiku_tags": list(self.haiku_tags),
            "feature_candidates": self.feature_candidate_labels(),
            "candidate_tensions": list(self.candidate_tensions),
        }

    def prompt_details(self, irony: IronyContext | None = None) -> dict[str, object]:
        details = {
            "player_name": self.player_name,
            "biome": self.biome_label,
            "biome_id": self.biome_id,
            "biome_group": self.biome_group,
            "biome_traits": list(self.biome_traits),
            "time_phase": self.time_phase,
            "time_label": self.time_label,
            "weather": self.weather,
            "weather_label": self.weather_label,
            "z_value": self.z_value,
            "held_item": self.held_item,
            "inventory_items": list(self.inventory_items),
            "nearby_blocks": list(self.nearby_blocks),
            "peaceful_mobs": list(self.peaceful_mobs),
            "haiku_tags": list(self.haiku_tags),
            "feature_candidates": self.feature_candidate_labels(),
            "candidate_tensions": list(self.candidate_tensions),
        }
        if irony is None or not irony.found:
            details["irony"] = None
            return details
        details["irony"] = {
            "kind": irony.kind,
            "description": irony.description,
            "elements": list(irony.elements),
            "focus": list(irony.focus),
            "confidence": irony.confidence,
        }
        return details
