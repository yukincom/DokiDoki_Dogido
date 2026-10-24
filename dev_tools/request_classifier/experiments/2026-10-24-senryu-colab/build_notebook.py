"""公開保存版の学習コードと固定済み424件を、前回形式のColabへ梱包する。"""
from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import zipfile


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
MATERIAL_COMMIT = "3f0250cdb1838d231ca5f7411db8ded23a997be8"
ARCHIVE_SHA256 = "aae4355c088c0436f3b019fa6674349e8b4091b51998a3f5b39274d9f1a8c2af"
ARCHIVE_ROOT = "colab-modernbert-ja-30m-2026-10-14/"
SOURCE_URL = "https://github.com/yukincom/AI-Curious-Community/releases/tag/modernbert-research-2026-10-24"
CLASSIFIER = "dev_tools/request_classifier/"
RELEASE = CLASSIFIER + "releases/2026-10-24-senryu-only-424"
FEEDBACK = CLASSIFIER + "curation/2026-10-24-live-feedback"
NAME = "modernbert-senryu424"
NOTEBOOK = "ModernBERT_Ja_30M_川柳8分類424件_2026-10-24.ipynb"
OLD_NAME = "modernbert-v7-pilot890"
OLD_RELEASE = "dev_tools/request_classifier/releases/v7-verse-variety-890"
OLD_COMMIT = "bc2062775291c99ce18b4eac3a47c3a413a06f0f"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded_json(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def set_source(cell: dict, source: str) -> None:
    cell["source"] = source.splitlines(keepends=True)
    if cell["cell_type"] == "code":
        ast.parse(source)
        cell.update(execution_count=None, outputs=[])


def build(source_archive: Path) -> None:
    archive_bytes = source_archive.read_bytes()
    if digest(archive_bytes) != ARCHIVE_SHA256:
        raise ValueError("Source archive SHA-256 differs from the published snapshot")
    inputs = {}
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
        for name in ("dataset.py", "evaluation.py", "train.py", "labels.json", "requirements-train.txt"):
            path = CLASSIFIER + name
            inputs[path] = archive.extractfile(ARCHIVE_ROOT + path).read()
        template_path = "ModernBERT_Ja_30M_23分類890件_2026-10-23.ipynb"
        template_bytes = archive.extractfile(ARCHIVE_ROOT + template_path).read()
        notebook = json.loads(template_bytes)
    source_hashes = {path: digest(data) for path, data in inputs.items()}
    material_dirs = [RELEASE, CLASSIFIER + "releases/2026-10-24-senryu-only-417", FEEDBACK]
    paths = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", MATERIAL_COMMIT, "--", *material_dirs], cwd=REPO,
    ).decode().splitlines()
    if len(paths) != 23:
        raise ValueError("The frozen material snapshot must contain 23 files")
    for path in paths:
        data = subprocess.check_output(["git", "show", f"{MATERIAL_COMMIT}:{path}"], cwd=REPO)
        if data != (REPO / path).read_bytes():
            raise ValueError(f"Working material differs from the fixed commit: {path}")
        inputs[path] = data
    labels = json.loads(inputs[RELEASE + "/labels.json"])
    splits = {split: [json.loads(line) for line in inputs[f"{RELEASE}/{split}.jsonl"].splitlines()]
              for split in ("train", "dev", "test")}
    counts = {split: len(rows) for split, rows in splits.items()}
    if counts != {"train": 349, "dev": 39, "test": 36} or len(labels) != 8:
        raise ValueError("Unexpected material counts or labels")
    freeze = json.loads(inputs[RELEASE + "/freeze.json"])
    for name, expected in freeze["output_sha256"].items():
        if digest(inputs[RELEASE + "/" + name]) != expected:
            raise ValueError(f"Frozen material hash differs: {name}")
    protocol = {
        "schema": "colab_training.v1", "profile": NAME, "material_commit": MATERIAL_COMMIT,
        "release": RELEASE, "counts": counts, "label_to_id": labels,
        "release_sha256": {p.removeprefix(RELEASE + "/"): digest(b) for p, b in inputs.items()
                           if p.startswith(RELEASE + "/")},
        "training": {
            "model": "sbintuitions/modernbert-ja-30m",
            "revision": "8cb03f54cb9e30e72459e5f1cedc6d89c7d8dcb5",
            "seeds": [42, 43, 44], "learning_rate": 5e-5, "epochs": 10, "batch_size": 16,
            "max_length": 128, "max_context": 0, "dtype": "float32", "attention": "sdpa",
            "initialization": "base pretrained model with a new 8-label classification head",
            "full_finetuning": True, "class_weights": False, "oversampling": False,
            "truncation": False, "tf32": False,
            "selection": "best epoch per seed by dev macro_f1, then dev loss, then earlier epoch; freeze all selections before test; report all seeds",
        },
        "evaluation_scope": freeze["evaluation_scope"],
        "runtime_routing": freeze["routing_contract"], "status": "prepared_not_run",
    }
    inputs["training-protocol.json"] = encoded_json(protocol)
    inputs["source-provenance.json"] = encoded_json({
        "source_archive_url": SOURCE_URL, "source_archive_sha256": ARCHIVE_SHA256,
        "source_archive_head": "54005915153d3f7d7f83226cfb7576f156d3781f",
        "unchanged_training_sources_sha256": source_hashes,
        "template_notebook": template_path, "template_notebook_sha256": digest(template_bytes),
        "material_commit": MATERIAL_COMMIT,
        "notebook_changes": ["8分類424件の固定データ", "川柳8分類のdev推移", "追加7件の学習内確認", "表示用改行の整形"],
    })
    hashes = {path: digest(data) for path, data in inputs.items()}
    payload_buffer = io.BytesIO()
    with zipfile.ZipFile(payload_buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, data in sorted({**inputs, "input-sha256.json": encoded_json(hashes)}.items()):
            info = zipfile.ZipInfo(path, date_time=(2026, 10, 24, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    payload = payload_buffer.getvalue()
    cells = notebook["cells"]
    if len(cells) != 10:
        raise ValueError("Unexpected previous notebook structure")
    set_source(cells[0], """# ModernBERT-Ja 30M：川柳専用8分類・424件

1. 「ランタイム」→「ランタイムのタイプを変更」→GPUを選択。
2. 「ランタイム」→「すべてのセルを実行」。教材と学習コードは同梱済みです。
3. 3シードの分類別結果・誤分類・devの推移を確認します。
4. 最後に `modernbert-senryu424-results.zip` をMacへ保存してからGPUを解放してください。

**train 349 / dev 39 / test 36**。川柳の行指定3種・具体変更3種・終了・対象外の8分類です。
前回の417件を保ち、ユーザー指定の対象外4件と終了3件だけをtrainへ追加しました。

前回と同じ seed42・43・44、学習率5e-5、各10 epoch、batch16、128 token、履歴なし、float32/SDPA、TF32無効。
固定した基盤モデルから新しい8分類headを作り、全パラメータを学習します。各seedのepochはdevで選び、全選択の確定後にtestを診断します。

「画面に川柳が表示されている間に川柳用へ切り替える」が本体接続の方針です。このノートブックは学習と結果保存を行います。本体への接続は別作業です。
dev/testは前回の教材設計で参照済みです。追加7件の結果は学習内の確認で、未知の実機精度とは区別します。
""")
    set_source(cells[1], f"""# 教材と前回の学習コードを展開（別ZIPのアップロードは不要）
from pathlib import Path
import base64, hashlib, io, json, zipfile
ROOT = Path('/content/{NAME}')
RELEASE_PATH = '{RELEASE}'
assert not ROOT.exists(), '前回の出力があります。新しいランタイムで実行してください。'
payload = base64.b64decode('{base64.b64encode(payload).decode()}')
assert hashlib.sha256(payload).hexdigest() == '{digest(payload)}'
ROOT.mkdir()
with zipfile.ZipFile(io.BytesIO(payload)) as archive:
    assert all(not Path(n).is_absolute() and '..' not in Path(n).parts for n in archive.namelist())
    archive.extractall(ROOT)
for name, expected in json.loads((ROOT/'input-sha256.json').read_text()).items():
    assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest() == expected, name
print('INPUT VERIFIED: 424件 / 8分類 / train 349, dev 39, test 36')
del payload
from IPython.display import display, Markdown
display(Markdown((ROOT/RELEASE_PATH/'README.md').read_text()))
""")
    set_source(cells[3], "".join(cells[3]["source"]).replace(OLD_NAME, NAME))
    run = "".join(cells[5]["source"]).replace(OLD_RELEASE, RELEASE).replace(OLD_COMMIT, MATERIAL_COMMIT)
    set_source(cells[5], run.replace("TRAINING AND HELD-OUT EVALUATION COMPLETE", "TRAINING AND KNOWN-DATA DIAGNOSTIC COMPLETE"))
    results = "".join(cells[7]["source"]).split("\n    history = ", 1)[0]
    results = results.replace("733/79/78", "349/39/36").replace(r"'\\n'", r"'\n'")
    results += r'''
    history = [json.loads(line) for line in (ROOT/'reports'/f"{selected['trial']}-dev-history.jsonl").read_text().splitlines()]
    all_labels = list(manifest['label_to_id'])
    history_lines = ['## epochごとのdev：川柳8分類', '',
        '| epoch | dev loss | macro F1 | ' + ' | '.join(all_labels) + ' |',
        '|---:|---:|---:|' + '---:|' * len(all_labels)]
    for item in history:
        values = [f"{item['epoch']:g}", f"{item['loss']:.4f}", f"{item['macro_f1']:.3f}"]
        for label in all_labels:
            row = item['per_class'][label]
            values.append(f"{int(row['correct'])}/{int(row['support'])}")
        history_lines.append('| ' + ' | '.join(values) + ' |')
    history_lines += ['', '各分類は正解数/対象数。選択は8分類全体のdev macro F1、同点ならloss・早いepochの順です。']
    history_text = '\n'.join(history_lines)
    (report_dir/'senryu-dev-history.md').write_text(history_text + '\n')
    display(Markdown(history_text))

    feedback = [json.loads(line) for line in (ROOT/'dev_tools/request_classifier/curation/2026-10-24-live-feedback/candidates.jsonl').read_text().splitlines()]
    predictions = {row['id']: row for row in (json.loads(line) for line in (report_dir/'train-predictions.jsonl').read_text().splitlines())}
    feedback_lines = ['## 今回追加した7件（学習内の確認）', '',
        'この7件はtrainに追加しています。未知の発話に対する評価ではありません。', '',
        '| 本文 | 正解 | 予測 | 正誤 |', '|---|---|---|---|']
    for row in feedback:
        prediction = predictions[row['id']]
        feedback_lines.append(f"| {row['text']} | {row['label']} | {prediction['predicted']} | {'○' if prediction['correct'] else '×'} |")
    feedback_text = '\n'.join(feedback_lines)
    (report_dir/'feedback-train-check.md').write_text(feedback_text + '\n')
    display(Markdown(feedback_text))
'''
    # 前回の表示セルにあった文字列としての「\n」も通常の改行へ揃える。
    results = results.replace(r"'\\n'", r"'\n'")
    set_source(cells[7], results)
    set_source(cells[9], "".join(cells[9]["source"]).replace(OLD_NAME, NAME))
    notebook["metadata"]["colab"]["name"] = NOTEBOOK
    for index, cell in enumerate(cells):
        cell["id"] = f"senryu424-{index:02}"
        if cell["cell_type"] == "code":
            ast.parse("".join(cell["source"]))
    (HERE / NOTEBOOK).write_bytes(encoded_json(notebook))
    (HERE / f"{NAME}-inputs.zip").write_bytes(payload)
    (HERE / "training-protocol.json").write_bytes(inputs["training-protocol.json"])
    (HERE / "source-provenance.json").write_bytes(inputs["source-provenance.json"])
    preparation = {
        "schema": "colab_preparation.v1", "status": "prepared_not_run", "notebook": NOTEBOOK,
        "notebook_sha256": digest((HERE / NOTEBOOK).read_bytes()),
        "input_archive": f"{NAME}-inputs.zip", "input_archive_bytes": len(payload),
        "input_archive_sha256": digest(payload), "input_sha256": hashes,
        "material_commit": MATERIAL_COMMIT, "counts": counts, "labels": labels,
        "training_protocol_sha256": digest(inputs["training-protocol.json"]),
        "source_provenance_sha256": digest(inputs["source-provenance.json"]),
        "builder_sha256": digest(Path(__file__).read_bytes()),
        "training_sources_changed": False, "code_cells_parsed": 5,
        "colab_executed": False, "model_training_run": False, "runtime_changes": False,
    }
    (HERE / "preparation.json").write_bytes(encoded_json(preparation))
    print(json.dumps({key: value for key, value in preparation.items() if key != "input_sha256"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-archive", type=Path, required=True)
    build(parser.parse_args().source_archive)
