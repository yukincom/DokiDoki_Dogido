# entry_catalog.py
from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from typing import Any

ENTRIES_DIR = Path(__file__).resolve().parents[1] / "data" / "catalogs" / "entries"
LOGGER = logging.getLogger("uvicorn.error")
ITEM_CATALOG_FILES = (
    "minecraft_tools_and_utilities",
    "minecraft_combat_items",
    "minecraft_food_and_drinks",
    "minecraft_materials",
    "minecraft_spawn_egg",
    "minecraft_command_only_items",
)

# グループ構造を示すキー。これを持つ dict はエントリではなくグループとして辿る。
GROUP_STRUCTURE_KEYS = {"items", "groups", "refs"}

# グループ直下にあってもエントリ id として扱わないキー。
NON_ENTRY_KEYS = {
    "label", "english", "japanese", "note", "items", "groups",
    "source", "refs", "meta", "variants", "role",
    "recommended_fields", "schema", "notes", "direct_labels", "description",
    "priority", "poetic", "parent",
}


def _is_source_pointer(value: Any) -> bool:
    # source は「参照ポインタ（.json で終わる）」と「ただのメタ情報」の両方に
    # 使われ得るので、ファイル参照の形をしているものだけ解決対象にする。
    return isinstance(value, str) and value.strip().lower().endswith(".json")


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
    return {
        item_id: str(entry.get("label", item_id))
        for item_id, entry in item_entries().items()
    }


def item_entries() -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for catalog_name in ITEM_CATALOG_FILES:
        catalog = load_entry_catalog(catalog_name)
        for section_id, section_payload in catalog.items():
            if not isinstance(section_payload, dict):
                continue
            _collect_grouped_entry_payloads(
                section_payload,
                entries,
                root_section=section_id,
            )
    return entries


def item_entry(item_id: str | None) -> dict[str, Any] | None:
    if not item_id:
        return None
    normalized = str(item_id).split(":")[-1].strip().lower()
    if not normalized:
        return None
    entry = item_entries().get(normalized)
    if entry is None:
        return None
    return dict(entry)


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


def block_entries() -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for catalog_name, catalog in load_named_entry_catalog_documents("block").items():
        direct_labels = catalog.get("direct_labels", {})
        if isinstance(direct_labels, dict):
            for item_id, label in direct_labels.items():
                entries[str(item_id)] = _entry_payload_from_value(
                    label,
                    root_section=catalog_name,
                    group_path=("direct_labels",),
                ) or {"label": str(label), "japanese": str(label), "section": catalog_name, "group_path": ["direct_labels"]}
        for section_id, section_payload in catalog.items():
            if section_id in {"direct_labels", "meta"}:
                continue
            if not isinstance(section_payload, dict):
                continue
            _collect_grouped_entry_payloads(
                section_payload,
                entries,
                root_section=section_id,
            )
    return entries


def block_entry(block_id: str | None) -> dict[str, Any] | None:
    if not block_id:
        return None
    normalized = str(block_id).split(":")[-1].strip().lower()
    if not normalized:
        return None
    entry = block_entries().get(normalized)
    if entry is None:
        return None
    return dict(entry)


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


def biome_reading(biome_id: str | None) -> str | None:
    """バイオーム表示名の正しい読み（カタログ reading。オーバーレイは catalog_readings 側）。"""
    from dogido_server.catalog_readings import resolve_reading

    if not biome_id:
        return None
    normalized = str(biome_id).strip().lower().removeprefix("minecraft:")
    entry = biome_entries().get(normalized)
    if entry is None:
        return None
    label = str(entry.get("label") or entry.get("japanese") or "").strip()
    catalog_reading = str(entry.get("reading") or "").strip() or None
    return resolve_reading(label, catalog_reading)


def biome_label_with_reading(biome_id: str | None) -> str | None:
    from dogido_server.catalog_readings import format_label_with_reading

    if not biome_id:
        return None
    normalized = str(biome_id).strip().lower().removeprefix("minecraft:")
    entry = biome_entries().get(normalized)
    if entry is None:
        return None
    label = str(entry.get("label") or entry.get("japanese") or normalized).strip()
    catalog_reading = str(entry.get("reading") or "").strip() or None
    return format_label_with_reading(label, catalog_reading)


