"""CSVの追加教材を固定教材へ合わせ、Colab用NotebookとZIPをローカルで作る。"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unicodedata
import uuid
import zipfile

HERE = Path(__file__).resolve().parent
HEADERS = ["使う", "モード", "発話", "正解分類", "メモ", "グループ", "用途"]
SPLIT_NAMES = {"": "train", "学習": "train", "train": "train", "調整": "dev", "dev": "dev", "テスト": "test", "test": "test"}
PROJECT_FILE = "project.json"


def json_read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def load_project(assets, folder=None):
    folder = Path(folder).expanduser().resolve() if folder else None
    path = folder / PROJECT_FILE if folder else assets / "dogido_project.json"
    config = json_read(path)
    if config.get("schema") == "edit_span_project.v1":
        import span_builder
        return span_builder.load_project(folder)
    if config.get("schema") != "classifier_project.v1" or not config.get("id") or not config.get("name"):
        raise ValueError("プロジェクトのID・名前・形式を確認してください。")
    if config.get("input_format") not in ("plain_text", "dogido_current_v1"):
        raise ValueError("入力形式はplain_textかdogido_current_v1を指定してください。")
    modes = config.get("modes", {})
    if not modes or any(not key.isascii() or not key.replace("_", "").isalnum() for key in modes):
        raise ValueError("プロジェクトのモードIDを確認してください。")
    aliases = {}
    for key, mode in modes.items():
        for alias in [key, mode["name"], *mode.get("aliases", [])]:
            if alias in aliases and aliases[alias] != key:
                raise ValueError("プロジェクト内でモード名が重複しています。")
            aliases[alias] = key
    label_path = (folder / config["labels_file"]).resolve() if folder else assets / "dogido-labels.csv"
    if folder and folder not in label_path.parents:
        raise ValueError("分類一覧はプロジェクトフォルダ内に置いてください。")
    with label_path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        if not {"正解分類", "機械名"} <= set(reader.fieldnames or []):
            raise ValueError("分類一覧.csvには「正解分類・機械名」を用意してください。")
        entries = list(reader)
    tables = {mode: [] for mode in modes}
    for row in entries:
        if not any((v or "").strip() for v in row.values() if isinstance(v, str)):
            continue
        if None in row or any(v is None for v in row.values()):
            raise ValueError("分類一覧.csvの列数を確認してください。")
        mode = aliases.get(row.get("モード", "").strip()) if row.get("モード", "").strip() else (next(iter(modes)) if len(modes) == 1 else None)
        label, display = row["機械名"].strip(), row["正解分類"].strip()
        if mode is None or not label or not display or any(c in label for c in "\0\r\n"):
            raise ValueError("分類一覧.csvのモード・正解分類・機械名を確認してください。")
        tables[mode].append({"label": label, "display": display, "id": row.get("ID", "").strip()})
    return {"config": config, "folder": folder, "aliases": aliases, "labels": tables,
            "label_bytes": label_path.read_bytes()}


def resolve_project(paths, assets, project=None):
    adjacent = []
    for path in paths:
        folder = Path(path).expanduser().resolve().parent
        if (folder / PROJECT_FILE).is_file() and not any(folder.samefile(other) for other in adjacent):
            adjacent.append(folder)
    if len(adjacent) > 1:
        raise ValueError("別のプロジェクトのCSVは一緒にドロップできません。")
    selected = Path(project).expanduser().resolve() if project else next(iter(adjacent), None)
    if adjacent and not selected.samefile(adjacent[0]):
        raise ValueError("選択したプロジェクトとCSVのプロジェクトが異なります。")
    return load_project(assets, selected)


def create_project(folder, name):
    folder = Path(folder).expanduser().resolve()
    if not name.strip() or folder.exists():
        raise ValueError("新しい名前と、まだ存在しないプロジェクトフォルダを指定してください。")
    config = {"schema": "classifier_project.v1", "id": uuid.uuid4().hex, "name": name.strip(),
              "input_format": "plain_text", "labels_file": "分類一覧.csv", "modes": {"main": {"name": "分類"}}}
    folder.mkdir(parents=True)
    (folder / PROJECT_FILE).write_bytes(encoded(config))
    for filename, fields in (("分類一覧.csv", ["モード", "ID", "正解分類", "機械名", "備考"]),
                             ("教材.csv", HEADERS)):
        with (folder / filename).open("w", encoding="utf-8-sig", newline="") as stream:
            csv.writer(stream, lineterminator="\n").writerow(fields)
    return folder


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def normalized(text):
    # 既存dataset.normalizedと同じ重複検査。学習へ渡すtext自体は変更しない。
    text = unicodedata.normalize("NFKC", text).casefold()
    text = "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in text)
    return "".join(c for c in text if not c.isspace() and unicodedata.category(c)[0] != "P")


def read_csv(paths, project):
    rows, skipped, sources = [], [], []
    seen = {}
    for index, path in enumerate(paths, 1):
        path = Path(path).expanduser().resolve(strict=True)
        if path.suffix.lower() != ".csv":
            raise ValueError(f"CSVを指定してください: {path.name}")
        raw = path.read_bytes()
        try:
            stream = io.StringIO(raw.decode("utf-8-sig"), newline="")
        except UnicodeDecodeError:
            raise ValueError(f"{path.name}: UTF-8で保存してください。")
        reader = csv.DictReader(stream, strict=True)
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)) or not {"発話", "正解分類"} <= set(fields):
            raise ValueError(f"{path.name}: 「発話・正解分類」の列を、重複なく用意してください。")
        unknown = set(fields) - set(HEADERS)
        if unknown:
            raise ValueError(f"{path.name}: 未対応の列: {', '.join(sorted(unknown))}")
        sources.append((f"sources/{index:02d}.csv", raw, path.name))
        for row in reader:
            where = f"{path.name}:{reader.line_num}行目"
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"{where}: 列数が合いません。引用符とカンマを確認してください。")
            if not any(value.strip() for value in row.values()):
                continue
            if any("\0" in value for value in row.values()):
                raise ValueError(f"{where}: NUL文字は使えません。")
            use = row.get("使う", "").strip().lower()
            if use in ("いいえ", "保留", "no", "false", "0"):
                skipped.append({"where": where, "reason": "使う列が保留", "text": row["発話"]})
                continue
            if use not in ("", "はい", "yes", "true", "1"):
                raise ValueError(f"{where}: 「使う」は、はい／いいえ／保留にしてください。")
            value = row.get("モード", "").strip()
            mode = project["aliases"].get(value) if value else (next(iter(project["config"]["modes"])) if len(project["config"]["modes"]) == 1 else None)
            if mode is None:
                raise ValueError(f"{where}: このプロジェクトの分類一覧.csvにあるモードを指定してください。")
            text = row["発話"]
            if not text.strip() or not normalized(text):
                raise ValueError(f"{where}: 発話が空です。")
            label = row["正解分類"].strip()
            label_names = dict(project["config"].get("label_aliases", {}))
            label_names.update({r["display"]: r["label"] for r in project["labels"][mode]})
            label = label_names.get(label, label)
            split = SPLIT_NAMES.get(row.get("用途", "").strip())
            if split is None:
                raise ValueError(f"{where}: 用途は「学習」「調整」「テスト」にしてください。空欄は学習です。")
            identity = (mode, normalized(text))
            if identity in seen:
                if seen[identity]["split"] != split:
                    raise ValueError(f"{where}: 同じ発話が異なる用途にあります（{seen[identity]['where']}）。")
                if seen[identity]["label"] != label:
                    raise ValueError(f"{where}: 同じ発話に異なる正解分類があります（{seen[identity]['where']}）。")
                skipped.append({"where": where, "reason": "CSV内の同じ発話・同じ分類", "text": text})
                continue
            result = {"id": "csv_" + sha((mode + "\0" + text).encode())[:20],
                      "group_id": row.get("グループ", "").strip() or "csv_" + sha((mode + normalized(text)).encode())[:20],
                      "text": text, "context": [], "label": label, "needs_clarification": False,
                      "tags": ["csv_addition"], "source": "user_example", "rationale": row.get("メモ", ""),
                      "where": where, "mode": mode, "split": split}
            seen[identity] = result
            rows.append(result)
    if not rows:
        raise ValueError("使う行がありません。CSVの「使う」列も確認してください。")
    return rows, skipped, sources


def baseline_folder(project, mode, assets):
    source = project["config"]["modes"][mode].get("baseline")
    if not source:
        return None
    if source.startswith("bundled:"):
        base = (assets / "baseline" / source.split(":", 1)[1]).resolve()
        if (assets / "baseline").resolve() not in base.parents:
            raise ValueError("元教材の指定が不正です。")
        return base
    if project["folder"] is None:
        raise ValueError("元教材のフォルダを確認してください。")
    base = (project["folder"] / source).resolve()
    if project["folder"] not in base.parents:
        raise ValueError("元教材はプロジェクトフォルダ内に置いてください。")
    return base


def prepare_profile(mode, additions, assets, project):
    base = baseline_folder(project, mode, assets)
    original_labels = json_read(base / "labels.json") if base else {}
    rows = project["labels"][mode]
    optional = project["config"]["modes"][mode].get("optional_labels", [])
    if not any(row["label"] in optional for row in additions):
        rows = [r for r in rows if r["label"] not in optional]
    labels, aliases = {}, {}
    for row in rows:
        label = row["label"]
        number = int(row["id"]) if row["id"] else original_labels.get(label, len(labels))
        if label in labels or (row["display"] in aliases and aliases[row["display"]] != label):
            raise ValueError("分類一覧.csvの機械名・表示名が重複しています。")
        labels[label] = number
        aliases[row["display"]] = label
    if len(labels) < 2 or set(labels.values()) != set(range(len(labels))) or any(type(i) is not int for i in labels.values()):
        raise ValueError("分類一覧.csvには2分類以上を用意し、IDを0から重複なく連続で指定してください。")
    if any(display in labels and display != label for display, label in aliases.items()):
        raise ValueError("分類一覧.csvの表示名が別分類の機械名と重複しています。")
    if any(labels.get(label) != number for label, number in original_labels.items()):
        raise ValueError("元教材の分類IDは変更・削除できません。")
    labels = dict(sorted(labels.items(), key=lambda pair: pair[1]))
    splits = {s: [json.loads(line) for line in (base / f"{s}.jsonl").read_text(encoding="utf-8").splitlines()] if base else []
              for s in ("train", "dev", "test")}
    baseline_counts = {s: len(rows) for s, rows in splits.items()}
    validate_splits(splits, labels, allow_empty=True)
    index = {normalized(r["text"]): (s, r) for s, rows in splits.items() for r in rows}
    groups = {r["group_id"]: s for s, rows in splits.items() for r in rows}
    changes = []
    for addition in additions:
        label, where = addition["label"], addition["where"]
        target_split = addition["split"]
        if label not in labels:
            raise ValueError(f"{where}: {mode}で使えない分類「{label}」です。分類一覧.csvを確認してください。")
        key = normalized(addition["text"])
        if key in index:
            split, original = index[key]
            if split != target_split:
                raise ValueError(f"{where}: 既存の{split}評価用データと同じ発話です。学習へは追加できません。")
            old = original["label"]
            if old != label:
                if split != "train":
                    raise ValueError(f"{where}: 固定の{split}の正解分類は変更できません。")
                original["label"] = label
                changes.append({"where": where, "action": "train_label_corrected", "id": original["id"], "old_label": old, "label": label})
            else:
                changes.append({"where": where, "action": "already_in_" + split, "id": original["id"], "label": label})
            continue
        if addition["group_id"] in groups and groups[addition["group_id"]] != target_split:
            raise ValueError(f"{where}: 評価用データと同じグループです。学習へは追加できません。")
        row = {k: v for k, v in addition.items() if k not in ("where", "mode", "split")}
        splits[target_split].append(row)
        index[key] = (target_split, row)
        groups[row["group_id"]] = target_split
        changes.append({"where": where, "action": "added_to_" + target_split, "id": row["id"], "label": label})
    missing = set(labels) - {r["label"] for r in splits["train"]}
    if missing:
        names = "、".join(sorted(missing))
        raise ValueError(f"{mode}: 学習例が0件の分類があります: {names}。各分類の学習例を追加してください。")
    validate_splits(splits, labels)
    return labels, splits, changes, baseline_counts


def validate_splits(splits, labels, allow_empty=False):
    """固定教材とCSVを合わせた後も、原文・グループ・IDの分割混入を検査する。"""
    seen = {name: {} for name in ("発話", "グループ", "ID")}
    for split, rows in splits.items():
        if not rows and not allow_empty:
            raise ValueError(f"{split}: 教材が空です。「用途」に学習・調整・テストを分けて追加してください。")
        for row in rows:
            if row["context"] or row["label"] not in labels:
                raise ValueError(f"{split}: 履歴付き教材または不明な分類です。")
            for name, value in (("発話", normalized(row["text"])), ("グループ", row["group_id"]), ("ID", row["id"])):
                previous = seen[name].get(value)
                if previous is not None and previous != split:
                    raise ValueError(f"同じ{name}が{previous}と{split}に混入しています: {value}")
                seen[name][value] = split


def cell(kind, source):
    result = {"cell_type": kind, "id": sha((kind + source).encode())[:12], "metadata": {}, "source": source.splitlines(keepends=True)}
    if kind == "code":
        result.update(execution_count=None, outputs=[])
    return result


def make_notebook(zip_name, digest, bundle_id, profiles, project_name="ドギド"):
    description = "\n".join(
        f"- {mode}: 元教材 {sum(v['baseline_counts'].values())}件 ＋ CSV新規 {sum(v['added_counts'].values())}件 → "
        f"学習 {v['counts']['train']} / 調整 {v['counts']['dev']} / テスト {v['counts']['test']}件"
        for mode, v in profiles.items())
    cells = [cell("markdown", f"""# {project_name}：無料T4で分類器を学習

