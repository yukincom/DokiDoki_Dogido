"""SSD句集を読み取り、出典付きの具体変更スロット教材を独立領域へ合成する。

見込み: 句の表現と実依頼に沿う文型を独立に組み合わせると未知の表現も抽出できる。
合格: 全offsetが原文一致、同一句とrevisionは同一分割、実対話評価の句は学習不使用。
範囲: テキスト教材。合成の正解は挿入時の範囲で確定し、過去のLLM編集を正解にしない。
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import random
import re
from string import Formatter

from span_model import validate_spans, write_json

EXPECTATION = "原文の変更元・変更先だけを取り、行呼称・依頼の助詞・説明を含めない"
CRITERIA = "roleと開始/終了offsetがすべて一致。原文にない読みや漢字へ変換しない"

# 文型は対話の『XをYに変えましょう』『上の句をYに』を基に作成。
# 分割ごとに言い回しを固定し、評価文型を学習へ混ぜない。
TEMPLATES = {
    "train": {
        "pair": ["{line}の{old}を{new}に変えてください", "{old}を{new}に変えましょう",
            "{line}の{old}を{new}にして", "{old}から{new}に変更してください",
            "{line}の『{old}』を『{new}』にしてみよう", "{old}を{new}に変えてみない？",
            "{old}じゃなくて{new}にしよう", "{line}、{old}のところは{new}にしたい",
            "{line}の{old}を{new}に変更しましょう", "{old}ではなく{new}にしてください",
            "{old}→{new}", "{old} -> {new}"],
        "whole": ["{line}を{new}に変更してください", "{line}を{new}に変えようか",
            "{line}は{new}にして", "{new}にしましょう", "{line}を『{new}』に変えてみよう",
            "{line}は{new}でお願いします", "{new}に変えてください", "{line}、{new}にしたいな"],
        "from_only": ["{line}の{old}を変えたい", "{old}のところを直したいな"],
        "none": ["{line}を変えようかな", "もう少し春らしくして", "この句はいいね",
            "今日はここでおしまいにしよう", "{line}の読み方を教えて", "さっきの案に戻したい",
            "{line}をもう少し考えよう", "もっと景色が浮かぶ言葉にしてほしい"],
    },
    "dev": {
        "pair": ["{line}の{old}を{new}へ変えてもらえる？", "{old}の代わりに{new}を使おう",
                 "{line}は{old}から{new}へ直したいな"],
        "whole": ["{line}を{new}に直してほしいな", "{line}、次は『{new}』にしようか"],
        "from_only": ["{old}の部分を考え直したい"],
        "none": ["{line}を違う感じにしてほしい", "この表現は好きだな", "別の案を考えてくれる？"],
    },
    "test": {
        "pair": ["{line}の{old}を{new}に差し替えてほしい", "{old}という言葉は{new}に置き換えよう",
                 "{line}の『{old}』、ここは『{new}』に直せるかな"],
        "whole": ["{line}は丸ごと{new}にしてもらおう", "{line}には{new}を使ってください"],
        "from_only": ["{old}の言い回しを変更したいんだ"],
        "none": ["{line}はまだしっくりこないね", "この句の意味を聞かせて", "もうちょっと明るい感じにしない？"],
    },
}

LINE_NAMES = [["上五", "上の句", "かみのく", "髪の句", "紙の句"],
              ["中七", "中の句", "なかく", "中核", "中区"], ["下五", "下の句", "しものく"]]
PREFIXES = ["", "", "じゃあ", "そうだな、", "えっと、", "うーんと", "まず", "つぎに", "それなら"]
SUFFIXES = ["", "", "。", "！", "。そのほうが景色に合うと思う", "、ほかはそのままで"]


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def render(template, values, prefix="", suffix=""):
    """placeholderを挿入した瞬間に正解offsetを保存。完成文の再解析はしない。"""
    text, spans = prefix, []
    for literal, field, _, _ in Formatter().parse(template):
        text += literal
        if field is None:
            continue
        value = values[field]
        start = len(text)
        text += value
        if field in {"old", "new"}:
            spans.append({"role": "from" if field == "old" else "to", "start": start,
                          "end": len(text), "text": value})
    return text + suffix, spans


def load_sources(memory_root):
    """セッション正本の句・修正・相談だけを読む。私的素材と入力hashは出力領域に残す。"""
    sources = []
    rows = defaultdict(list)
    for filename, kind in [("haiku_entries.jsonl", "entries"), ("haiku_revisions.jsonl", "revisions"),
                           ("haiku_workshop_turns.jsonl", "turns")]:
        for path in sorted((memory_root / "sessions").glob("*/long_term/" + filename)):
            data = path.read_bytes()
            sources.append({"path": str(path), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
            for i, line in enumerate(data.decode().splitlines(), 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                row["source_file"] = str(path)
                row["source_line"] = i
                row["source_session"] = path.parents[1].name
                rows[kind].append(row)
    return rows, sources


def group_poems(rows):
    """同じentryの各版と、全文が同じ別entryを一つの群へ束ねる。"""
    poems = {}
    for row in rows["entries"]:
        poem = poems.setdefault(row["id"], {"id": row["id"], "variants": set(), "provenance": []})
        poem["provenance"].append({"file": row["source_file"], "line": row["source_line"], "id": row["id"]})
        for key in ["text", "surface_text", "reading_text"]:
            if isinstance(row.get(key), str) and row[key].strip():
                poem["variants"].add(row[key].strip())
    for row in rows["revisions"]:
        if row["haiku_id"] not in poems:
            continue
        poem = poems[row["haiku_id"]]
        for key in ["original_text", "base_text", "base_surface_text", "revised_text", "revised_surface_text"]:
            if isinstance(row.get(key), str) and row[key].strip():
                poem["variants"].add(row[key].strip())
        poem["provenance"].append({"file": row["source_file"], "line": row["source_line"], "id": row["id"]})
    parent = {key: key for key in poems}
    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key
    owners = {}
    for key, poem in poems.items():
        for verse in sorted(poem["variants"]):
            normalized = re.sub(r"\s+", "", verse)
            if normalized in owners:
                parent[find(key)] = find(owners[normalized])
            else:
                owners[normalized] = key
    grouped = defaultdict(list)
    for key, poem in poems.items():
        poem["variants"] = sorted(poem["variants"])
        grouped[find(key)].append(poem)
    return dict(grouped)


def manual_training(rows, reserved_ids, annotation_path):
    """履歴の原文に対する少数の確認済み注釈。モデルの修正結果を引用しない。"""
    annotations = json.loads(annotation_path.read_text())
    result = []
    seen = set()
    for turn in rows["turns"]:
        text = turn.get("player_text", "")
        if text not in annotations or text in seen or turn["entry_id"] in reserved_ids:
            continue
        if turn.get("event_kind") != "turn_result":
            continue
        seen.add(text)
        spans = []
        cursor = 0
        for role, value in zip(["from", "to"], annotations[text]):
            if value is None:
                continue
            start = text.index(value, cursor)
            spans.append({"role": role, "start": start, "end": start + len(value), "text": value})
            cursor = start + len(value)
        result.append({"id": "real-train-" + turn["id"], "entry_id": turn["entry_id"],
            "text": text, "current_verse": turn.get("base_verse") or "", "spans": spans,
            "source_file": turn["source_file"], "source_id": turn["id"], "kind": "real_manually_annotated",
            "expectation": EXPECTATION, "pass_criteria": CRITERIA, "scope": "実対話の保存済みASR本文"})
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--memory-root", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--manual-annotations", type=Path, required=True)
    parser.add_argument("--regression", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Output exists; create a new dataset version")
    args.output.mkdir(parents=True)
    rng = random.Random(args.seed)
    audit = json.loads(args.audit.read_text())
    # 評価ファイルは検証・モデル選択に用いず、学習対象から除くIDだけ先に読む。
    evaluation = audit["extraction"] + audit["non_extraction"]
    original_regression = audit["regression"]
    reserved_ids = {r["entry_id"] for r in evaluation + original_regression + audit["ambiguous"]}
    regression = json.loads(args.regression.read_text())
    reserved_ids |= {r["entry_id"] for r in regression}
    rows, sources = load_sources(args.memory_root)
    groups = group_poems(rows)
    reserved_groups = {g for g, poems in groups.items() if any(p["id"] in reserved_ids for p in poems)}
    reserved_ids |= {p["id"] for g in reserved_groups for p in groups[g]}
    available = sorted(set(groups) - reserved_groups)
    rng.shuffle(available)
    ndev = max(4, len(available) // 8)
    ntest = max(4, len(available) // 8)
    assignments = {g: "dev" if i < ndev else "test" if i < ndev + ntest else "train" for i, g in enumerate(available)}
    # 実対話の少数教師を使う句はtrainへ固定。holdout優先で予約群を戻さない。
    manual = manual_training(rows, reserved_ids, args.manual_annotations)
    manual_ids = {r["entry_id"] for r in manual}
    for g, poems in groups.items():
        if g not in reserved_groups and any(p["id"] in manual_ids for p in poems):
            assignments[g] = "train"
    materials = defaultdict(list)
    for group, split in assignments.items():
        for poem in groups[group]:
            for verse in poem["variants"]:
                for line_index, line in enumerate(verse.splitlines()):
                    if 2 <= len(line.strip()) <= 24:
                        materials[split].append({"text": line.strip(), "verse": verse,
                            "entry_id": poem["id"], "group_id": group, "line_index": line_index})
    # 同じ一行を別分割で再使用しない。実対話に使われた句の全行も先に予約する。
    protected = set()
    for group in reserved_groups:
        for poem in groups[group]:
            protected |= {line.strip() for verse in poem["variants"] for line in verse.splitlines() if line.strip()}
    protected |= {s["text"] for row in evaluation + regression for s in row["spans"]}
    manual = [row for row in manual if not any(s["text"] in protected for s in row["spans"])
              and not any(line.strip() in protected for line in row["current_verse"].splitlines())]
    for split in ["test", "dev", "train"]:
        unique = {}
        for item in materials[split]:
            if item["text"] not in protected and not any(line.strip() in protected for line in item["verse"].splitlines()):
                unique.setdefault(item["text"], item)
        materials[split] = list(unique.values())
        protected |= set(unique)
    all_rows = {"train": [], "dev": [], "test": []}
    seen = set()
    for split, count in [("train", 3600), ("dev", 480), ("test", 480)]:
        pool = materials[split]
        if len(pool) < 8:
            raise ValueError(f"Not enough independent phrase materials in {split}: {len(pool)}")
        attempts = 0
        while len(all_rows[split]) < count:
            attempts += 1
            if attempts > count * 30:
                raise ValueError("Insufficient unique synthetic combinations")
            source, target = rng.sample(pool, 2)
            old, new = source["text"], target["text"]
            if rng.random() < 0.08:
                new = old  # 同じ文字列でも出現位置からfrom/toを区別する。
            # 一部置換も、挿入時に境界を確定する教材として含める。
            partial = rng.random() < 0.20 and len(old) >= 4
            if partial:
                begin = rng.randrange(0, len(old) - 2)
                old = old[begin:rng.randrange(begin + 2, len(old) + 1)]
            mode = rng.choices(["pair", "whole", "from_only", "none"], [45, 30, 5, 20])[0]
            template = rng.choice(TEMPLATES[split][mode])
            text, spans = render(template, {"line": rng.choice(LINE_NAMES[source["line_index"] % 3]), "old": old, "new": new},
                                 rng.choice(PREFIXES), rng.choice(SUFFIXES))
            if text in seen:
                continue
            seen.add(text)
            row = {"id": f"{split}-{len(all_rows[split]):05d}", "text": text, "current_verse": source["verse"],
                "spans": spans, "kind": "synthetic_" + mode, "partial_source": partial,
                "source_entry_ids": [source["entry_id"], target["entry_id"]],
                "source_groups": [source["group_id"], target["group_id"]], "template": template,
                "expectation": EXPECTATION, "pass_criteria": CRITERIA,
                "scope": "句集の語句から合成したテキスト。実発話・ASRの成功を意味しない"}
            validate_spans(row)
            all_rows[split].append(row)
    # 手動注釈を繰り返す場合も同一train内のみ。実例数を水増しせず記録する。
    for repeat in range(4):
        for row in manual:
            all_rows["train"].append({**row, "id": row["id"] + f"-repeat{repeat}"})
    all_rows["real_test"] = [{**r, "id": "real-test-" + r["source_id"],
        "expectation": EXPECTATION, "pass_criteria": CRITERIA, "scope": "保存された実対話の原文。ASRの修復はしない"}
        for r in evaluation if r.get("category") != "regression"]
    all_rows["regression"] = regression
    for split, split_rows in all_rows.items():
        for row in split_rows:
            validate_spans(row)
        (args.output / f"{split}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in split_rows))
    write_json(args.output / "source-manifest.json", sources)
    write_json(args.output / "poem-groups.json", {"groups": groups, "assignments": assignments,
        "reserved_groups": sorted(reserved_groups), "reserved_ids": sorted(reserved_ids)})
    write_json(args.output / "ambiguous-not-scored.json", audit["ambiguous"])
    write_json(args.output / "materials.json", dict(materials))
    report = {"seed": args.seed, "source_counts": {k: len(v) for k, v in rows.items()},
        "poem_groups": len(groups), "reserved_groups": len(reserved_groups),
        "assigned_groups": dict(Counter(assignments.values())),
        "material_lines": {k: len(v) for k, v in materials.items()}, "manual_training_unique": len(manual),
        "rows": {k: len(v) for k, v in all_rows.items()},
        "kinds": {k: dict(Counter(r.get("kind", "real") for r in v)) for k, v in all_rows.items()},
        "audit_sha256": hashlib.sha256(args.audit.read_bytes()).hexdigest(),
        "manual_annotations_sha256": hashlib.sha256(args.manual_annotations.read_bytes()).hexdigest(),
        "template_disjoint": all(not set(TEMPLATES[a][m]) & set(TEMPLATES[b][m])
            for a, b in [("train", "dev"), ("train", "test"), ("dev", "test")] for m in TEMPLATES[a]),
        "scope": "Synthetic rows do not increase independent source diversity; real evaluation is a small local sample"}
    write_json(args.output / "dataset-report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