def biome_labels() -> dict[str, str]:
    return {
        biome_id: str(entry.get("label", biome_id))
        for biome_id, entry in biome_entries().items()
    }


def biome_group_id(biome_id: str | None) -> str | None:
    if not biome_id:
        return None
    normalized = str(biome_id).strip().lower().removeprefix("minecraft:")
    entry = biome_entries().get(normalized)
    if entry is None:
        return None
    group = entry.get("group_id")
    return str(group) if group else None


def biomes_in_groups(group_ids: set[str] | frozenset[str] | list[str] | tuple[str, ...]) -> frozenset[str]:
    wanted = {str(gid).strip().lower() for gid in group_ids if str(gid).strip()}
    if not wanted:
        return frozenset()
    return frozenset(
        biome_id
        for biome_id, entry in biome_entries().items()
        if str(entry.get("group_id") or "").lower() in wanted
    )


def biome_label_to_id_map() -> dict[str, str]:
    """表示名・読み → biome_id。長いキー優先でマッチするため呼び出し側で sort する。"""
    from dogido_server.catalog_readings import resolve_reading

    mapping: dict[str, str] = {}
    for biome_id, entry in biome_entries().items():
        label = str(entry.get("label") or entry.get("japanese") or "").strip()
        if label:
            mapping[label] = biome_id
        reading = resolve_reading(label, str(entry.get("reading") or "").strip() or None)
        if reading:
            mapping[reading] = biome_id
        # id 自体もヒントに（snowy_taiga など）
        mapping[biome_id] = biome_id
        mapping[biome_id.replace("_", "")] = biome_id
    return mapping


# ぼんやり場所イメージ → カタログ group_id（複数可）
# catalog groups: snowy, cold, temperate, dry, aquatic, cave, neutral_other, nether, end
BIOME_PLACE_GROUP_HINTS: tuple[tuple[tuple[str, ...], tuple[str, ...], str], ...] = (
    # (trigger phrases, group_ids, speech label)
    (("氷雪", "雪のとこ", "雪のところ", "雪国"), ("snowy",), "氷雪バイオーム"),
    (("冷帯", "寒いとこ", "寒いところ", "寒い場所", "寒い", "冷たいとこ", "冷たい"), ("cold", "snowy"), "寒いところ"),
    (("温帯", "暖地", "暖かいとこ", "あたたかいとこ", "温暖"), ("temperate",), "温帯"),
    (("乾燥", "乾燥帯", "乾いたとこ", "砂漠っぽ"), ("dry",), "乾燥帯"),
    (("水性", "海の", "海辺", "水辺", "海で", "海に"), ("aquatic",), "水辺"),
    (("洞窟", "地下のバイオーム", "洞窟バイオーム"), ("cave",), "洞窟"),
    (("ネザー", "ねざー", "地獄"), ("nether",), "ネザー"),
    (("エンド", "えんど", "ジ・エンド", "ジエンド"), ("end",), "エンド"),
)


def _fold_kana_for_match(text: str) -> str:
    return "".join(
        chr(ord(ch) - 0x60) if "ァ" <= ch <= "ヶ" else ch
        for ch in text
    )


def resolve_biome_place_from_text(text: str) -> dict[str, object]:
    """発話から具体バイオーム or グループを解決する。

    戻り値:
      biome_id: 具体 id or None
      group_ids: frozenset[str]
      biome_ids: 展開後の id 集合（グループ指定時）
      place_label: 返事用ラベル
    """
    raw = (text or "").strip()
    folded = _fold_kana_for_match(raw)
    if not folded and not raw:
        return {
            "biome_id": None,
            "group_ids": frozenset(),
            "biome_ids": frozenset(),
            "place_label": None,
        }

    # 1) 具体名（カタログ全ラベル・読み）。長い順
    label_map = biome_label_to_id_map()
    for hint, biome_id in sorted(label_map.items(), key=lambda item: -len(item[0])):
        if len(hint) < 2:
            continue
        if hint in raw or _fold_kana_for_match(hint) in folded:
            label = biome_labels().get(biome_id, biome_id)
            return {
                "biome_id": biome_id,
                "group_ids": frozenset(),
                "biome_ids": frozenset({biome_id}),
                "place_label": label,
            }

    # 2) ぼんやりグループ
    for phrases, group_ids, place_label in BIOME_PLACE_GROUP_HINTS:
        for phrase in phrases:
            if phrase in raw or _fold_kana_for_match(phrase) in folded:
                expanded = biomes_in_groups(group_ids)
                return {
                    "biome_id": None,
                    "group_ids": frozenset(group_ids),
                    "biome_ids": expanded,
                    "place_label": place_label,
                }

    return {
        "biome_id": None,
        "group_ids": frozenset(),
        "biome_ids": frozenset(),
        "place_label": None,
    }


