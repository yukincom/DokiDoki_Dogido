"""川柳だけが使う、カタログ原文からの読み取り専用派生層。

元 JSON の ``note`` は通常会話と将来の「あんちょこ」でも使う正本である。
このモジュールは正本を書き換えず、発句する瞬間だけ文単位の source atom を作る。
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable


CLAIM_CLASSES = frozenset({"factual", "interpretive"})
CLAIM_SCOPES = frozenset(
    {
        "identity_only",
        "source_meaning",
        "observed_state",
        "poetic_interpretation",
    }
)


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
    kind: str
    # factual はカタログ原文・実測の範囲、interpretive は印象・取り合わせの範囲。
    # どちらも basis に無い新しい事実を足す許可にはしない。
    claim_class: str
    claim_scopes: tuple[str, ...]
    # 発話済み見どころの節だけが持つ、派生元の一次 atom ID。
    basis_atom_ids: tuple[str, ...] = ()

    def to_prompt_dict(self) -> dict[str, object]:
        return {
            "atom_id": self.atom_id,
            "text": self.text,
            "source_ref": self.source_ref,
            "field_path": self.field_path,
            "observation_role": self.observation_role,
            "kind": self.kind,
            "claim_class": self.claim_class,
            "claim_scopes": list(self.claim_scopes),
            "basis_atom_ids": list(self.basis_atom_ids),
        }


@dataclass(frozen=True, slots=True)
class PrefaceClause:
    """実際に話す見どころの一節と、その主張可能範囲。"""

    text: str
    basis_atom_ids: tuple[str, ...]
    claim_class: str
    claim_scopes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "text": self.text,
            "basis_atom_ids": list(self.basis_atom_ids),
            "claim_class": self.claim_class,
            "claim_scopes": list(self.claim_scopes),
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
                    claim_class="factual",
                    claim_scopes=("identity_only",),
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
                    kind="catalog_fact",
                    claim_class="factual",
                    claim_scopes=("source_meaning",),
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
                    claim_class="interpretive",
                    claim_scopes=("source_meaning",),
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
                claim_class="factual",
                claim_scopes=("observed_state",),
            )
        )
    return tuple(atoms)


def preface_clauses_from_payload(
    raw_clauses: object,
    *,
    source_atoms: Iterable[HaikuSourceAtom],
) -> tuple[PrefaceClause, ...] | None:
    """LLM の節ごとの自己申告を、一次 atom と閉じた主張範囲へ束縛する。

    scope はモデルに決めさせない。factual は引用した一次 atom の scope を継承し、
    interpretive は印象・関係の表現だけに狭める。一節でも不正なら見どころ全体を
    捨て、部分的に根拠の無い発話を作らない。
    """

    if not isinstance(raw_clauses, list) or not 1 <= len(raw_clauses) <= 3:
        return None
    atom_by_id = {atom.atom_id: atom for atom in source_atoms if not atom.basis_atom_ids}
    if not atom_by_id:
        return None
    clauses: list[PrefaceClause] = []
    seen_text: set[str] = set()
    total_chars = 0
    for raw in raw_clauses:
        if not isinstance(raw, dict):
            return None
        text = _single_spoken_clause(raw.get("text"))
        raw_ids = raw.get("basis_atom_ids")
        claim_class = str(raw.get("claim_class") or "").strip()
        if (
            not text
            or not isinstance(raw_ids, list)
            or not 1 <= len(raw_ids) <= 4
            or claim_class not in CLAIM_CLASSES
        ):
            return None
        basis_atom_ids = tuple(str(value).strip() for value in raw_ids)
        if (
            any(not atom_id or atom_id not in atom_by_id for atom_id in basis_atom_ids)
            or len(set(basis_atom_ids)) != len(basis_atom_ids)
        ):
            return None
        normalized_text = re.sub(r"\s+", "", text)
        if normalized_text in seen_text:
            return None
        seen_text.add(normalized_text)
        total_chars += len(text)
        if total_chars > 72:
            return None

        bases = tuple(atom_by_id[atom_id] for atom_id in basis_atom_ids)
        if claim_class == "factual":
            # 解釈 atom を事実へ昇格させる自己申告は受け入れない。
            if any(atom.claim_class != "factual" for atom in bases):
                return None
            claim_scopes = tuple(
                scope
                for scope in ("identity_only", "source_meaning", "observed_state")
                if any(scope in atom.claim_scopes for atom in bases)
            )
        else:
            claim_scopes = ("poetic_interpretation",)
        if not claim_scopes:
            return None
        clauses.append(
            PrefaceClause(
                text=text,
                basis_atom_ids=basis_atom_ids,
                claim_class=claim_class,
                claim_scopes=claim_scopes,
            )
        )
    return tuple(clauses)


def atoms_from_preface_clauses(
    clauses: Iterable[PrefaceClause],
) -> tuple[HaikuSourceAtom, ...]:
    """検証済みで実際に話す節だけを、句が参照できる派生 atom にする。"""

    return tuple(
        HaikuSourceAtom(
            atom_id=f"preface:spoken:clause:{index}",
            text=clause.text,
            source_ref="preface:spoken",
            field_path=f"clause[{index}]",
            observation_role="spoken_preface",
            kind="preface_clause",
            claim_class=clause.claim_class,
            claim_scopes=clause.claim_scopes,
            basis_atom_ids=clause.basis_atom_ids,
        )
        for index, clause in enumerate(clauses)
    )


def merge_source_atoms(*groups: Iterable[HaikuSourceAtom]) -> tuple[HaikuSourceAtom, ...]:
    """同じ ID と同じ表示材料を一度だけ残す。順序は観測優先順を維持する。"""

    merged: list[HaikuSourceAtom] = []
    seen_ids: set[str] = set()
    seen_text: set[str] = set()
    for group in groups:
        for atom in group:
            normalized_text = re.sub(r"\s+", "", atom.text)
            # 発話節は同じ字面でも一次 atom とは別の主張範囲を持つため残す。
            if atom.atom_id in seen_ids or (not atom.basis_atom_ids and normalized_text in seen_text):
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
                "claim_class",
            )
        }
        raw_scopes = row.get("claim_scopes")
        raw_basis = row.get("basis_atom_ids")
        atom_id = values["atom_id"]
        if (
            not atom_id
            or atom_id in seen
            or not values["text"]
            or not values["source_ref"]
            or values["claim_class"] not in CLAIM_CLASSES
            or not isinstance(raw_scopes, list)
            or not raw_scopes
            or any(not isinstance(scope, str) or scope not in CLAIM_SCOPES for scope in raw_scopes)
            or len(set(raw_scopes)) != len(raw_scopes)
            or not isinstance(raw_basis, list)
            or any(not isinstance(value, str) or not value.strip() for value in raw_basis)
            or len(set(raw_basis)) != len(raw_basis)
        ):
            continue
        seen.add(atom_id)
        atoms.append(
            HaikuSourceAtom(
                atom_id=atom_id,
                text=values["text"],
                source_ref=values["source_ref"],
                field_path=values["field_path"],
                observation_role=values["observation_role"],
                kind=values["kind"],
                claim_class=values["claim_class"],
                claim_scopes=tuple(raw_scopes),
                basis_atom_ids=tuple(value.strip() for value in raw_basis),
            )
        )
    atom_by_id = {atom.atom_id: atom for atom in atoms}
    valid: list[HaikuSourceAtom] = []
    for atom in atoms:
        if atom.kind == "preface_clause":
            bases = tuple(atom_by_id.get(atom_id) for atom_id in atom.basis_atom_ids)
            expected_scopes = (
                tuple(
                    scope
                    for scope in ("identity_only", "source_meaning", "observed_state")
                    if any(
                        base is not None and scope in base.claim_scopes
                        for base in bases
                    )
                )
                if atom.claim_class == "factual"
                else ("poetic_interpretation",)
            )
            if (
                not bases
                or any(
                    base is None
                    or base.kind == "preface_clause"
                    or not _primary_claim_contract_valid(base)
                    for base in bases
                )
                or (
                    atom.claim_class == "factual"
                    and any(base.claim_class != "factual" for base in bases if base is not None)
                )
                or atom.claim_scopes != expected_scopes
            ):
                continue
        elif atom.basis_atom_ids:
            continue
        elif not _primary_claim_contract_valid(atom):
            continue
        valid.append(atom)
    return tuple(valid)


def _single_spoken_clause(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip().strip("「」\"' 。．.!！?？…")
    if (
        not 2 <= len(text) <= 36
        or any(marker in text for marker in ("\n", "\r"))
        or any(marker in text for marker in ("プレイヤー", "Y座標", "ｙ座標", "%", "％", "確率"))
    ):
        return ""
    return text


def _primary_claim_contract_valid(atom: HaikuSourceAtom) -> bool:
    expected = {
        "catalog_label": ("factual", ("identity_only",)),
        "catalog_fact": ("factual", ("source_meaning",)),
        "catalog_field": ("interpretive", ("source_meaning",)),
        "observation": ("factual", ("observed_state",)),
    }.get(atom.kind)
    return expected == (atom.claim_class, atom.claim_scopes)


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
