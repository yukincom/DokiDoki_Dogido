# state_machine/haiku_context.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dogido_server.haiku.source_atoms import (
    CatalogSourceSnapshot,
    HaikuSourceAtom,
    PrefaceClause,
    preface_clauses_from_payload,
)
from dogido_server.state_machine.precipitation import PrecipitationContext


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
class SceneContext:
    found: bool = False
    clauses: tuple[PrefaceClause, ...] = ()
    motifs: tuple[str, ...] = ()
    focus: tuple[str, ...] = ()
    confidence: float = 0.0

    @classmethod
    def from_mapping(
        cls,
        payload: dict[str, Any] | None,
        *,
        source_atoms: tuple[HaikuSourceAtom, ...],
    ) -> SceneContext:
        if not isinstance(payload, dict):
            return cls()
        raw_clauses = payload.get("clauses")
        # 自動川柳の scene は consumer 側のドメイン契約で読む。小さいローカル
        # モデルが一節だけを最上位へ返しても、内容が同じなら一節として扱う。
        if raw_clauses is None and isinstance(payload.get("text"), str):
            raw_clauses = [{
                "text": payload.get("text"),
                "basis_atom_ids": payload.get("basis_atom_ids"),
                "claim_class": payload.get("claim_class"),
            }]
            found = True
        else:
            found = bool(payload.get("found"))
        clauses = preface_clauses_from_payload(
            raw_clauses,
            source_atoms=source_atoms,
        )
        motifs = tuple(str(value) for value in payload.get("motifs") or [] if value)
        focus = tuple(str(value) for value in payload.get("focus") or [] if value)
        try:
            confidence = float(payload.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if not found or clauses is None:
            return cls()
        return cls(
            found=True,
            clauses=clauses,
            motifs=motifs,
            focus=focus,
            confidence=max(0.0, min(1.0, confidence)),
        )

    @property
    def spoken_text(self) -> str:
        """構造契約を通った節から、実際に口にする文を組み立てる。"""

        return "。".join(clause.text for clause in self.clauses)


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
    precipitation_context: PrecipitationContext
    poem_item_id: str
    held_item: str  # 句の主役に使う持ち物ラベル（手持ち or 所持から選んだ1つ）
    inventory_items: tuple[str, ...]
    inventory_close_pair: tuple[str, ...]
    inventory_far_item: str
    nearby_blocks: tuple[str, ...]
    dropped_items: tuple[str, ...]
    passive_mobs: tuple[str, ...]
    haiku_tags: tuple[str, ...]
    feature_candidates: tuple[HaikuFeature, ...]
    candidate_tensions: tuple[str, ...]
    catalog_notes: tuple[str, ...] = ()
    catalog_sources: tuple[CatalogSourceSnapshot, ...] = ()
    source_atoms: tuple[HaikuSourceAtom, ...] = ()
    poetic_lines: tuple[str, ...] = ()
    # structure があるときは場所の主役。climate_hint は参考程度。
    structure_id: str = ""
    structure_label: str = ""
    climate_hint: str = ""
    # hand=実際に手にあるもの / pocket=道具手持ち時に所持から重み付き選択
    poem_item_source: str = "hand"
    # 観測値は保持したまま、現在のプレイヤーから見えない環境情報を
    # 川柳プロンプトと source atom へ出さないための投影条件。
    include_biome_context: bool = True
    include_sky_context: bool = True

    def feature_candidate_labels(self) -> list[str]:
        return [feature.prompt_label() for feature in self.feature_candidates]

    def has_structure(self) -> bool:
        return bool(self.structure_label or self.structure_id)

    def _base_details(self) -> dict[str, object]:
        details: dict[str, object] = {
            "player_name": self.player_name,
            "structure_id": self.structure_id,
            "structure_label": self.structure_label,
            "has_structure": self.has_structure(),
            "climate_hint": self.climate_hint if self.include_biome_context else "",
            "biome_context_visible": self.include_biome_context,
            "sky_context_visible": self.include_sky_context,
            "poem_item_id": self.poem_item_id,
            "held_item": self.held_item,
            "poem_item_source": self.poem_item_source,
            "inventory_items": list(self.inventory_items),
            "inventory_close_pair": list(self.inventory_close_pair),
            "inventory_far_item": self.inventory_far_item,
            "nearby_blocks": list(self.nearby_blocks),
            "dropped_items": list(self.dropped_items),
            "passive_mobs": list(self.passive_mobs),
            "haiku_tags": list(self.haiku_tags),
            "poetic_lines": list(self.poetic_lines),
            "feature_candidates": self.feature_candidate_labels(),
            "candidate_tensions": list(self.candidate_tensions),
            "catalog_notes": list(self.catalog_notes),
            "catalog_sources": [source.to_dict() for source in self.catalog_sources],
            "source_atoms": [atom.to_prompt_dict() for atom in self.source_atoms],
        }
        if self.include_biome_context:
            details.update({
                "biome": self.biome_label,
                "biome_id": self.biome_id,
                "biome_group": self.biome_group,
                "biome_traits": list(self.biome_traits),
            })
        if self.include_sky_context:
            details.update({
                "time_phase": self.time_phase,
                "time_label": self.time_label,
                "weather": self.weather,
                "weather_label": self.weather_label,
                **self.precipitation_context.to_prompt_details(),
            })
        return details

    def irony_details(self) -> dict[str, object]:
        return self._base_details()

    def scene_details(self, irony: IronyContext | None = None) -> dict[str, object]:
        details = self._base_details()
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

    def prompt_details(
        self,
        irony: IronyContext | None = None,
        scene: SceneContext | None = None,
    ) -> dict[str, object]:
        details = self.scene_details(irony)
        if scene is None or not scene.found:
            details["scene"] = None
            return details
        details["scene"] = {
            "spoken_text": scene.spoken_text,
            "clauses": [clause.to_dict() for clause in scene.clauses],
            "motifs": list(scene.motifs),
            "focus": list(scene.focus),
            "confidence": scene.confidence,
        }
        return details