def structure_entries() -> dict[str, dict[str, Any]]:
    catalog = load_entry_catalog("structure")
    groups = catalog.get("groups")
    flattened: dict[str, dict[str, Any]] = {}
    if not isinstance(groups, dict):
        return flattened
    for group_id, group_payload in groups.items():
        if not isinstance(group_payload, dict):
            continue
        group_label = group_payload.get("label")
        group_description = group_payload.get("description")
        group_structures = group_payload.get("structures", {})
        group_meta = {
            key: value
            for key, value in group_payload.items()
            if key not in {"label", "description", "structures"}
        }
        if not isinstance(group_structures, dict):
            continue
        for structure_id, structure_payload in group_structures.items():
            if not isinstance(structure_payload, dict):
                continue
            entry = dict(structure_payload)
            entry["label"] = entry.pop("japanese", structure_id)
            entry["group_id"] = str(group_id)
            entry["group_label"] = group_label
            entry["group_description"] = group_description
            for key, value in group_meta.items():
                entry[f"group_{key}"] = value
            flattened[str(structure_id)] = entry
    return flattened


def structure_labels() -> dict[str, str]:
    return {
        structure_id: str(entry.get("label", structure_id))
        for structure_id, entry in structure_entries().items()
    }


def normalize_biome_id(biome_id: str | None) -> str | None:
    text = str(biome_id or "").removeprefix("minecraft:").strip().lower().replace("-", "_")
    return text or None


def structure_biomes(structure_id: str | None) -> frozenset[str]:
    if not structure_id:
        return frozenset()
    entry = structure_entries().get(str(structure_id))
    if not entry:
        return frozenset()
    biomes = entry.get("biomes") or []
    if not isinstance(biomes, list):
        return frozenset()
    normalized = {
        bid
        for raw in biomes
        if (bid := normalize_biome_id(str(raw))) is not None
    }
    return frozenset(normalized)


def structure_related_mobs(structure_id: str | None) -> tuple[str, ...]:
    if not structure_id:
        return ()
    entry = structure_entries().get(str(structure_id))
    if not entry:
        return ()
    raw = entry.get("related_mobs") or []
    if not isinstance(raw, list):
        return ()
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        mob_id = str(item or "").removeprefix("minecraft:").strip().lower()
        if not mob_id or mob_id in seen:
            continue
        seen.add(mob_id)
        out.append(mob_id)
    return tuple(out)


@lru_cache(maxsize=1)
def structures_for_mob_index() -> dict[str, tuple[str, ...]]:
    """mob_id → structure_id 群（related_mobs の逆引き）。"""
    index: dict[str, list[str]] = {}
    for structure_id in structure_entries():
        for mob_id in structure_related_mobs(structure_id):
            index.setdefault(mob_id, []).append(structure_id)
    return {mob_id: tuple(ids) for mob_id, ids in index.items()}


def structures_for_mob(mob_id: str | None) -> tuple[str, ...]:
    if not mob_id:
        return ()
    key = str(mob_id).removeprefix("minecraft:").strip().lower()
    return structures_for_mob_index().get(key, ())


def structure_ids_for_plausibility(
    topic_hits: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
) -> list[str]:
    """トピックから plausibility 対象の structure id を集める。

    - kind=structure はそのまま
    - kind=mob は related_mobs 逆引きし、マッチ語が structure 表示名に含まれるときだけ
      （「前哨基地」→ ピリジャー scene → 前哨基地、のように。ピリ視認だけでは出さない）
    """
    ids: list[str] = []
    seen: set[str] = set()

    def add(structure_id: str) -> None:
        if structure_id and structure_id not in seen and structure_id in structure_entries():
            seen.add(structure_id)
            ids.append(structure_id)

    for hit in topic_hits or ():
        kind = str(hit.get("kind") or "")
        entry_id = str(hit.get("entry_id") or "").strip()
        if not entry_id:
            continue
        if kind == "structure":
            add(entry_id)
            continue
        if kind != "mob":
            continue
        matched = [str(t).strip() for t in (hit.get("matched_terms") or ()) if str(t).strip()]
        if not matched:
            continue
        mob_label = str(hit.get("label_ja") or "").strip()
        for structure_id in structures_for_mob(entry_id):
            label = str((structure_entries().get(structure_id) or {}).get("label") or "")
            # 種名ラベルそのもの（「ピリジャー」）だけでは前哨を出さない。
            # 「前哨基地」など structure 名に含まれる別語のときだけ。
            if any(
                len(term) >= 2 and term in label and term != mob_label
                for term in matched
            ):
                add(structure_id)
    return ids


