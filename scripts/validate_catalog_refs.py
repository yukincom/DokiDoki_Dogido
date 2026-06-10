"""カタログ JSON の構造・参照整合性チェッカー。

使い方:
    python scripts/validate_catalog_refs.py

検出するもの:
- グループ直下に紛れ込んだ「グループらしき dict」(ネスト崩れの兆候)
- items 配下のエントリ値に items/groups/refs が入っている (ネスト崩れ)
- _source が指すファイルが存在しない
- ref / refs の id が参照先ファイルに見つからない
- "japanse" などのキータイポ
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ENTRIES_DIR = Path(__file__).resolve().parents[1] / "data" / "catalogs" / "entries"

RESERVED_KEYS = {
    "label", "english", "japanese", "note", "items", "groups", "_source",
    "ref", "refs", "meta", "variants", "role", "recommended_fields",
    "schema", "notes", "direct_labels", "description", "biomes", "priority",
    "poetic", "source", "parent",
}
KEY_TYPOS = {"japanse": "japanese"}

issues: list[str] = []


def resolve_source(spec: str) -> Path | None:
    name = str(spec).strip()
    base = Path(name)
    candidates = [
        ENTRIES_DIR / name,
        ENTRIES_DIR / base.parent / f"minecraft_{base.name}",
        ENTRIES_DIR / "block" / base.name,
        ENTRIES_DIR / "block" / f"minecraft_{base.name}",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def looks_like_group(value: object) -> bool:
    return isinstance(value, dict) and bool(
        {"items", "groups", "refs", "ref"} & value.keys()
    )


def entry_ids_in_file(path: Path) -> set[str]:
    ids: set[str] = set()

    def walk(node: object) -> None:
        if not isinstance(node, dict):
            return
        items = node.get("items")
        if isinstance(items, dict):
            ids.update(str(key) for key in items)
            for value in items.values():
                walk(value)
        groups = node.get("groups")
        if isinstance(groups, dict):
            for value in groups.values():
                walk(value)
        for key, value in node.items():
            if key in {"items", "groups"}:
                continue
            if isinstance(value, dict):
                if key not in RESERVED_KEYS:
                    ids.add(str(key))
                walk(value)
    data = json.loads(path.read_text(encoding="utf-8"))
    walk(data)
    return ids


def check_node(node: object, *, file: Path, path: str, under_items: bool) -> None:
    if not isinstance(node, dict):
        return
    for typo, correct in KEY_TYPOS.items():
        if typo in node:
            issues.append(f"[typo] {file.name} {path}: キー '{typo}' (正: '{correct}')")
    source = node.get("_source")
    if source is not None:
        target = resolve_source(str(source))
        if target is None:
            issues.append(f"[bad-source] {file.name} {path}: _source '{source}' が見つからない")
        else:
            ref_ids = node.get("refs") or node.get("ref")
            if isinstance(ref_ids, list):
                known = entry_ids_in_file(target)
                for ref_id in ref_ids:
                    if str(ref_id) not in known:
                        issues.append(
                            f"[bad-ref] {file.name} {path}: '{ref_id}' が {target.name} に無い"
                        )
    items = node.get("items")
    if isinstance(items, dict):
        for item_id, value in items.items():
            if looks_like_group(value):
                issues.append(f"[nesting] {file.name} {path}.items.{item_id}: エントリ値にグループ構造")
            check_node(value, file=file, path=f"{path}.items.{item_id}", under_items=True)
    groups = node.get("groups")
    if isinstance(groups, dict):
        for group_id, value in groups.items():
            check_node(value, file=file, path=f"{path}.groups.{group_id}", under_items=False)
    for key, value in node.items():
        if key in {"items", "groups"} or key in RESERVED_KEYS:
            continue
        if looks_like_group(value):
            issues.append(f"[nesting] {file.name} {path}: '{key}' がグループらしき形で直下に存在")
        check_node(value, file=file, path=f"{path}.{key}", under_items=under_items)


def main() -> int:
    for json_path in sorted(ENTRIES_DIR.rglob("*.json")):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            issues.append(f"[invalid-json] {json_path.name}: {exc}")
            continue
        if not isinstance(data, dict):
            continue
        for section_id, payload in data.items():
            check_node(payload, file=json_path, path=section_id, under_items=False)
    if issues:
        print(f"{len(issues)} 件の問題:")
        for issue in issues:
            print("  " + issue)
        return 1
    print("問題なし")
    return 0


if __name__ == "__main__":
    sys.exit(main())
