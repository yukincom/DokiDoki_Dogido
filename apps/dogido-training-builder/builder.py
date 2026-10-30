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
HEADERS = ["使う", "モード", "発話", "正解分類", "メモ", "グループ"]
MODE_NAMES = {"通常": "normal", "通常対話": "normal", "normal": "normal", "川柳": "senryu", "senryu": "senryu"}
LABEL_NAMES = {
    "対象外": "not_target", "対象外・その他": "not_target", "モブ情報": "enemy_information",
    "剣に持ち替える": "equip_sword", "乗用動物の呼び寄せ": "recall_mount",
    "ドギドの呼び寄せ": "dogido_recall", "指定方向を向く": "look_direction",
    "右を向く": "look_relative_right", "指定した対象を見る": "look_target", "プレイヤーを見る": "look_player",
    "前を向く": "look_relative_front", "後ろを向く": "look_relative_back", "左を向く": "look_relative_left",
    "左前を向く": "look_relative_front_left", "右前を向く": "look_relative_front_right",
    "左後ろを向く": "look_relative_back_left", "右後ろを向く": "look_relative_back_right",
    "上を向く": "look_relative_up", "下を向く": "look_relative_down",
    "上五の行指定": "senryu_line_upper", "中七の行指定": "senryu_line_middle", "下五の行指定": "senryu_line_lower",
    "上五の具体変更": "senryu_change_upper", "中七の具体変更": "senryu_change_middle", "下五の具体変更": "senryu_change_lower",
    "川柳相談の終了": "senryu_end",
}


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def normalized(text):
    # 既存dataset.normalizedと同じ重複検査。学習へ渡すtext自体は変更しない。
    text = unicodedata.normalize("NFKC", text).casefold()
    text = "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in text)
    return "".join(c for c in text if not c.isspace() and unicodedata.category(c)[0] != "P")


def read_csv(paths):
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
        if len(fields) != len(set(fields)) or not {"モード", "発話", "正解分類"} <= set(fields):
            raise ValueError(f"{path.name}: 「モード・発話・正解分類」の列を、重複なく用意してください。")
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
            mode = MODE_NAMES.get(row["モード"].strip())
            if mode is None:
                raise ValueError(f"{where}: モードは「通常」か「川柳」にしてください。")
            text = row["発話"]
            if not text.strip() or not normalized(text):
                raise ValueError(f"{where}: 発話が空です。")
            label = row["正解分類"].strip()
            label = LABEL_NAMES.get(label, label)
            identity = (mode, normalized(text))
            if identity in seen:
                if seen[identity]["label"] != label:
                    raise ValueError(f"{where}: 同じ発話に異なる正解分類があります（{seen[identity]['where']}）。")
                skipped.append({"where": where, "reason": "CSV内の同じ発話・同じ分類", "text": text})
                continue
            result = {"id": "csv_" + sha((mode + "\0" + text).encode())[:20],
                      "group_id": row.get("グループ", "").strip() or "csv_" + sha((mode + normalized(text)).encode())[:20],
                      "text": text, "context": [], "label": label, "needs_clarification": False,
                      "tags": ["csv_addition"], "source": "user_example", "rationale": row.get("メモ", ""),
                      "where": where, "mode": mode}
            seen[identity] = result
            rows.append(result)
    if not rows:
        raise ValueError("学習に使う行がありません。CSVの「使う」列も確認してください。")
    return rows, skipped, sources