def build_plausibility_hint_lines(
    *,
    structure_ids: list[str] | tuple[str, ...] | set[str] | None = None,
    topic_hits: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    current_biome_id: str | None = None,
    current_biome_label: str | None = None,
) -> list[str]:
    """structure の生成可否を現在 biome と突合した短い行（LLM 推論用ではない事実行）。"""
    resolved_ids = list(structure_ids or ())
    if topic_hits is not None:
        for structure_id in structure_ids_for_plausibility(topic_hits):
            if structure_id not in resolved_ids:
                resolved_ids.append(structure_id)

    biome = normalize_biome_id(current_biome_id)
    place = (current_biome_label or "").strip() or (biome or "いまの場所")
    lines: list[str] = []
    seen: set[str] = set()

    for raw_id in resolved_ids:
        structure_id = str(raw_id or "").strip()
        if not structure_id or structure_id in seen:
            continue
        entry = structure_entries().get(structure_id)
        if entry is None:
            continue
        seen.add(structure_id)
        label = str(entry.get("label") or structure_id)
        biomes = structure_biomes(structure_id)
        if not biomes:
            continue
        if biome is None:
            lines.append(f"{label}: 生成バイオームの知識はあるが、いまの場所は不明")
            continue
        if biome in biomes:
            lines.append(f"{label}: いまの場所（{place}）は生成されうるバイオームに含む → ありうる")
        else:
            lines.append(f"{label}: いまの場所（{place}）には生成されにくいかも")
        # 前哨は襲撃と混同しやすいので注記
        if structure_id == "pillager_outpost":
            lines.append("（ピリジャーがいること自体は襲撃でも起きる。前哨の有無は別）")
    return lines


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
    normalized = str(mob_id).strip().lower().removeprefix("minecraft:")
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


def mob_poetic_line(mob_id: str | None, *, max_tags: int = 4) -> str | None:
    """主役用の1行詩語。role を軸に代表タグだけ添える（フラットタグ列より誰の語か分かる）。"""
    entry = mob_entry(mob_id)
    if entry is None:
        return None
    poetic = entry.get("poetic")
    if not isinstance(poetic, dict):
        return None
    label = str(entry.get("label") or mob_id or "").strip()
    if not label:
        return None
    role = str(poetic.get("role") or "").strip()
    tags: list[str] = []
    seen: set[str] = set()
    for key in (
        "visual_tags",
        "sound_tags",
        "motion_tags",
        "comic_tags",
        "scene_tags",
        "reaction_tags",
    ):
        values = poetic.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            text = str(value or "").strip()
            if not text or text in seen or text == role:
                continue
            seen.add(text)
            tags.append(text)
            if len(tags) >= max_tags:
                break
        if len(tags) >= max_tags:
            break
    if role and tags:
        return f"{label}: {role}（{'、'.join(tags)}）"
    if role:
        return f"{label}: {role}"
    if tags:
        return f"{label}: {'、'.join(tags)}"
    return None


def mob_dogido_tactics(mob_id: str | None) -> dict[str, Any] | None:
    """Mob ごとのドギド戦術メモ（禁止助言・安全ヒント）。"""
    entry = mob_entry(mob_id)
    if entry is None:
        return None
    tactics = entry.get("dogido_tactics")
    if not isinstance(tactics, dict):
        return None
    return dict(tactics)