1. **ランタイム → ランタイムのタイプを変更 → T4 GPU** にします。無料T4の割り当て・利用時間は保証されません。
2. 上から順番にセルを実行し、同時に作られた `{zip_name}` をアップロードします。最後に操作ボタンが表示されます。
3. **「学習する」→「テストする」→「結果ZIPを保存」** の順に押します。テストだけやり直しても再学習しません。
4. プロジェクトのモードごとに、seed 42で順番に学習します。公開ベースのModernBERT-Ja 30Mから、前の教材とCSVの追加分を合わせて学習し直します。採用済みモデルへの追加学習ではありません。
5. 中断に備え、下の「Google Driveに途中保存する」をオンにすることを推奨します。本体への採用は別途行います。

{description}

調整devは最良epochの選択だけに使い、テストtestは学習にも選択にも使いません。
既存dev/testは既知の診断集合です。新しいテストはCSVの用途を「テスト」にして追加できます。
学習は無料枠を想定した1シードです。以前の複数シード・4専門モデルの比較実験とは条件が異なります。
"""), cell("code", """import torch
if not torch.cuda.is_available() or "T4" not in torch.cuda.get_device_name(0):
    raise RuntimeError("T4 GPUを選んで接続し直してください。割り当てがない場合は時間を置いてください。")