def prepare_profile(mode, additions, assets):
    base = assets / "baseline" / mode
    labels = json.loads((base / "labels.json").read_text(encoding="utf-8"))
    if mode == "normal" and any(r["label"] in ("look_relative_up", "look_relative_down") for r in additions):
        labels.update(look_relative_up=16, look_relative_down=17)
    splits = {s: [json.loads(line) for line in (base / f"{s}.jsonl").read_text(encoding="utf-8").splitlines()]
              for s in ("train", "dev", "test")}
    index = {normalized(r["text"]): (s, r) for s, rows in splits.items() for r in rows}
    groups = {r["group_id"]: s for s, rows in splits.items() for r in rows}
    changes = []
    for addition in additions:
        label, where = addition["label"], addition["where"]
        if label not in labels:
            raise ValueError(f"{where}: {mode}で使えない分類「{label}」です。分類一覧.csvを確認してください。")
        key = normalized(addition["text"])
        if key in index:
            split, original = index[key]
            if split != "train":
                raise ValueError(f"{where}: 既存の{split}評価用データと同じ発話です。学習へは追加できません。")
            old = original["label"]
            if old != label:
                original["label"] = label
                changes.append({"where": where, "action": "train_label_corrected", "id": original["id"], "old_label": old, "label": label})
            else:
                changes.append({"where": where, "action": "already_in_train", "id": original["id"], "label": label})
            continue
        if groups.get(addition["group_id"]) in ("dev", "test"):
            raise ValueError(f"{where}: 評価用データと同じグループです。学習へは追加できません。")
        row = {k: v for k, v in addition.items() if k not in ("where", "mode")}
        splits["train"].append(row)
        index[key] = ("train", row)
        groups[row["group_id"]] = "train"
        changes.append({"where": where, "action": "added_to_train", "id": row["id"], "label": label})
    missing = set(labels) - {r["label"] for r in splits["train"]}
    if missing:
        names = "、".join(next((n for n, value in LABEL_NAMES.items() if value == label), label) for label in sorted(missing))
        raise ValueError(f"{mode}: 学習例が0件の分類があります: {names}。上下を使う場合は両方の例を追加してください。")
    # 元の評価集合は変えず、新ラベルの評価例の不足は成績の範囲へ明記する。
    return labels, splits, changes


def cell(kind, source):
    result = {"cell_type": kind, "id": sha((kind + source).encode())[:12], "metadata": {}, "source": source.splitlines(keepends=True)}
    if kind == "code":
        result.update(execution_count=None, outputs=[])
    return result


def make_notebook(zip_name, digest, bundle_id, counts):
    description = "\n".join(f"- {mode}: train {v['train']} / dev {v['dev']} / test {v['test']}" for mode, v in counts.items())
    cells = [cell("markdown", f"""# ドギド：無料T4で分類器を学習

1. **ランタイム → ランタイムのタイプを変更 → T4 GPU** にします。無料T4の割り当て・利用時間は保証されません。
2. 上から順番にセルを実行し、同時に作られた `{zip_name}` をアップロードします。
3. 通常対話と川柳は別々のモデルとして、seed 42で順番に学習します。モデルは公開ベースのModernBERT-Ja 30Mから学習します。
4. 中断に備え、下の「Google Driveに途中保存する」をオンにすることを推奨します。
5. 終わったら結果ZIPをダウンロードします。本体への採用は別途行います。

{description}

既存dev/testは学習に使いません。既知の診断集合であり、未知の実機精度の保証にはなりません。
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
ROOT = Path('/content/dogido-training-{bundle_id}')
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
"""), cell("code", """#@title 学習と途中保存
Google_Driveに途中保存する = True #@param {type:"boolean"}
if Google_Driveに途中保存する:
    from google.colab import drive
    drive.mount('/content/drive')
    OUTPUT = Path('/content/drive/MyDrive/ドギド学習') / ROOT.name
else:
    OUTPUT = ROOT / 'results'
import sys, subprocess
subprocess.run([sys.executable, '-u', str(ROOT / 'train_colab.py'), '--root', str(ROOT), '--output', str(OUTPUT)], check=True)
"""), cell("code", """#@title 結果をダウンロード
result_zip = OUTPUT / 'dogido-training-results.zip'
if not result_zip.is_file():
    raise RuntimeError("学習完了後にこのセルを実行してください。")
files.download(str(result_zip))
""")]
    return encoded({"nbformat": 4, "nbformat_minor": 5, "metadata": {
        "colab": {"name": "ドギド_T4学習.ipynb", "provenance": []}, "accelerator": "GPU", "gpuClass": "standard",
        "kernelspec": {"display_name": "Python 3", "name": "python3"}, "language_info": {"name": "python"}}, "cells": cells})


