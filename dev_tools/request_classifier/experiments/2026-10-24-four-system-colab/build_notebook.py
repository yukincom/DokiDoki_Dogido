"""固定教材、既存の学習部品、共通初期重みからColab配布物を作る。学習は実行しない。"""
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
PREFIX = "dev_tools/request_classifier/"
COMMIT = "3228997ab113ca676f4c0a498716b0ecf43e6fe0"
RELEASE = PREFIX + "releases/2026-10-24-normal-dialogue-four-system"
CURATION = PREFIX + "curation/2026-10-24-four-system-contrast"
OLD = REPO / PREFIX / "experiments/2026-10-24-normal-dialogue-colab"
PILOT = REPO / PREFIX / "experiments/2026-10-24-normal-dialogue-specialist-merge"
NAME = "modernbert-four-system"
NOTEBOOK = "ModernBERT_Ja_30M_4系統x7seed_学習と合成比較_2026-10-24.ipynb"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def read(path):
    return json.loads(path.read_text())


def packed(files, level=9):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=level) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 24, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data, compresslevel=level)
    return buffer.getvalue()


def build():
    inputs = {}
    paths = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", COMMIT,
        "--", RELEASE, CURATION], cwd=REPO).decode().splitlines()
    for name in paths:
        if name.endswith((".zip", ".sha256")):
            continue
        data = subprocess.check_output(["git", "show", f"{COMMIT}:{name}"], cwd=REPO)
        if data != (REPO / name).read_bytes():
            raise ValueError(f"Material changed from the frozen commit: {name}")
        inputs[name] = data
    material_hashes = {n: digest(b) for n, b in inputs.items()}
    plan = json.loads(inputs[RELEASE + "/training-plan.json"])
    labels = json.loads(inputs[RELEASE + "/labels.json"])
    counts = {s: len(inputs[f"{RELEASE}/{s}.jsonl"].splitlines()) for s in ("train", "dev", "test")}
    if counts != {"train": 818, "dev": 46, "test": 50} or len(labels) != 16:
        raise ValueError("Material counts differ")
    previous = read(OLD / "source-provenance.json")
    for name, expected in previous["unchanged_training_sources_sha256"].items():
        data = (OLD / "artifacts/colab-results" / name).read_bytes()
        if digest(data) != expected:
            raise ValueError(f"Archived training source changed: {name}")
        inputs[name] = data
    pilot = read(PILOT / "runs/pilot/protocol.json")
    for name in ("merge_utils.py", "LICENSE.txt"):
        local = PILOT / "reference" / name
        data = local.read_bytes()
        if digest(data) != pilot["source_sha256"][str(local.relative_to(REPO))]:
            raise ValueError(f"Archived TIES reference changed: {name}")
        inputs["reference/" + name] = data
    for name in ("run_experiment.py", "build_report.py"):
        data = (HERE / name).read_bytes()
        ast.parse(data)
        inputs[name] = data
    base_path = REPO / plan["initialization_for_merge"]["path"]
    base_files = {p.name: p.read_bytes() for p in sorted(base_path.iterdir()) if p.is_file()}
    base_hashes = {n: digest(b) for n, b in base_files.items()}
    if base_hashes != pilot["base"]["sha256"] or read(base_path / "config.json")["label2id"] != labels:
        raise ValueError("The common trained checkpoint differs")
    base_zip = packed({"base/" + n: b for n, b in base_files.items()}, level=1)
    (HERE / f"{NAME}-base.zip").write_bytes(base_zip)
    base_record = {"filename": f"{NAME}-base.zip", "bytes": len(base_zip), "sha256": digest(base_zip)}
    del base_files, base_zip
    seeds = plan["seed_plan"]
    experts = plan["experts"]
    recipes = [
        {"id": "arithmetic-quarter", "method": "arithmetic", "scale": .25},
        {"id": "arithmetic-half", "method": "arithmetic", "scale": .5},
        {"id": "arithmetic-one", "method": "arithmetic", "scale": 1.0},
        {"id": "ties20-half", "method": "ties", "keep_percent": 20, "scale": .5},
        {"id": "ties20-one", "method": "ties", "keep_percent": 20, "scale": 1.0},
    ]
    families = list(experts) + [r["id"] for r in recipes]
    candidates = ["base-seed43"] + [f"seed{s}-{f}" for s in seeds for f in families] + [f"mean7-{f}" for f in families]
    if len(candidates) != 73 or len(set(candidates)) != 73:
        raise ValueError("Candidate plan differs")
    protocol = {
        "schema": "four_system_colab.v1", "status": "prepared_not_run", "profile": NAME,
        "material_commit": COMMIT, "release": RELEASE, "additions": CURATION + "/candidates.jsonl",
        "counts": counts, "label_to_id": labels, "experts": experts, "seeds": seeds,
        "base_source": plan["initialization_for_merge"], "base_files_sha256": base_hashes,
        "base_upload": base_record,
        "training": {"jobs": 28, "learning_rate": 1e-5, "epochs": 10, "batch_size": 16,
            "max_length": 128, "context": 0, "dtype": "float32", "attention": "sdpa", "device": "cuda",
            "optimizer": "adamw_torch", "weight_decay": .01, "warmup_steps": 0,
            "max_grad_norm": 1.0, "scheduler": "linear", "tf32": False, "deterministic_algorithms": True,
            "initialization": "All 66 tensors including classifier loaded from the same saved seed43 checkpoint; fresh optimizer for each job",
            "training_stream": plan["training_stream"], "full_finetuning": True,
            "checkpoint_selection": "Development macro F1 descending, loss ascending, earlier epoch",
            "rationale": "Keep the prior specialist pilot learning rate 1e-5; allow 10 epochs for the new 336 rows and choose the epoch by dev, as in the earlier Colab training"},
        "recipes": recipes, "candidates": candidates,
        "merge_scope": "All 66 float32 tensors including classifier; experts ordered spatial, mount, sword, mob",
        "arithmetic_formula": "base + scale * sum(expert - base); scale .25 is the equal mean of four experts, up to floating-point roundoff",
        "ties_formula": "base + scale * TIES(expert - base); concatenate all tensors sorted by name; per-model global top20 percent, mass sign election, dis-mean",
        "ties_reference": pilot["ties_reference"],
        "seven_seed_weight_mean": "For each of the four experts and five merge recipes, equally average its seven checkpoints; separately evaluate all nine resulting models",
        "selection": "Rank all 73 candidates by dev macro F1 descending, loss ascending, declared candidate order; freeze before test. Also select best trained single and best four-expert merge by the same rule. Pick recipe by seven-seed mean dev macro F1, mean dev loss, declared recipe order.",
        "test_policy": "Evaluate all 73 fixed candidates only after dev selection is frozen; no changes based on test",
        "train_diagnostics": "Evaluate unique selected candidates plus baseline on shared 818 rows; identify added 336 rows separately; not heldout accuracy",
        "evaluation_scope": plan["evaluation_scope"], "runtime_adoption": False,
        "runtime_environment": {"python": "3.11.14", "requirements": PREFIX + "requirements-train.txt",
            "gpu_required": True, "initial_free_disk_gib": 20,
            "reproducibility": "Record GPU, CUDA, package versions, seeds, hashes and deterministic mode. Different GPU/driver versions can still produce different floating-point results."},
    }
    provenance = {
        "material_commit": COMMIT, "material_sha256": material_hashes,
        "unchanged_training_sources_sha256": previous["unchanged_training_sources_sha256"],
        "archived_training_source": previous["source_archive_url"],
        "archived_training_archive_sha256": previous["source_archive_sha256"],
        "common_checkpoint_source": str(base_path.relative_to(REPO)), "common_checkpoint_sha256": base_hashes,
        "ties_reference": pilot["ties_reference"],
        "ties_reference_sha256": {n: digest(b) for n, b in inputs.items() if n.startswith("reference/")},
        "new_orchestration_sha256": {n: digest(inputs[n]) for n in ("run_experiment.py", "build_report.py")},
        "reuse": ["EncodedRows", "SaveBestDevelopment", "MeasuredTrainer", "compute_metrics", "evaluate_checkpoint"],
        "new_work": "28-job schedule, compatible all-tensor merges, seven-seed statistics, frozen selection, comparison report and ZIP export",
    }
    inputs["training-protocol.json"] = encoded(protocol)
    inputs["source-provenance.json"] = encoded(provenance)
    hashes = {name: digest(data) for name, data in inputs.items()}
    inputs["input-sha256.json"] = encoded(hashes)
    payload = packed(inputs)
    cells = []

    def cell(kind, source):
        if kind == "code":
            ast.parse(source)
        item = {"cell_type": kind, "id": f"four-system-{len(cells):02}", "metadata": {}, "source": source.splitlines(keepends=True)}
        if kind == "code":
            item.update(execution_count=None, outputs=[])
        cells.append(item)

    cell("markdown", """# ModernBERT-Ja 30M：4系統 × 7seed — 学習・合成・比較

**使い方**
1. 「ランタイム」→「ランタイムのタイプを変更」でGPUを選びます。
2. 「すべてのセルを実行」。アップロード欄が出たら、付属の **modernbert-four-system-base.zip** を選びます。教材はノートブックに内蔵済みです。
3. 28本の学習後、合成・比較・結果ZIP作成まで順番に進みます。
4. 主結果ZIPと、最後の欄にある **seed別7個の重みZIP** をMacへ保存してからランタイムを解放してください。

視線＋ドギド呼出し（spatial）／乗用動物（mount）／剣（sword）／モブ情報（mob）の4系統。各モデルは共通の16分類を持ち、全818件＋系統別の再提示教材で学習します。ラベルや分割はseed間で変えません。

共有 **train 818 / dev 46 / test 50**。学習28本＋4系統合成35本＋7seed重み平均9本＋元モデル1本、**計73候補**を比較します。

全28本は前回のseed43学習済みモデルから開始します。7seedは学習順などの反復で、基盤モデルを7種類作り直す実験ではありません。
学習率1e-5、各10 epoch、batch16、128 token、履歴なし、float32/SDPA。各モデルのepochをdevで選択し、全候補の選択を固定した後にtestを診断します。

dev/testは教材設計で参照済みです。成績は既知データの比較であり、未知の実機精度とは区別します。本体への採用は別作業です。

初回はセットアップ後に空き20 GiBが必要です。所要時間はGPUによって変わります。中断後は同じランタイム内で再実行すると、完了済みを再利用し、未完了の学習1本を初期状態からやり直します。ランタイムを削除すると、保存していない結果は失われます。
""")
    cell("code", f"""# 1. 固定教材・学習処理を展開。同じランタイム内では一致を確かめて再利用します。
from pathlib import Path
import base64, hashlib, io, json, zipfile
from IPython.display import display, Markdown, SVG
ROOT = Path('/content/{NAME}')
payload = base64.b64decode('{base64.b64encode(payload).decode()}')
assert hashlib.sha256(payload).hexdigest() == '{digest(payload)}'
ROOT.mkdir(exist_ok=True)
with zipfile.ZipFile(io.BytesIO(payload)) as archive:
    for name in archive.namelist():
        assert not Path(name).is_absolute() and '..' not in Path(name).parts
        target = ROOT / name
        data = archive.read(name)
        if target.exists():
            assert target.read_bytes() == data, f'既存の入力が異なります: {{name}}'
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
for name, expected in json.loads((ROOT/'input-sha256.json').read_text()).items():
    assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest() == expected, name
PROTOCOL = json.loads((ROOT/'training-protocol.json').read_text())
print('教材確認: train 818 / dev 46 / test 50、16分類、4系統 × seed42〜48')
del payload, data
""")
    cell("markdown", """## 2. 共通初期モデルをアップロード

付属の `modernbert-four-system-base.zip`（約105 MB）を選びます。前回のseed43モデルをそのまま読み込み、分類headを含む初期重みを全28本で共通にします。ファイルが一致する場合、再実行時のアップロードは省略します。
""")
    cell("code", """from google.colab import files
import gc
expected_base = PROTOCOL['base_files_sha256']
base_dir = ROOT / 'base'
def base_is_ready():
    if not base_dir.is_dir():
        return False
    actual = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in base_dir.iterdir() if p.is_file()}
    assert actual == expected_base, '既存の初期モデルが異なります。元のランタイムは保管し、新しいランタイムを使ってください。'
    return True
if not base_is_ready():
    print('modernbert-four-system-base.zip を選んでください。')
    uploaded = files.upload()
    assert len(uploaded) == 1, '付属の初期モデルZIPを1個だけ選んでください。'
    blob = next(iter(uploaded.values()))
    assert hashlib.sha256(blob).hexdigest() == PROTOCOL['base_upload']['sha256'], '初期モデルZIPが指定ファイルと異なります。'
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        assert set(archive.namelist()) == {'base/' + n for n in expected_base}
        for name, expected in expected_base.items():
            assert hashlib.sha256(archive.read('base/' + name)).hexdigest() == expected, name
        archive.extractall(ROOT)
    del uploaded, blob
    gc.collect()
assert base_is_ready()
print('初期モデル確認完了: 前回seed43の同一16分類checkpoint')
""")
    cell("markdown", """## 3. GPU環境を準備

前回と同じPython・学習ライブラリを専用環境に用意します。CPUへの自動切替は行いません。ここでGPUなしと表示された場合は、GPUランタイムへ切り替えて最初から実行してください。
""")
    cell("code", """import subprocess, sys, time
def checked(command, **kwargs):
    return subprocess.run([str(v) for v in command], check=True, **kwargs)
checked(['nvidia-smi'])
checked([sys.executable, '-m', 'pip', 'install', '-q', 'uv'])
PYTHON = ROOT / '.venv/bin/python'
if not PYTHON.exists():
    checked([sys.executable, '-m', 'uv', 'venv', '--python', '3.11.14', ROOT/'.venv'])
assert subprocess.check_output([str(PYTHON), '-c', 'import platform; print(platform.python_version())'], text=True).strip() == '3.11.14'
checked([sys.executable, '-m', 'uv', 'pip', 'install', '--python', PYTHON, '-r', ROOT/'dev_tools/request_classifier/requirements-train.txt'])
checked([PYTHON, '-c', "import torch; assert torch.cuda.is_available(), 'GPU runtime required'; print(torch.__version__, torch.cuda.get_device_name(0))"])
freeze = subprocess.check_output([sys.executable, '-m', 'uv', 'pip', 'freeze', '--python', str(PYTHON)], text=True)
(ROOT/'pip-freeze.txt').write_text(freeze)
(ROOT/'setup.json').write_text(json.dumps({'python': str(PYTHON), 'python_version': '3.11.14', 'at_unix': time.time()}, indent=2))
print('GPU環境の準備が完了しました。')
""")
    cell("markdown", """## 4. 28本を学習 → 合成 → dev選択 → test比較

**固定した合成候補**（各seedの4専門を同じseed同士で合成）

| 方式 | 係数 |
|---|---|
| Task arithmetic：共通初期重み + 係数 × 4専門の差分和 | 0.25 / 0.5 / 1.0 |
| TIES：差分の上位20%を残し、符号を揃えて合成 | 0.5 / 1.0 |

0.25は4専門の等重み平均に相当します。さらに4専門＋5方式それぞれで、7seed分の重みを等重み平均します。**7seedの点数の平均**と、**7seedの重みを平均したモデルの点数**を別々に記録します。

進行中は各学習のepoch・dev成績が表示されます。中断やエラー時も次の保存セルへ進めば、完了した重みと途中記録を保存できます。

手法の出典：[Task arithmetic](https://arxiv.org/abs/2212.04089)、[TIES-Merging](https://arxiv.org/abs/2306.01708)。TIESは同梱した著者実装の4関数を使います。
""")
    cell("code", r"""# 長い処理です。途中で止めた場合、同じランタイム内でこのセルを再実行できます。
log_path = ROOT / 'execution.log'
command = [str(PYTHON), '-u', str(ROOT/'run_experiment.py'), 'all', '--root', str(ROOT)]
process = None
RUN_RETURN_CODE = None
with log_path.open('a') as log:
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in process.stdout:
            print(line, end='')
            log.write(line)
            log.flush()
        RUN_RETURN_CODE = process.wait()
    except KeyboardInterrupt:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        print('\n処理を中断しました。下の保存セルで完了済みの結果を保存できます。')
if RUN_RETURN_CODE == 0:
    print('学習・合成・比較レポートが完成しました。続くセルで結果を表示・保存します。')
else:
    print(f'未完了（終了コード: {RUN_RETURN_CODE}）。ログを確認し、保存セルで途中結果を保存してください。')
""")
    cell("markdown", """## 5. 比較結果

選択モデル、全73候補、各系統の誤分類、7seedの平均と標本標準偏差を表示します。分類別F1・混同行列・全予測・誤分類一覧・各epochのdev推移も結果ZIPに入ります。
""")
    cell("code", """report_path = ROOT / 'run/report.md'
if RUN_RETURN_CODE == 0 and report_path.exists():
    display(Markdown(report_path.read_text().replace('![7seedの比較](seven-seed-f1.svg)', '')))
    display(SVG(filename=str(ROOT/'run/seven-seed-f1.svg')))
else:
    print('比較は未完了です。下のセルで、完了済みモデル・途中記録を保存できます。')
    failure = ROOT / 'run/last-failure.json'
    if failure.exists():
        print(failure.read_text())
""")
    cell("markdown", """## 6. 結果と重みをMacへ保存

- **主結果ZIP**：全73候補の成績・予測・誤分類、教材、出典、実行環境、devで選んだ主要モデルの重み。
- **seed別ZIP × 7**：全28本の学習済み重み。後で別の合成をするために、7個とも保存してください。途中で止めた場合は、そのseed内で完了した1〜3本も保存します。
- 合成後の44本すべての重みを主ZIPに重複収録はしません。選択外の合成重みは、seed別ZIP・元の初期モデルZIP・固定レシピから再構成できます。

主ZIPが自動ダウンロードされます。その後、表示される一覧からseed別ZIPを1個ずつ保存してください。ブラウザにダウンロード許可を求められた場合は許可してください。
**Colabのランタイムを解放する前に、Macへの保存完了を確認してください。** 同じランタイム内の再実行に対応しています。保存ZIPから別ランタイムへ戻す操作は自動化していません。
""")
    cell("code", """# このセルは学習途中のエラー後でも実行できます。
checked([PYTHON, '-u', ROOT/'run_experiment.py', 'package', '--root', ROOT])
downloads = json.loads((ROOT/'exports/downloads.json').read_text())
print('完了した結果です。' if downloads['complete'] else '途中結果です。完了した学習のみを保存しています。')
main_zip = ROOT / downloads['main']['file']
print(f'主ZIP: {main_zip.name} ({main_zip.stat().st_size/1024**2:.1f} MiB)')
files.download(str(main_zip))
import ipywidgets as widgets
seed_records = downloads['all_trained_models']
for item in seed_records:
    print(f"seed{item['seed']}: {len(item['experts'])}/4本、{item['bytes']/1024**2:.1f} MiB、{item['file']}")
options = [('主結果ZIPを再ダウンロード', str(main_zip))] + [
    (f"seed{item['seed']}の重み ({len(item['experts'])}/4本)", str(ROOT/item['file'])) for item in seed_records]
selector = widgets.Dropdown(options=options, description='保存対象:', layout=widgets.Layout(width='600px'))
button = widgets.Button(description='選んだZIPを保存', button_style='primary')
def download_selected(_):
    files.download(selector.value)
button.on_click(download_selected)
display(widgets.VBox([selector, button]))
""")
    notebook = {"nbformat": 4, "nbformat_minor": 5, "metadata": {
        "colab": {"name": NOTEBOOK, "provenance": []}, "accelerator": "GPU",
        "kernelspec": {"display_name": "Python 3", "name": "python3"}, "language_info": {"name": "python"}}, "cells": cells}
    for name, data in ((NOTEBOOK, encoded(notebook)), (f"{NAME}-inputs.zip", payload),
                       ("training-protocol.json", inputs["training-protocol.json"]),
                       ("source-provenance.json", inputs["source-provenance.json"])):
        (HERE / name).write_bytes(data)
    preparation = {"schema": "four_system_colab_preparation.v1", "status": "prepared_not_run",
        "notebook": NOTEBOOK, "notebook_sha256": digest(encoded(notebook)),
        "input_archive": f"{NAME}-inputs.zip", "input_archive_bytes": len(payload),
        "input_archive_sha256": digest(payload), "input_sha256": hashes,
        "base_archive": base_record, "material_commit": COMMIT, "counts": counts,
        "training_jobs": 28, "merged_checkpoints": 44, "evaluated_candidates": len(candidates),
        "code_cells_parsed": sum(c["cell_type"] == "code" for c in cells),
        "builder_sha256": digest(Path(__file__).read_bytes()),
        "colab_executed": False, "model_training_run": False, "runtime_changes": False}
    (HERE / "preparation.json").write_bytes(encoded(preparation))
    print(json.dumps({k: v for k, v in preparation.items() if k != "input_sha256"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    build()
