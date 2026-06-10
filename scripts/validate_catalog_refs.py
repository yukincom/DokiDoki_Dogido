"""カタログ JSON の構造・参照整合性チェッカー。

使い方:
    python scripts/validate_catalog_refs.py

検出するもの:
- グループ直下に紛れ込んだ「グループらしき dict」(ネスト崩れの兆候)
- items 配下のエントリ値に items/groups/refs が入っている (ネスト崩れ)
- source ポインタ（.json で終わる値）が指すファイルが存在しない
- refs の id が参照先ファイルに見つからない
- "japanse" / "_source" / "ref" などのキータイポ・旧表記
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
KEY_TYPOS = {
    "japanse": "japanese",
    "jpanese": "japanese",
    "japanees": "japanese",
    "ntoe": "note",
    "noet": "note",
    "_source": "source",  # 仮表記。参照解決コードが入ったので source に統一
    "ref": "refs",
}


def is_source_pointer(value: object) -> bool:
    return isinstance(value, str) and value.strip().lower().endswith(".json")

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
        {"items", "groups", "refs"} & value.keys()
    )


def entry_ids_in_file(path: Path) -> dict[str, int]:
    ids: dict[str, int] = {}

    def add(key: object) -> None:
        ids[str(key)] = ids.get(str(key), 0) + 1

    def walk(node: object) -> None:
        if not isinstance(node, dict):
            return
        items = node.get("items")
        if isinstance(items, dict):
            for key, value in items.items():
                add(key)
                walk(value)
        groups = node.get("groups")
        if isinstance(groups, dict):
            for group_id, value in groups.items():
                # label がオブジェクトのグループは「代表エントリ」を兼ねる
                # （例: bamboo, deepslate）。ローダーと同じ扱いで id に数える。
                if isinstance(value, dict):
                    label_value = value.get("label")
                    if isinstance(label_value, dict) and (
                        {"japanese", "label", "note"} & label_value.keys()
                    ):
                        add(group_id)
                    elif "refs" not in value and (
                        {"japanese", "note", "source"} & value.keys()
                    ):
                        add(group_id)
                walk(value)
        for key, value in node.items():
            if key in {"items", "groups"}:
                continue
            if isinstance(value, dict):
                if key not in RESERVED_KEYS:
                    add(key)
                walk(value)
    data = json.loads(path.read_text(encoding="utf-8"))
    walk(data)
    return ids


def check_node(
    node: object,
    *,
    file: Path,
    path: str,
    under_items: bool,
    node_key: str | None = None,
) -> None:
    if not isinstance(node, dict):
        return
    for typo, correct in KEY_TYPOS.items():
        if typo in node:
            issues.append(f"[typo] {file.name} {path}: キー '{typo}' (正: '{correct}')")
    for key in node:
        if isinstance(key, str) and key.rstrip() != key.rstrip(": "):
            issues.append(f"[typo] {file.name} {path}: キー '{key}' に余分なコロン/空白")
    source = node.get("source")
    if is_source_pointer(source):
        target = resolve_source(str(source))
        if target is None:
            issues.append(f"[bad-source] {file.name} {path}: source '{source}' が見つからない")
        else:
            ref_ids = node.get("refs")
            known = entry_ids_in_file(target)
            if isinstance(ref_ids, list):
                for ref_id in ref_ids:
                    if str(ref_id) not in known:
                        issues.append(
                            f"[bad-ref] {file.name} {path}: '{ref_id}' が {target.name} に無い"
                        )
            elif node_key:
                # エントリ単位のポインタ: 参照先に同じ id の正準データがあるか
                count = known.get(node_key, 0)
                if count == 0:
                    issues.append(
                        f"[bad-pointer] {file.name} {path}: '{node_key}' が {target.name} に無い"
                    )
                elif target.name == file.name and count < 2:
                    issues.append(
                        f"[self-pointer] {file.name} {path}: '{node_key}' が自分自身しか指していない（正準データが無い）"
                    )
    items = node.get("items")
    if isinstance(items, dict):
        for item_id, value in items.items():
            if looks_like_group(value):
                issues.append(f"[nesting] {file.name} {path}.items.{item_id}: エントリ値にグループ構造")
            check_node(
                value,
                file=file,
                path=f"{path}.items.{item_id}",
                under_items=True,
                node_key=str(item_id),
            )
    groups = node.get("groups")
    if isinstance(groups, dict):
        for group_id, value in groups.items():
            check_node(value, file=file, path=f"{path}.groups.{group_id}", under_items=False)
    for key, value in node.items():
        if key in {"items", "groups"} or key in RESERVED_KEYS:
            continue
        if looks_like_group(value):
            issues.append(f"[nesting] {file.name} {path}: '{key}' がグループらしき形で直下に存在")
        check_node(
            value,
            file=file,
            path=f"{path}.{key}",
            under_items=under_items,
            node_key=str(key),
        )


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