def collect_dogido_tactics_for_mobs(mob_ids: list[str] | tuple[str, ...] | set[str]) -> dict[str, Any]:
    """複数 Mob の tactics を合成して player_chat 等に渡す。

    戻り値:
      labels: 日本語名リスト
      notes: 性質メモ
      forbidden_advice: 禁止フレーズ断片（重複除去）
      safe_hints: 言ってよいヒント
    """
    labels: list[str] = []
    notes: list[str] = []
    forbidden: list[str] = []
    safe_hints: list[str] = []
    seen_ids: set[str] = set()
    for raw_id in mob_ids:
        mob_id = str(raw_id or "").removeprefix("minecraft:").strip().lower()
        if not mob_id or mob_id in seen_ids:
            continue
        seen_ids.add(mob_id)
        entry = mob_entry(mob_id)
        if entry is None:
            continue
        label = str(entry.get("label") or mob_id)
        labels.append(label)
        tactics = entry.get("dogido_tactics")
        if not isinstance(tactics, dict):
            continue
        note = tactics.get("notes")
        if note:
            notes.append(f"{label}: {note}")
        for item in tactics.get("forbidden_advice") or []:
            text = str(item).strip()
            if text and text not in forbidden:
                forbidden.append(text)
        for item in tactics.get("safe_hints") or []:
            text = str(item).strip()
            if text and text not in safe_hints:
                safe_hints.append(text)
    return {
        "labels": labels,
        "notes": notes,
        "forbidden_advice": forbidden,
        "safe_hints": safe_hints,
    }


# --- player_chat 汎用トピック照合（プレイヤー文 → カタログ） ---

# field_kind → 重み。種族専用キーワード表は置かない。
_TOPIC_FIELD_WEIGHTS: dict[str, float] = {
    "label": 10.0,
    "spoken_aliases": 10.0,
    "visual_tags": 6.0,
    "role": 5.0,
    "scene_tags": 4.0,
    "motion_tags": 4.0,
    "sound_tags": 4.0,
    "reaction_tags": 2.0,
    "comic_tags": 2.0,
    "structure_label": 8.0,
    "note": 3.0,
}

_TOPIC_MIN_TERM_LEN = 1  # 旗・紫・薬など1文字タグを許可（カタログが正本）
_TOPIC_MIN_SCORE = 5.0
_TOPIC_TOP_K = 3


def _topic_term_score(field_kind: str, term_len: int) -> float:
    weight = _TOPIC_FIELD_WEIGHTS.get(field_kind, 2.0)
    # 長い語ほど強い（「とんがり帽子」>「帽子」が両方あっても長い方が効く）
    return weight * float(max(term_len, 1))


@lru_cache(maxsize=1)
def _topic_term_index() -> tuple[tuple[str, str, str, str], ...]:
    """(term, entry_id, kind, field_kind) を長い term 優先で返す。"""
    rows: list[tuple[str, str, str, str]] = []

    def add(term: str, entry_id: str, kind: str, field_kind: str) -> None:
        text = str(term or "").strip()
        if len(text) < _TOPIC_MIN_TERM_LEN:
            return
        # 長文 note は部分一致ノイズが大きいので短めだけ
        if field_kind == "note" and len(text) > 24:
            return
        # 音タグは擬音が多く「こんにちは」⊃「こん」などの誤爆が出やすい
        if field_kind == "sound_tags" and len(text) < 3:
            return
        rows.append((text, entry_id, kind, field_kind))

    for mob_id, entry in all_mob_entries().items():
        label = str(entry.get("label") or entry.get("japanese") or "").strip()
        if label:
            add(label, mob_id, "mob", "label")
        aliases = entry.get("spoken_aliases")
        if isinstance(aliases, list):
            for alias in aliases:
                add(str(alias), mob_id, "mob", "spoken_aliases")
        poetic = entry.get("poetic")
        if isinstance(poetic, dict):
            role = poetic.get("role")
            if role:
                add(str(role), mob_id, "mob", "role")
            for key in (
                "visual_tags",
                "sound_tags",
                "motion_tags",
                "scene_tags",
                "reaction_tags",
                "comic_tags",
            ):
                values = poetic.get(key)
                if not isinstance(values, list):
                    continue
                for value in values:
                    add(str(value), mob_id, "mob", key)

    for structure_id, entry in structure_entries().items():
        label = str(entry.get("label") or entry.get("japanese") or "").strip()
        if label:
            add(label, structure_id, "structure", "structure_label")
        note = entry.get("note")
        if note:
            add(str(note), structure_id, "structure", "note")

    # 長い term を先に照合（部分の短い語より優先してスコア加算）
    rows.sort(key=lambda row: (-len(row[0]), row[0], row[1], row[2], row[3]))
    return tuple(rows)