def build(paths, assets=None, output_parent=None):
    assets = Path(assets or HERE / "assets").resolve()
    rows, skipped, sources = read_csv(paths)
    snapshot = json.loads((assets / "baseline-sha256.json").read_text())
    for name, expected in snapshot.items():
        if sha((assets / name).read_bytes()) != expected:
            raise ValueError("同梱教材が変更されています。アプリを作り直してください: " + name)
    profiles = {}
    files = {}
    for mode in ("normal", "senryu"):
        additions = [row for row in rows if row["mode"] == mode]
        if not additions:
            continue
        labels, splits, changes = prepare_profile(mode, additions, assets)
        for split, values in splits.items():
            files[f"data/{mode}/{split}.jsonl"] = (
                (assets / "baseline" / mode / f"{split}.jsonl").read_bytes() if split in ("dev", "test") else
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in values).encode())
        files[f"data/{mode}/labels.json"] = encoded(labels)
        profiles[mode] = {"labels": labels, "counts": {s: len(v) for s, v in splits.items()}, "changes": changes,
                          "train_label_counts": dict(Counter(r["label"] for r in splits["train"])),
                          "labels_without_test_examples": sorted(set(labels) - {r["label"] for r in splits["test"]})}
    for name in ("dataset.py", "evaluation.py", "train.py", "labels.json"):
        files["shared/" + name] = (assets / "shared" / name).read_bytes()
    files["train_colab.py"] = (assets / "train_colab.py").read_bytes()
    for path in sorted((assets / "evidence").glob("*.jsonl")):
        files["evidence/" + path.name] = path.read_bytes()
    for archive_name, raw, original_name in sources:
        files[archive_name] = raw
    bundle_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    protocol = {"schema": "dogido_csv_colab.v1", "bundle_id": bundle_id, "profiles": profiles,
                "training": {"model": "sbintuitions/modernbert-ja-30m", "revision": "8cb03f54cb9e30e72459e5f1cedc6d89c7d8dcb5",
                             "seed": 42, "epochs": 10, "learning_rate": 5e-5, "batch_size": 16, "max_length": 128,
                             "dtype": "float32", "attention": "sdpa", "selection": "dev macro_f1, then dev loss, then earlier epoch"},
                "skipped_csv_rows": skipped, "source_csvs": [{"archive_path": n, "filename": o, "sha256": sha(b)} for n, b, o in sources],
                "baseline_sha256": snapshot, "runtime_adoption": False, "free_gpu_guaranteed": False}
    files["training-protocol.json"] = encoded(protocol)
    files["input-sha256.json"] = encoded({name: sha(raw) for name, raw in sorted(files.items())})
    parent = Path(output_parent) if output_parent else Path(paths[0]).expanduser().resolve().parent / "出力"
    parent.mkdir(parents=True, exist_ok=True)
    destination = parent / ("ドギド教材-" + bundle_id)
    with tempfile.TemporaryDirectory(prefix=".building-", dir=str(parent)) as temporary:
        stage = Path(temporary)
        zip_path = stage / "教材.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, raw in sorted(files.items()):
                archive.writestr(name, raw)
        counts = {mode: value["counts"] for mode, value in profiles.items()}
        (stage / "ドギド_T4学習.ipynb").write_bytes(make_notebook(zip_path.name, sha(zip_path.read_bytes()), bundle_id, counts))
        (stage / "生成結果.json").write_bytes(encoded(protocol))
        report = ["# 教材を作成しました", "", "ドギド_T4学習.ipynbをColabで開き、T4 GPUを選んで教材.zipをアップロードしてください。", ""]
        for mode, profile in profiles.items():
            report += [f"- {mode}: {len(profile['labels'])}分類、{profile['counts']}"]
            if profile["labels_without_test_examples"]:
                report += ["  評価例がない分類: " + ", ".join(profile["labels_without_test_examples"]) + "。この分類の未知データへの精度は測定できません。"]
        report += [f"- 保留・重複で追加しなかったCSV行: {len(skipped)}件", "", "詳しい追加・訂正・重複の内訳は生成結果.jsonに保存しています。", "無料T4の利用可能時間は保証されません。Google Driveへの途中保存はNotebook上で選べます。", ""]
        (stage / "最初に読む.md").write_text("\n".join(report), encoding="utf-8")
        stage.rename(destination)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", nargs="+", type=Path)
    parser.add_argument("--assets", type=Path)
    parser.add_argument("--output-parent", type=Path)
    args = parser.parse_args()
    try:
        print(build(args.csv, args.assets, args.output_parent))
    except (ValueError, OSError, csv.Error, UnicodeError) as exc:
        parser.exit(1, str(exc) + "\n")
