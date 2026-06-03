# state_machine/ambient_mob_catalog.py
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).resolve().parents[2] / "data" / "mobs" / "ambient_reactions.json"


@dataclass(frozen=True, slots=True)
class AmbientMobReactionContext:
    mob_type: str
    mob_label: str
    inventory_item_ids: frozenset[str]


@lru_cache(maxsize=1)
def load_ambient_mob_catalog() -> dict[str, Any]:
    with CATALOG_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def ambient_mob_fallback_candidates(context: AmbientMobReactionContext) -> list[str]:
    catalog = load_ambient_mob_catalog()
    mob_entry = catalog.get("mobs", {}).get(context.mob_type, {})

    candidates: list[str] = []
    for conditional in mob_entry.get("conditional_lines", []):
        required_ids = frozenset(str(item_id) for item_id in conditional.get("requires_inventory_any", []))
        if required_ids and not required_ids.intersection(context.inventory_item_ids):
            continue
        line = str(conditional.get("line", "")).strip()
        if line:
            candidates.append(line)

    for line in mob_entry.get("specific_lines", []):
        text = str(line).strip()
        if text:
            candidates.append(text)

    for template in catalog.get("generic_templates", []):
        text = str(template).replace("{mob}", context.mob_label).strip()
        if text:
            candidates.append(text)

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        deduped.append(candidate)
    return deduped
