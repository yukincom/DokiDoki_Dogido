"""川柳だけが使う、カタログ原文からの読み取り専用派生層。

元 JSON の ``note`` は通常会話と将来の「あんちょこ」でも使う正本である。
このモジュールは正本を書き換えず、発句する瞬間だけ文単位の source atom を作る。
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class CatalogSourceSnapshot:
    """観測したカタログ項目の、その時点での原文スナップショット。"""

    catalog_type: str
    catalog_id: str
    label: str
    note_raw: str = ""
    reading: str = ""
    observation_role: str = ""
    # 発句が実際に参照しうる構造化フィールドだけを、path付きでsnapshotする。
    extra_fields: tuple[tuple[str, str], ...] = ()

    @property
    def source_ref(self) -> str:
        return f"{self.catalog_type}:{self.catalog_id}"

    def to_dict(self) -> dict[str, object]:
        return {
            "source_ref": self.source_ref,
            "catalog_type": self.catalog_type,
            "catalog_id": self.catalog_id,
            "label": self.label,
            "reading": self.reading or None,
            # 分割後の atom だけでなく、あんちょこと同じ原文も必ず残す。
            "note_raw": self.note_raw,
            "observation_role": self.observation_role,
            "extra_fields": [
                {"field_path": field_path, "text": text}
                for field_path, text in self.extra_fields
            ],
        }


@dataclass(frozen=True, slots=True)
class HaikuSourceAtom:
    """一句の一行が意味を借りられる、追跡可能な最小材料。"""

    atom_id: str
    text: str
    source_ref: str
    field_path: str
    observation_role: str
    kind: str = "catalog_fact"

    def to_prompt_dict(self) -> dict[str, str]:
        return {
            "atom_id": self.atom_id,
            "text": self.text,
            "source_ref": self.source_ref,
            "field_path": self.field_path,
            "observation_role": self.observation_role,
            "kind": self.kind,
        }


def catalog_source_snapshot(
    *,
    catalog_type: str,
    catalog_id: str,
    entry: dict[str, Any] | None,
    observation_role: str,
    fallback_label: str = "",
) -> CatalogSourceSnapshot | None:
    """entry のコピーから正本スナップショットを作る。entry 自体は変更しない。"""

    normalized_id = str(catalog_id or "").removeprefix("minecraft:").strip()
    if not normalized_id:
        return None
    payload = entry if isinstance(entry, dict) else {}
    label = str(
        payload.get("japanese")
        or payload.get("label")
        or fallback_label
        or normalized_id
    ).strip()
    if not label:
        return None
    extra_fields: list[tuple[str, str]] = []
    poetic = payload.get("poetic")
    if isinstance(poetic, dict):
        # mob_poetic_line と同じ正本要素へ戻せるよう、roleを先に、タグは元の添字つきで残す。
        role = str(poetic.get("role") or "").strip()
        if role:
            extra_fields.append(("poetic.role", role))
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
            extra_fields.extend(
                (f"poetic.{key}[{index}]", str(value).strip())
                for index, value in enumerate(values)
                if str(value).strip()
            )
    return CatalogSourceSnapshot(
        catalog_type=str(catalog_type).strip(),
        catalog_id=normalized_id,
        label=label,
        note_raw=str(payload.get("note") or "").strip(),
        reading=str(payload.get("reading") or "").strip(),
        observation_role=str(observation_role or "").strip(),
        extra_fields=tuple(extra_fields),
    )


def split_note_sentences(note_raw: str) -> tuple[str, ...]:
    """句点・終止記号の直後だけで分け、句読点を含む原文を保つ。"""

    text = str(note_raw or "").strip()
    if not text:
        return ()
    # 読点では切らない。「茶色で、ひび割れている」は一要素のまま扱う。
    return tuple(part.strip() for part in re.split(r"(?<=[。！？!?])", text) if part.strip())


def atoms_from_catalog_sources(
    sources: Iterable[CatalogSourceSnapshot],
    *,
    max_note_atoms: int = 8,
    max_extra_atoms_per_source: int = 5,
) -> tuple[HaikuSourceAtom, ...]:
    """カタログの名前と note 文を atom 化する。

    note の個数だけに上限を掛ける。原文スナップショットには上限を掛けない。
    """

    atoms: list[HaikuSourceAtom] = []
    seen_ids: set[str] = set()
    note_count = 0
    for source in sources:
        label_id = f"{source.source_ref}:japanese"
        if label_id not in seen_ids:
            seen_ids.add(label_id)
            atoms.append(
                HaikuSourceAtom(
                    atom_id=label_id,
                    text=source.label,
                    source_ref=source.source_ref,
                    field_path="japanese",
                    observation_role=source.observation_role,
                    kind="catalog_label",
                )
            )
        for index, sentence in enumerate(split_note_sentences(source.note_raw)):
            if note_count >= max_note_atoms:
                break
            atom_id = f"{source.source_ref}:note:{index}"
            if atom_id in seen_ids:
                continue
            seen_ids.add(atom_id)
            note_count += 1
            atoms.append(
                HaikuSourceAtom(
                    atom_id=atom_id,
                    text=sentence,
                    source_ref=source.source_ref,
                    field_path=f"note[{index}]",
                    observation_role=source.observation_role,
                )
            )
        for field_path, text in source.extra_fields[:max_extra_atoms_per_source]:
            safe_path = re.sub(r"[^0-9A-Za-z_]+", ":", field_path).strip(":").lower()
            atom_id = f"{source.source_ref}:{safe_path}"
            if atom_id in seen_ids:
                continue
            seen_ids.add(atom_id)
            atoms.append(
                HaikuSourceAtom(
                    atom_id=atom_id,
                    text=text,
                    source_ref=source.source_ref,
                    field_path=field_path,
                    observation_role=source.observation_role,
                    kind="catalog_field",
                )
            )
    return tuple(atoms)


def atoms_from_observations(features: Iterable[Any]) -> tuple[HaikuSourceAtom, ...]:
    """時刻・天気など、カタログ note 以外の実測材料も出典化する。"""

    atoms: list[HaikuSourceAtom] = []
    for feature in features:
        source = str(getattr(feature, "source", "observation") or "observation").strip()
        key = str(getattr(feature, "key", "") or "").strip()
        label = str(getattr(feature, "label", "") or "").strip()
        if not key or not label or label == "不明":
            continue
        safe_source = re.sub(r"[^0-9A-Za-z_]+", "_", source).strip("_").lower() or "observed"
        atom_id = f"observation:{safe_source}:{key}"
        atoms.append(
            HaikuSourceAtom(
                atom_id=atom_id,
                text=label,
                source_ref=f"observation:{key}",
                field_path="observed_label",
                observation_role=key,
                kind="observation",
            )
        )
    return tuple(atoms)


def atoms_from_spoken_preface(spoken_text: str) -> tuple[HaikuSourceAtom, ...]:
    """プレイヤーへ実際に話した見どころを、解釈由来の句材料へ分ける。

    カタログ事実や実測観測へ昇格はしない。発話に現れた節だけを別 provenance
    で残し、まだ口にしていない scene / irony の連想は句の出典にしない。
    """

    text = str(spoken_text or "").strip()
    if not text:
        return ()
    text = re.sub(r"[、，]?なんか浮かんできたわ[。．.!！]*$", "", text).strip()
    if not text or text in {"なんか浮かんできたわ", "なんか浮かんできた"}:
        return ()
    clauses = [
        clause.strip(" 。．.!！?？…")
        for clause in re.split(r"[。．.!！?？、，]+", text)
    ]
    atoms: list[HaikuSourceAtom] = []
    for index, clause in enumerate(clause for clause in clauses if len(clause) >= 2):
        atoms.append(
            HaikuSourceAtom(
                atom_id=f"preface:spoken:clause:{index}",
                text=clause,
                source_ref="preface:spoken",
                field_path=f"clause[{index}]",
                observation_role="spoken_preface",
                kind="preface_interpretation",
            )
        )
    return tuple(atoms)


def merge_source_atoms(*groups: Iterable[HaikuSourceAtom]) -> tuple[HaikuSourceAtom, ...]:
    """同じ ID と同じ表示材料を一度だけ残す。順序は観測優先順を維持する。"""

    merged: list[HaikuSourceAtom] = []
    seen_ids: set[str] = set()
    seen_text: set[str] = set()
    for group in groups:
        for atom in group:
            normalized_text = re.sub(r"\s+", "", atom.text)
            if atom.atom_id in seen_ids or normalized_text in seen_text:
                continue
            seen_ids.add(atom.atom_id)
            seen_text.add(normalized_text)
            merged.append(atom)
    return tuple(merged)


def catalog_notes_projection(sources: Iterable[CatalogSourceSnapshot]) -> tuple[str, ...]:
    """既存の場面プロンプト向け表示。データ源は source snapshot 一系統だけ。"""

    notes: list[str] = []
    for source in sources:
        if not source.note_raw:
            continue
        # 原文snapshotは切らない。従来どおり、場面プロンプトへの投影だけ短くする。
        note = source.note_raw
        if len(note) > 100:
            note = note[:99].rstrip() + "…"
        notes.append(f"{source.label}: {note}")
    return tuple(notes)


def source_atoms_from_materials(materials: dict[str, Any] | None) -> tuple[HaikuSourceAtom, ...]:
    """保存済み materials の読取り専用 snapshot を閉じた型へ戻す。

    元カタログへ戻って再解決しない。句を詠んだ時点で保存した atom だけを使い、
    欠損・重複・不正な行は捨てる。
    """

    rows = materials.get("source_atoms") if isinstance(materials, dict) else None
    if not isinstance(rows, list):
        return ()
    atoms: list[HaikuSourceAtom] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        values = {
            key: str(row.get(key) or "").strip()
            for key in (
                "atom_id",
                "text",
                "source_ref",
                "field_path",
                "observation_role",
                "kind",
            )
        }
        atom_id = values["atom_id"]
        if not atom_id or atom_id in seen or not values["text"] or not values["source_ref"]:
            continue
        seen.add(atom_id)
        atoms.append(
            HaikuSourceAtom(
                atom_id=atom_id,
                text=values["text"],
                source_ref=values["source_ref"],
                field_path=values["field_path"],
                observation_role=values["observation_role"],
                kind=values["kind"] or "catalog_fact",
            )
        )
    return tuple(atoms)


def line_source_ids_from_materials(
    materials: dict[str, Any] | None,
    *,
    verse_lines: list[str],
    allowed_atom_ids: set[str],
) -> dict[int, tuple[str, ...]]:
    """保存済み行出典を、現在句との一致を確認して復元する。"""

    rows = materials.get("line_sources") if isinstance(materials, dict) else None
    if not isinstance(rows, list):
        return {}
    result: dict[int, tuple[str, ...]] = {}
    used: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        index = row.get("line_index")
        text = str(row.get("text") or "").strip()
        raw_ids = row.get("atom_ids")
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or not 0 <= index < len(verse_lines)
            or index in result
            or text != verse_lines[index]
            or not isinstance(raw_ids, list)
            or not raw_ids
        ):
            continue
        atom_ids = tuple(str(value).strip() for value in raw_ids)
        if (
            any(not atom_id or atom_id not in allowed_atom_ids for atom_id in atom_ids)
            or len(set(atom_ids)) != len(atom_ids)
            or used.intersection(atom_ids)
        ):
            continue
        result[index] = atom_ids
        used.update(atom_ids)
    return result