def find_catalog_topics(
    text: str,
    *,
    observed_ids: list[str] | tuple[str, ...] | set[str] | None = None,
    top_k: int = _TOPIC_TOP_K,
    min_score: float = _TOPIC_MIN_SCORE,
) -> list[dict[str, Any]]:
    """プレイヤー文に含まれるカタログ語から話題候補を返す。

    戻り値は score 降順の dict リスト:
      entry_id, kind, label_ja, score, matched_terms, observed
    """
    raw = (text or "").strip()
    if not raw:
        return []
    folded = _fold_kana_for_match(raw)
    observed: set[str] = set()
    for raw_id in observed_ids or ():
        normalized = str(raw_id or "").removeprefix("minecraft:").strip().lower()
        if normalized:
            observed.add(normalized)

    scores: dict[tuple[str, str], float] = {}
    matched: dict[tuple[str, str], list[str]] = {}
    labels: dict[tuple[str, str], str] = {}

    for term, entry_id, kind, field_kind in _topic_term_index():
        term_folded = _fold_kana_for_match(term)
        if not term_folded:
            continue
        if term not in raw and term_folded not in folded:
            continue
        key = (kind, entry_id)
        scores[key] = scores.get(key, 0.0) + _topic_term_score(field_kind, len(term))
        bucket = matched.setdefault(key, [])
        if term not in bucket:
            bucket.append(term)
        if key not in labels:
            if kind == "mob":
                entry = mob_entry(entry_id) or {}
                labels[key] = str(entry.get("label") or entry_id)
            else:
                entry = structure_entries().get(entry_id) or {}
                labels[key] = str(entry.get("label") or entry_id)

    hits: list[dict[str, Any]] = []
    for key, score in scores.items():
        kind, entry_id = key
        is_observed = entry_id in observed
        if is_observed:
            score *= 3.0
        if score < min_score:
            continue
        hits.append(
            {
                "entry_id": entry_id,
                "kind": kind,
                "label_ja": labels.get(key, entry_id),
                "score": score,
                "matched_terms": tuple(matched.get(key, ())),
                "observed": is_observed,
            }
        )

    hits.sort(
        key=lambda hit: (
            -float(hit["score"]),
            not bool(hit["observed"]),
            str(hit["entry_id"]),
        )
    )
    return hits[: max(0, int(top_k))]


