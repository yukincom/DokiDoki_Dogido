# entry_catalog.py
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

ENTRIES_DIR = Path(__file__).resolve().parents[1] / "data" / "catalogs" / "entries"
LOGGER = logging.getLogger("uvicorn.error")


@lru_cache(maxsize=None)
def load_entry_catalog(name: str) -> dict[str, Any]:
    path = _entry_catalog_file(name)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache(maxsize=None)
def load_entry_catalog_documents(name: str) -> tuple[dict[str, Any], ...]:
    directory = ENTRIES_DIR / name
    if directory.is_dir():
        documents: list[dict[str, Any]] = []
        for path in sorted(directory.glob("*.json")):
            try:
                with path.open("r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
            except json.JSONDecodeError as exc:
                LOGGER.debug("entry_catalog_skip_invalid_json path=%s detail=%s", path, exc)
                continue
            if isinstance(loaded, dict):
                documents.append(loaded)
        return tuple(documents)
    return (load_entry_catalog(name),)


@lru_cache(maxsize=None)
def load_named_entry_catalog_documents(name: str) -> dict[str, dict[str, Any]]:
    directory = ENTRIES_DIR / name
    if directory.is_dir():
        documents: dict[str, dict[str, Any]] = {}
        for path in sorted(directory.glob("*.json")):
            try:
                with path.open("r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
            except json.JSONDecodeError as exc:
                LOGGER.debug("entry_catalog_skip_invalid_json path=%s detail=%s", path, exc)
                continue
            if isinstance(loaded, dict):
                documents[path.stem] = loaded
        return documents
    loaded = load_entry_catalog(name)
    return {name: loaded} if isinstance(loaded, dict) else {}


def _entry_catalog_file(name: str) -> Path:
    direct = ENTRIES_DIR / f"{name}.json"
    if direct.exists():
        return direct
    minecraft_named = ENTRIES_DIR / f"minecraft_{name}.json"
    if minecraft_named.exists():
        return minecraft_named
    return direct


def item_labels() -> dict[str, str]:
    return dict(load_entry_catalog("item"))


def block_labels() -> dict[str, str]:
    labels: dict[str, str] = {}
    for catalog in load_entry_catalog_documents("block"):
        direct_labels = catalog.get("direct_labels", {})
        if isinstance(direct_labels, dict):
            for item_id, label in direct_labels.items():
                _merge_block_label(labels, str(item_id), str(label))
        for payload in catalog.values():
            if isinstance(payload, dict):
                _collect_grouped_block_labels(payload, labels)
    return labels


def biome_entries() -> dict[str, dict[str, Any]]:
    catalog = load_entry_catalog("biome")
    groups = catalog.get("groups")
    if isinstance(groups, dict):
        flattened: dict[str, dict[str, Any]] = {}
        for group_id, group_payload in groups.items():
            if not isinstance(group_payload, dict):
                continue
            group_label = group_payload.get("label")
            group_description = group_payload.get("description")
            group_biomes = group_payload.get("biomes", {})
            group_meta = {
                key: value
                for key, value in group_payload.items()
                if key not in {"label", "description", "biomes"}
            }
            if not isinstance(group_biomes, dict):
                continue
            for biome_id, biome_payload in group_biomes.items():
                if not isinstance(biome_payload, dict):
                    continue
                entry = dict(biome_payload)
                entry["label"] = entry.pop("japanese", biome_id)
                entry["group_id"] = str(group_id)
                entry["group_label"] = group_label
                entry["group_description"] = group_description
                for key, value in group_meta.items():
                    entry[f"group_{key}"] = value
                flattened[str(biome_id)] = entry
        return flattened

    return {
        biome_id: {"label": str(label)}
        for biome_id, label in catalog.items()
    }


def biome_labels() -> dict[str, str]:
    return {
        biome_id: str(entry.get("label", biome_id))
        for biome_id, entry in biome_entries().items()
    }


def hostile_mob_entries() -> dict[str, dict[str, Any]]:
    sections = _mob_catalog_sections()
    return _normalize_mob_entries(_mob_section_items(sections.get("hostile", {})))


def neutral_mob_entries() -> dict[str, dict[str, Any]]:
    sections = _mob_catalog_sections()
    return _normalize_mob_entries(_mob_section_items(sections.get("neutral", {})))


def threat_mob_entries() -> dict[str, dict[str, Any]]:
    return _merge_mob_entry_maps(hostile_mob_entries(), neutral_mob_entries())


def passive_mob_entries() -> dict[str, dict[str, Any]]:
    sections = _mob_catalog_sections()
    passive = _normalize_mob_entries(_mob_section_items(sections.get("passive", {})))
    return _merge_mob_entry_maps(neutral_mob_entries(), passive)


def hostile_mob_labels() -> dict[str, str]:
    return {mob_id: str(entry.get("label", mob_id)) for mob_id, entry in hostile_mob_entries().items()}


def neutral_mob_labels() -> dict[str, str]:
    return {mob_id: str(entry.get("label", mob_id)) for mob_id, entry in neutral_mob_entries().items()}


def threat_mob_labels() -> dict[str, str]:
    return {mob_id: str(entry.get("label", mob_id)) for mob_id, entry in threat_mob_entries().items()}


def passive_mob_labels() -> dict[str, str]:
    return {mob_id: str(entry.get("label", mob_id)) for mob_id, entry in passive_mob_entries().items()}


def all_mob_entries() -> dict[str, dict[str, Any]]:
    return _merge_mob_entry_maps(threat_mob_entries(), passive_mob_entries())


def mob_entry(mob_id: str | None) -> dict[str, Any] | None:
    if not mob_id:
        return None
    normalized = str(mob_id).strip().lower()
    if not normalized:
        return None
    entry = all_mob_entries().get(normalized)
    if entry is None:
        return None
    return dict(entry)


def mob_poetic_tags(mob_id: str | None) -> tuple[str, ...]:
    entry = mob_entry(mob_id)
    if entry is None:
        return ()
    poetic = entry.get("poetic")
    if not isinstance(poetic, dict):
        return ()
    tags: list[str] = []
    for key in (
        "visual_tags",
        "sound_tags",
        "motion_tags",
        "scene_tags",
        "reaction_tags",
        "comic_tags",
    ):
        values = poetic.get(key)
        if isinstance(values, list):
            tags.extend(str(value) for value in values if value)
    role = poetic.get("role")
    if role:
        tags.append(str(role))
    seen: set[str] = set()
    deduped: list[str] = []
    for tag in tags:
        if not tag or tag in seen:
            continue
        seen.add(tag)
        deduped.append(tag)
    return tuple(deduped)


def _normalize_mob_entries(raw_entries: Any) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    if not isinstance(raw_entries, dict):
        return normalized
    for mob_id, payload in raw_entries.items():
        if isinstance(payload, dict):
            normalized[str(mob_id)] = dict(payload)
            continue
        normalized[str(mob_id)] = {"label": str(payload)}
    return normalized


def _mob_catalog_sections() -> dict[str, dict[str, Any]]:
    documents = load_named_entry_catalog_documents("mobs")
    if {"hostile", "neutral", "passive"} & documents.keys():
        return documents
    legacy = documents.get("mobs", {})
    if not isinstance(legacy, dict):
        return {}
    return {
        key: value
        for key, value in legacy.items()
        if key in {"schema", "hostile", "neutral", "passive"}
        and isinstance(value, dict)
    }


def _mob_section_items(raw_section: Any) -> dict[str, Any]:
    if not isinstance(raw_section, dict):
        return {}
    wrapped_items = raw_section.get("items")
    if isinstance(wrapped_items, dict):
        return wrapped_items
    return {
        key: value
        for key, value in raw_section.items()
        if key not in {"label", "items", "notes", "schema", "recommended_fields"}
    }


def _merge_mob_entry_maps(*maps: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for entry_map in maps:
        for mob_id, payload in entry_map.items():
            merged[mob_id] = dict(payload)
    return merged


def _collect_grouped_block_labels(node: dict[str, Any], labels: dict[str, str]) -> None:
    items = node.get("items", {})
    if isinstance(items, dict):
        for item_id, label in items.items():
            _merge_block_label(labels, str(item_id), str(label))
    groups = node.get("groups", {})
    if isinstance(groups, dict):
        for payload in groups.values():
            if isinstance(payload, dict):
                _collect_grouped_block_labels(payload, labels)


def _merge_block_label(labels: dict[str, str], item_id: str, candidate: str) -> None:
    existing = labels.get(item_id)
    if existing is None or _should_replace_block_label(existing, candidate):
        labels[item_id] = candidate


def _should_replace_block_label(existing: str, candidate: str) -> bool:
    if existing == candidate:
        return False
    return len(candidate) > len(existing)