print("使用GPU:", torch.cuda.get_device_name(0))
"""), cell("code", "%pip -q install 'transformers==5.19.0' 'accelerate==1.15.0' 'safetensors==0.8.0'\n"),
             cell("code", f"""from google.colab import files
from pathlib import Path
import hashlib, json, zipfile, io
uploaded = files.upload()
if len(uploaded) != 1:
    raise ValueError("教材ZIPを1つだけ選んでください。")
payload = next(iter(uploaded.values()))
if hashlib.sha256(payload).hexdigest() != {digest!r}:
    raise ValueError("このNotebookと組の教材ZIPを選んでください。")
ROOT = Path('/content/modernbert-training-{bundle_id}')
ROOT.mkdir(exist_ok=True)
with zipfile.ZipFile(io.BytesIO(payload)) as archive:
    for name in archive.namelist():
        target = (ROOT / name).resolve()
        if not target.is_relative_to(ROOT.resolve()):
            raise ValueError("ZIPの保存先が不正です。")
    archive.extractall(ROOT)
checksums = json.loads((ROOT / 'input-sha256.json').read_text())
for name, expected in checksums.items():
    if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
        raise ValueError("教材が変更されています: " + name)
print("教材を確認しました:", ROOT)
protocol = json.loads((ROOT / 'training-protocol.json').read_text())
for mode, profile in protocol['profiles'].items():
    print(mode, '元教材:', profile['baseline_counts'], 'CSV新規:', profile['added_counts'], '合計:', profile['counts'])