def format_catalog_topic_hints(hits: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> str:
    """player_chat details / プロンプト用の短いヒント文。"""
    lines: list[str] = []
    for hit in hits:
        terms = "・".join(str(term) for term in (hit.get("matched_terms") or ())[:4])
        obs = "あり" if hit.get("observed") else "なし"
        label = str(hit.get("label_ja") or hit.get("entry_id") or "")
        kind = str(hit.get("kind") or "mob")
        prefix = "構造物" if kind == "structure" else "モブ"
        if terms:
            lines.append(f"- [{prefix}] {label}: マッチ「{terms}」。観測: {obs}")
        else:
            lines.append(f"- [{prefix}] {label}: 観測: {obs}")
    return "\n".join(lines)


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


def _looks_like_group(value: Any) -> bool:
    return isinstance(value, dict) and bool(GROUP_STRUCTURE_KEYS & value.keys())


@lru_cache(maxsize=None)
def _load_catalog_document(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.warning("entry_catalog_source_load_failed path=%s detail=%s", path, exc)
        return None
    return loaded if isinstance(loaded, dict) else None


def _resolve_source_path(spec: Any) -> Path | None:
    name = str(spec or "").strip()
    if not name:
        return None
    base = Path(name)
    candidates = (
        ENTRIES_DIR / name,
        ENTRIES_DIR / base.parent / f"minecraft_{base.name}",
        ENTRIES_DIR / "block" / base.name,
        ENTRIES_DIR / "block" / f"minecraft_{base.name}",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return None


def _find_canonical_payload(
    path: Path,
    entry_id: str,
    _seen: frozenset[tuple[str, str]] = frozenset(),
) -> Any:
    key = (str(path), entry_id)
    if key in _seen:
        LOGGER.warning("entry_catalog_source_cycle id=%s path=%s", entry_id, path.name)
        return None
    document = _load_catalog_document(path)
    if document is None:
        return None
    pointer: dict[str, Any] | None = None
    for found_id, payload, _group_path in _iter_document_entries(document):
        if found_id != entry_id:
            continue
        if isinstance(payload, dict) and _is_source_pointer(payload.get("source")):
            if pointer is None:
                pointer = payload
            continue
        return payload
    if pointer is not None:
        next_path = _resolve_source_path(pointer.get("source"))
        if next_path is not None:
            return _find_canonical_payload(next_path, entry_id, _seen | {key})
    return None


def _resolve_entry_payload(payload: Any, entry_id: str) -> Any:
    if not isinstance(payload, dict) or not _is_source_pointer(payload.get("source")):
        return payload
    local = {
        key: value
        for key, value in payload.items()
        if key not in {"source", "refs"}
    }
    source_path = _resolve_source_path(payload.get("source"))
    canonical = _find_canonical_payload(source_path, entry_id) if source_path else None
    if canonical is None:
        LOGGER.warning(
            "entry_catalog_unresolved_entry_source id=%s spec=%r",
            entry_id,
            payload.get("source"),
        )
        return local or None
    if isinstance(canonical, str):
        merged = dict(local)
        merged.setdefault("japanese", canonical)
        return merged
    if isinstance(canonical, dict):
        merged = dict(local)
        merged.update(
            {
                key: value
                for key, value in canonical.items()
                if key not in {"source", "refs"}
            }
        )
        return merged
    return local or None


def _iter_document_entries(
    document: dict[str, Any],
) -> Iterator[tuple[str, Any, tuple[str, ...]]]:
    for section_id, payload in document.items():
        if not isinstance(payload, dict):
            continue
        if section_id == "direct_labels":
            for item_id, label in payload.items():
                if isinstance(label, str):
                    yield str(item_id), label, ("direct_labels",)
            continue
        if section_id == "meta":
            continue
        yield from _iter_node_entries(
            payload, (str(section_id),), resolve=False, node_id=str(section_id)
        )


def _iter_node_entries(
    node: dict[str, Any],
    group_path: tuple[str, ...],
    *,
    resolve: bool,
    node_id: str | None = None,
) -> Iterator[tuple[str, Any, tuple[str, ...]]]:
    # label がオブジェクトの場合は2通りの手書き慣習に対応する:
    #   {japanese/note...} -> グループ代表エントリの定義
    #   {id: 名前, ...}    -> 子エントリのマップ
    # グループとして辿られたノード自身が japanese/note を持つ場合、
    # そのノード id を代表エントリとして放出する (例: lightning_rod, pot)。
    # refs を持つグループは参照展開で完結するので対象外（id がエントリ名ではないため）。
    if node_id and "refs" not in node and ({"japanese", "note", "source"} & node.keys()):
        self_payload = {
            key: value
            for key, value in node.items()
            if key not in GROUP_STRUCTURE_KEYS and key != "label"
        }
        if self_payload:
            yield node_id, _maybe_resolve(self_payload, node_id, resolve), group_path
    label_value = node.get("label")
    if isinstance(label_value, dict):
        if {"japanese", "label", "note"} & label_value.keys():
            if node_id:
                yield node_id, label_value, group_path
        else:
            for sub_id, sub_label in label_value.items():
                if isinstance(sub_label, str):
                    yield str(sub_id), sub_label, group_path
    if resolve:
        ref_ids = node.get("refs")
        source = node.get("source")
        if _is_source_pointer(source) and isinstance(ref_ids, list):
            source_path = _resolve_source_path(source)
            if source_path is None:
                LOGGER.warning(
                    "entry_catalog_unresolved_group_source group=%s spec=%r",
                    "/".join(group_path) or "(root)",
                    source,
                )
            else:
                for ref_id in ref_ids:
                    canonical = _find_canonical_payload(source_path, str(ref_id))
                    if canonical is None:
                        LOGGER.warning(
                            "entry_catalog_missing_ref id=%s source=%s",
                            ref_id,
                            source_path.name,
                        )
                        continue
                    yield str(ref_id), canonical, group_path
    items = node.get("items")
    if isinstance(items, dict):
        for item_id, payload in items.items():
            if _looks_like_group(payload):
                yield from _iter_node_entries(
                    payload,
                    (*group_path, str(item_id)),
                    resolve=resolve,
                    node_id=str(item_id),
                )
                continue
            yield from _iter_entry_payload(str(item_id), payload, group_path, resolve)
    groups = node.get("groups")
    if isinstance(groups, dict):
        for group_id, payload in groups.items():
            if isinstance(payload, dict):
                yield from _iter_node_entries(
                    payload,
                    (*group_path, str(group_id)),
                    resolve=resolve,
                    node_id=str(group_id),
                )
    for key, value in node.items():
        if key in NON_ENTRY_KEYS:
            continue
        if _looks_like_group(value):
            yield from _iter_node_entries(
                value, (*group_path, str(key)), resolve=resolve, node_id=str(key)
            )
        elif isinstance(value, str):
            yield str(key), value, group_path
        elif isinstance(value, dict) and (
            {"japanese", "label", "note", "source"} & value.keys()
        ):
            yield from _iter_entry_payload(str(key), value, group_path, resolve)


def _iter_entry_payload(
    entry_id: str,
    payload: Any,
    group_path: tuple[str, ...],
    resolve: bool,
) -> Iterator[tuple[str, Any, tuple[str, ...]]]:
    yield entry_id, _maybe_resolve(payload, entry_id, resolve), group_path
    if isinstance(payload, dict):
        label_value = payload.get("label")
        if isinstance(label_value, dict) and not (
            {"japanese", "label", "note"} & label_value.keys()
        ):
            for sub_id, sub_label in label_value.items():
                if isinstance(sub_label, str):
                    yield str(sub_id), sub_label, group_path


def _maybe_resolve(payload: Any, entry_id: str, resolve: bool) -> Any:
    if resolve and isinstance(payload, dict) and _is_source_pointer(payload.get("source")):
        return _resolve_entry_payload(payload, entry_id)
    return payload


def _collect_grouped_block_labels(node: dict[str, Any], labels: dict[str, str]) -> None:
    for item_id, payload, _group_path in _iter_node_entries(node, (), resolve=True):
        label = _entry_label_from_payload(payload)
        if label:
            _merge_block_label(labels, item_id, label)


def _collect_grouped_entry_payloads(
    node: dict[str, Any],
    entries: dict[str, dict[str, Any]],
    *,
    root_section: str,
    group_path: tuple[str, ...] = (),
) -> None:
    for item_id, payload, found_path in _iter_node_entries(node, group_path, resolve=True):
        entry = _entry_payload_from_value(
            payload,
            root_section=root_section,
            group_path=found_path,
        )
        if entry is None:
            continue
        # note 持ちのエントリを note 無しの重複定義で潰さない
        existing = entries.get(item_id)
        if existing is not None and not entry.get("note") and existing.get("note"):
            continue
        entries[item_id] = entry

        if isinstance(payload, dict):
            variants = payload.get("variants")
            if isinstance(variants, dict):
                for variant_id, variant_name in variants.items():
                    entries[str(variant_id)] = {
                        "label": variant_name,
                        "japanese": variant_name,
                        "note": payload.get("note", ""),
                        "parent": item_id,
                        "section": root_section,
                        "group_path": list(found_path),
                    }


def _entry_payload_from_value(
    payload: Any,
    *,
    root_section: str,
    group_path: tuple[str, ...],
) -> dict[str, Any] | None:
    label = _entry_label_from_payload(payload)
    if not label:
        return None
    if isinstance(payload, dict):
        entry = dict(payload)
    else:
        entry = {}
    entry["label"] = label
    entry.setdefault("japanese", label)
    entry.setdefault("role", "")
    entry.setdefault("note", "")
    entry.setdefault("section", root_section)
    entry.setdefault("group_path", list(group_path))
    return entry


def _entry_label_from_payload(payload: Any) -> str | None:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        japanese = payload.get("japanese")
        if japanese:
            return str(japanese)
        label = payload.get("label")
        if label:
            return str(label)
    return None


def _merge_block_label(labels: dict[str, str], item_id: str, candidate: str) -> None:
    existing = labels.get(item_id)
    if existing is None or _should_replace_block_label(existing, candidate):
        labels[item_id] = candidate


def _should_replace_block_label(existing: str, candidate: str) -> bool:
    if existing == candidate:
        return False
    return len(candidate) > len(existing)
