"""前回のColabノートブックと学習コードを再利用し、通常対話578件を同梱する。"""
from __future__ import annotations

import ast
import base64
import hashlib
import io
import json
from pathlib import Path
import subprocess
import zipfile


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
CLASSIFIER = "dev_tools/request_classifier/"
MATERIAL_COMMIT = "f8e1d0296068dfcd9ab583cb5604b9e0cfe06701"
TEMPLATE_COMMIT = "7c37a7d92342ff15da7fefbe5269514b75453c5c"
TEMPLATE = CLASSIFIER + "experiments/2026-10-24-senryu-colab/ModernBERT_Ja_30M_川柳8分類424件_2026-10-24.ipynb"
RELEASE = CLASSIFIER + "releases/2026-10-24-normal-dialogue-578"
FEEDBACK = CLASSIFIER + "curation/2026-10-24-normal-dialogue-feedback-1548"
CONTRAST = CLASSIFIER + "curation/2026-10-24-normal-dialogue-tool-contrast"
NAME = "modernbert-normal578"
NOTEBOOK = "ModernBERT_Ja_30M_通常対話16分類578件_2026-10-24.ipynb"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded_json(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def git_file(commit: str, path: str) -> bytes:
    return subprocess.check_output(["git", "show", f"{commit}:{path}"], cwd=REPO)


def set_source(cell: dict, source: str) -> None:
    cell["source"] = source.splitlines(keepends=True)
    if cell["cell_type"] == "code":
        ast.parse(source)
        cell.update(execution_count=None, outputs=[])


def build() -> None:
    template_bytes = git_file(TEMPLATE_COMMIT, TEMPLATE)
    notebook = json.loads(template_bytes)
    cells = notebook["cells"]
    if len(cells) != 10:
        raise ValueError("Unexpected previous notebook structure")
    assignments = ast.parse("".join(cells[1]["source"])).body
    payload_calls = [node.value for node in assignments if isinstance(node, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == "payload" for t in node.targets)]
    if len(payload_calls) != 1:
        raise ValueError("Expected one embedded input payload")
    template_payload = base64.b64decode(ast.literal_eval(payload_calls[0].args[0]), validate=True)
    preparation_path = str(Path(TEMPLATE).with_name("preparation.json"))
    template_preparation = json.loads(git_file(TEMPLATE_COMMIT, preparation_path))
    if digest(template_bytes) != template_preparation["notebook_sha256"] or digest(template_payload) != template_preparation["input_archive_sha256"]:
        raise ValueError("Previous notebook or input payload differs from its preparation record")
    inputs = {}
    with zipfile.ZipFile(io.BytesIO(template_payload)) as archive:
        provenance = json.loads(archive.read("source-provenance.json"))
        protocol = json.loads(archive.read("training-protocol.json"))
        for path, expected in provenance["unchanged_training_sources_sha256"].items():
            data = archive.read(path)
            if digest(data) != expected:
                raise ValueError(f"Training source hash differs: {path}")
            inputs[path] = data
    if len(inputs) != 5:
        raise ValueError("Expected the same five training source files")
    old_release, old_commit, old_name = protocol["release"], protocol["material_commit"], protocol["profile"]
    material_dirs = [RELEASE, CLASSIFIER + "releases/2026-10-24-normal-dialogue-546",
        CLASSIFIER + "releases/2026-10-24-normal-dialogue-541", FEEDBACK, CONTRAST,
        CLASSIFIER + "curation/2026-10-24-normal-dialogue-feedback",
        CLASSIFIER + "curation/2026-10-24-live-feedback"]
    paths = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", MATERIAL_COMMIT, "--", *material_dirs], cwd=REPO,
    ).decode().splitlines()
    if len(paths) != 50:
        raise ValueError("Expected 50 frozen material and provenance files")
    for path in paths:
        data = git_file(MATERIAL_COMMIT, path)
        if data != (REPO / path).read_bytes():
            raise ValueError(f"Working material differs from the fixed commit: {path}")
        inputs[path] = data
    labels = json.loads(inputs[RELEASE + "/labels.json"])
    splits = {split: [json.loads(line) for line in inputs[f"{RELEASE}/{split}.jsonl"].splitlines()]
              for split in ("train", "dev", "test")}
    counts = {split: len(rows) for split, rows in splits.items()}
    if counts != {"train": 482, "dev": 46, "test": 50} or len(labels) != 16:
        raise ValueError("Unexpected material counts or labels")
    freeze = json.loads(inputs[RELEASE + "/freeze.json"])
    for name, expected in freeze["output_sha256"].items():
        if digest(inputs[RELEASE + "/" + name]) != expected:
            raise ValueError(f"Frozen material hash differs: {name}")
    protocol.update(profile=NAME, material_commit=MATERIAL_COMMIT, release=RELEASE,
        counts=counts, label_to_id=labels,
        release_sha256={p.removeprefix(RELEASE + "/"): digest(b) for p, b in inputs.items()
                        if p.startswith(RELEASE + "/")},
        evaluation_scope=freeze["evaluation_scope"], runtime_routing=freeze["routing_contract"],
        status="prepared_not_run")
    protocol["training"]["initialization"] = "base pretrained model with a new 16-label classification head"
    inputs["training-protocol.json"] = encoded_json(protocol)
    provenance.update(template_notebook=TEMPLATE, template_notebook_commit=TEMPLATE_COMMIT,
        template_notebook_sha256=digest(template_bytes),
        template_input_archive_sha256=digest(template_payload), material_commit=MATERIAL_COMMIT,
        notebook_changes=["通常対話16分類578件の固定データ", "通常16分類のdev推移",
            "実機15件と合成32件を分けた学習内確認", "通常対話用の入出力名"])
    inputs["source-provenance.json"] = encoded_json(provenance)
    hashes = {path: digest(data) for path, data in inputs.items()}
    payload_buffer = io.BytesIO()
    with zipfile.ZipFile(payload_buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, data in sorted({**inputs, "input-sha256.json": encoded_json(hashes)}.items()):
            info = zipfile.ZipInfo(path, date_time=(2026, 10, 24, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    payload = payload_buffer.getvalue()
    set_source(cells[0], """# ModernBERT-Ja 30M：通常対話用16分類・578件

1. 「ランタイム」→「ランタイムのタイプを変更」→GPUを選択。
2. 「ランタイム」→「すべてのセルを実行」。教材と学習コードは同梱済みです。
3. 3シードの分類別結果・誤分類・devの推移を確認します。
4. 最後に `modernbert-normal578-results.zip` をMacへ保存してからGPUを解放してください。

**train 482 / dev 46 / test 50**。モブ情報・剣・乗用動物・ドギド呼び出し・視線11種・対象外の16分類です。
前版546件を保ち、今回採用した道具・雑談の合成例32件（剣依頼8、対象外24）をtrainへ追加しました。
既存の実機フィードバック15件もtrainに含みます。dev/testと分類IDは前版から変更していません。

前回と同じ seed42・43・44、学習率5e-5、各10 epoch、batch16、128 token、履歴なし、float32/SDPA、TF32無効。
固定した基盤モデルから新しい16分類headを作り、全パラメータを学習します。各seedのepochはdevで選び、全選択の確定後にtestを診断します。

このノートブックは通常対話用の学習と結果保存を行います。川柳用との切替・本体への接続は別作業です。
dev/testは前回の教材設計で参照済みです。実機15件・合成32件の結果は学習内の確認で、未知の実機精度とは区別します。
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
print('INPUT VERIFIED: 578件 / 16分類 / train 482, dev 46, test 50')
del payload
from IPython.display import display, Markdown
display(Markdown((ROOT/RELEASE_PATH/'README.md').read_text()))
""")
    set_source(cells[3], "".join(cells[3]["source"]).replace(old_name, NAME))
    set_source(cells[5], "".join(cells[5]["source"]).replace(old_release, RELEASE).replace(old_commit, MATERIAL_COMMIT))
    results = "".join(cells[7]["source"]).split("\n    feedback = ", 1)[0]
    results = results.replace("349/39/36", "482/46/50").replace("川柳8分類", "通常対話16分類")
    results = results.replace("選択は8分類全体", "選択は16分類全体").replace("senryu-dev-history.md", "normal-dev-history.md")
    results += r'''
    predictions = {row['id']: row for row in (json.loads(line) for line in (report_dir/'train-predictions.jsonl').read_text().splitlines())}
    batches = [
        ('dev_tools/request_classifier/curation/2026-10-24-normal-dialogue-feedback-1548/candidates.jsonl', 15,
         '実機フィードバック15件', 'feedback-train-check.md'),
        ('dev_tools/request_classifier/curation/2026-10-24-normal-dialogue-tool-contrast/candidates.jsonl', 32,
         '今回追加した道具・雑談の合成例32件', 'contrast-train-check.md'),
    ]
    for source, expected_count, title, filename in batches:
        feedback = [json.loads(line) for line in (ROOT/source).read_text().splitlines()]
        assert len(feedback) == expected_count
        feedback_lines = [f'## {title}（学習内の確認）', '',
            '全件trainに含みます。未知の発話に対する評価ではありません。', '',
            '| 本文 | 正解 | 予測 | 正誤 |', '|---|---|---|---|']
        correct = 0
        for row in feedback:
            prediction = predictions[row['id']]
            assert prediction['gold'] == row['label'] and prediction['text'] == row['text']
            correct += int(prediction['correct'])
            text = row['text'].replace('|', r'\|').replace('\n', '<br>')
            feedback_lines.append(f"| {text} | {row['label']} | {prediction['predicted']} | {'○' if prediction['correct'] else '×'} |")
        feedback_lines += ['', f'学習内の正解：{correct}/{len(feedback)}']
        feedback_text = '\n'.join(feedback_lines)
        (report_dir/filename).write_text(feedback_text + '\n')
        display(Markdown(feedback_text))
'''
    set_source(cells[7], results)
    set_source(cells[9], "".join(cells[9]["source"]).replace(old_name, NAME))
    notebook["metadata"]["colab"]["name"] = NOTEBOOK
    for index, cell in enumerate(cells):
        cell["id"] = f"normal578-{index:02}"
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
    build()