"""), cell("code", """#@title 途中保存と操作ボタン
Google_Driveに途中保存する = True #@param {type:"boolean"}
if Google_Driveに途中保存する:
    from google.colab import drive
    drive.mount('/content/drive')
    OUTPUT = Path('/content/drive/MyDrive/ModernBERT学習') / ROOT.name
else:
    OUTPUT = ROOT / 'results'
import sys
sys.path.insert(0, str(ROOT))
from notebook_controls import show_controls
controls = show_controls(ROOT, OUTPUT)
""")]
    return encoded({"nbformat": 4, "nbformat_minor": 5, "metadata": {
        "colab": {"name": "ModernBERT_T4学習.ipynb", "provenance": []}, "accelerator": "GPU", "gpuClass": "standard",
        "kernelspec": {"display_name": "Python 3", "name": "python3"}, "language_info": {"name": "python"}}, "cells": cells})


def build(paths, assets=None, output_parent=None, project=None):
    assets = Path(assets or HERE / "assets").resolve()
    project = resolve_project(paths, assets, project)
    if project["config"]["schema"] == "edit_span_project.v1":
        import span_builder
        return span_builder.build(paths, assets, output_parent, project)
    rows, skipped, sources = read_csv(paths, project)
    snapshot = json.loads((assets / "baseline-sha256.json").read_text())
    for name, expected in snapshot.items():
        if sha((assets / name).read_bytes()) != expected:
            raise ValueError("同梱教材が変更されています。アプリを作り直してください: " + name)
    profiles = {}
    files = {}
    for mode in project["config"]["modes"]:
        additions = [row for row in rows if row["mode"] == mode]
        if not additions:
            continue
        labels, splits, changes, baseline_counts = prepare_profile(mode, additions, assets, project)
        for split, values in splits.items():
            files[f"data/{mode}/{split}.jsonl"] = (
                (baseline_folder(project, mode, assets) / f"{split}.jsonl").read_bytes() if split in ("dev", "test") and baseline_counts[split] and len(values) == baseline_counts[split] else
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in values).encode())
        files[f"data/{mode}/labels.json"] = encoded(labels)
        profiles[mode] = {"labels": labels, "counts": {s: len(v) for s, v in splits.items()}, "changes": changes,
                          "baseline_counts": baseline_counts,
                          "added_counts": {s: sum(c['action'] == 'added_to_' + s for c in changes) for s in splits},
                          "label_counts": {s: dict(Counter(r['label'] for r in values)) for s, values in splits.items()},
                          "split_integrity": {"normalized_text_overlap": 0, "group_overlap": 0, "id_overlap": 0},
                          "train_label_counts": dict(Counter(r["label"] for r in splits["train"])),
                          "labels_without_test_examples": sorted(set(labels) - {r["label"] for r in splits["test"]})}
    for name in ("dataset.py", "evaluation.py", "train.py"):
        files["shared/" + name] = (assets / "shared" / name).read_bytes()
    files["shared/labels.json"] = encoded(next(iter(profiles.values()))["labels"])
    files["train_colab.py"] = (assets / "train_colab.py").read_bytes()
    files["notebook_controls.py"] = (assets / "notebook_controls.py").read_bytes()
    files["project/project.json"] = encoded(project["config"])
    files["project/分類一覧.csv"] = project["label_bytes"]
    if project["config"].get("id") == "dogido":
        for path in sorted((assets / "evidence").glob("*.jsonl")):
            files["evidence/" + path.name] = path.read_bytes()
    for archive_name, raw, original_name in sources:
        files[archive_name] = raw
    bundle_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    protocol = {"schema": "classifier_csv_colab.v3", "bundle_id": bundle_id, "profiles": profiles,
                "project": {k: project["config"][k] for k in ("id", "name", "input_format")},
                "training_method": "公開ベースモデルから、前の教材とCSV追加分を合わせて再学習（採用モデルへの追加学習ではない）",
                "baseline_sources": {mode: project["config"]["modes"][mode].get("baseline") for mode in profiles},
                "training": {"model": "sbintuitions/modernbert-ja-30m", "revision": "8cb03f54cb9e30e72459e5f1cedc6d89c7d8dcb5",
                             "seed": 42, "epochs": 10, "learning_rate": 5e-5, "batch_size": 16, "max_length": 128,
                             "dtype": "float32", "attention": "sdpa", "selection": "dev macro_f1, then dev loss, then earlier epoch", "input_format": project["config"]["input_format"]},
                "skipped_csv_rows": skipped, "source_csvs": [{"archive_path": n, "filename": o, "sha256": sha(b)} for n, b, o in sources],
                "baseline_sha256": {key: value for key, value in snapshot.items() if key.startswith("shared/") or any(key.startswith("baseline/" + mode + "/") and project["config"]["modes"][mode].get("baseline") == "bundled:" + mode for mode in profiles)}, "runtime_adoption": False, "free_gpu_guaranteed": False}
    files["training-protocol.json"] = encoded(protocol)
    files["input-sha256.json"] = encoded({name: sha(raw) for name, raw in sorted(files.items())})
    parent = Path(output_parent) if output_parent else Path(paths[0]).expanduser().resolve().parent / "出力"
    parent.mkdir(parents=True, exist_ok=True)
    destination = parent / ("ModernBERT教材-" + bundle_id)
    with tempfile.TemporaryDirectory(prefix=".building-", dir=str(parent)) as temporary:
        stage = Path(temporary)
        zip_path = stage / "教材.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, raw in sorted(files.items()):
                archive.writestr(name, raw)
        (stage / "ModernBERT_T4学習.ipynb").write_bytes(make_notebook(zip_path.name, sha(zip_path.read_bytes()), bundle_id, profiles, project["config"]["name"]))
        (stage / "生成結果.json").write_bytes(encoded(protocol))
        report = ["# " + project["config"]["name"] + " の教材", "", protocol['training_method'], "",
                  "ModernBERT_T4学習.ipynbをColabで開き、T4 GPUを選んで教材.zipをアップロードしてください。",
                  "最後のセルで「学習する」→「テストする」→「結果ZIPを保存」を押します。", "",
                  "| モード | 元教材 | CSV新規 | 学習train | 調整dev | テストtest |", "|---|---:|---:|---:|---:|---:|"]
        for mode, profile in profiles.items():
            c = profile['counts']
            report += [f"| {mode} | {sum(profile['baseline_counts'].values())} | {sum(profile['added_counts'].values())} | {c['train']} | {c['dev']} | {c['test']} |"]
        report += ["", "元の教材は全件を引き継ぎます。trainだけを学習し、devで選び、testはテストボタンで評価します。",
                   "教材の中身/に用途別CSVを出力しました。同じ発話・グループ・IDの用途間混入は0件です。", ""]
        for mode, profile in profiles.items():
            if profile["labels_without_test_examples"]:
                report += ["  評価例がない分類: " + ", ".join(profile["labels_without_test_examples"]) + "。この分類の未知データへの精度は測定できません。"]
            folder = stage / "教材の中身" / mode
            folder.mkdir(parents=True)
            for split, title in (("train", "学習"), ("dev", "調整"), ("test", "テスト")):
                with (folder / (title + ".csv")).open("w", encoding="utf-8-sig", newline="") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(["ID", "グループ", "発話", "正解分類"])
                    for line in files[f"data/{mode}/{split}.jsonl"].decode().splitlines():
                        row = json.loads(line)
                        writer.writerow([row['id'], row['group_id'], row['text'], row['label']])
        report += [f"- 保留・重複で追加しなかったCSV行: {len(skipped)}件", "", "詳しい追加・訂正・重複の内訳は生成結果.jsonに保存しています。", "無料T4の利用可能時間は保証されません。Google Driveへの途中保存はNotebook上で選べます。", ""]
        (stage / "最初に読む.md").write_text("\n".join(report), encoding="utf-8")
        stage.rename(destination)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", nargs="*", type=Path)
    parser.add_argument("--project", type=Path)
    parser.add_argument("--create-project", type=Path)
    parser.add_argument("--name")
    parser.add_argument("--assets", type=Path)
    parser.add_argument("--output-parent", type=Path)
    args = parser.parse_args()
    try:
        if args.create_project:
            print(create_project(args.create_project, args.name or args.create_project.name))
        elif args.csv:
            print(build(args.csv, args.assets, args.output_parent, args.project))
        else:
            raise ValueError("CSVを指定してください。")
    except (ValueError, OSError, csv.Error, UnicodeError) as exc:
        parser.exit(1, str(exc) + "\n")
