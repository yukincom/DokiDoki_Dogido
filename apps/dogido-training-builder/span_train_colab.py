"""語句抽出のColab学習・保存済みモデルの評価・単発推論を接続する。

学習と最良重みの選択は既存の文字BERTを使う。test/regressionは選択後だけ読む。
中断した試行は保持し、新試行で最初から学習する。個人の実例はコードへ埋め込まない。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import zipfile

MODEL = "tohoku-nlp/bert-base-japanese-char-v3"
REVISION = "ccddee593a9e03a589ae1feedcd4d1eb06d6ba02"
INPUT_FORMAT = "current_verse_original_chars_v1"
SPLITS = ("train", "dev", "test", "regression")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def inside(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError("保存先が不正です: " + str(name))
    return path


def bundle(root):
    root = Path(root).resolve()
    checksums = read(root / "input-sha256.json")
    required = {"training-protocol.json", "data/dataset-status.json", "data/train.jsonl", "data/dev.jsonl", "data/test.jsonl",
                "shared/train.py", "shared/span_model.py", "shared/early_stopping.py", "shared/download_base.py"}
    if not required <= set(checksums):
        raise ValueError("教材ZIPに必要なファイルの照合情報がありません。ZIPを作り直してください。")
    for name, expected in checksums.items():
        if digest(inside(root, name)) != expected:
            raise ValueError("教材が変更されています: " + name)
    protocol = read(root / "training-protocol.json")
    if protocol.get("schema") != "edit_span_csv_colab.v1" or protocol.get("project", {}).get("input_format") != INPUT_FORMAT:
        raise ValueError("語句抽出用の教材ZIPを選んでください。")
    config = protocol["training"]
    if config.get("model") != MODEL or config.get("revision") != REVISION:
        raise ValueError("公開ベースモデルの固定版が一致しません。")
    for name in ("epochs", "batch_size", "early_stopping_patience"):
        if type(config.get(name)) is not int or config[name] <= 0:
            raise ValueError("学習設定を確認してください: " + name)
    if not 0 < config["learning_rate"] < 1 or type(config.get("seed")) is not int:
        raise ValueError("学習率・seedを確認してください。")
    if read(root / "data/dataset-status.json").get("training_allowed") is not True:
        raise ValueError("この教材は学習対象に設定されていません。")
    for split in SPLITS:
        path = root / "data" / (split + ".jsonl")
        if path.is_file() and "data/" + path.name not in checksums:
            raise ValueError("照合情報のない教材があります: " + path.name)
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.is_file() else []
        expected = protocol["profiles"]["span"]["counts"].get(split, 0)
        if len(rows) != expected or (split != "regression" and not rows):
            raise ValueError("教材の件数を確認してください: " + split)
    identity = {"protocol_sha256": digest(root / "training-protocol.json"),
                "input_sha256": checksums, "input_format": INPUT_FORMAT}
    return protocol, identity


def bind(output, identity, create=False):
    output = Path(output)
    marker = output / "bundle-identity.json"
    if marker.exists():
        if read(marker) != identity:
            raise ValueError("別の教材・設定の出力先です。同じNotebookと教材ZIPを使ってください。")
    elif create:
        if output.exists() and any(output.iterdir()):
            raise ValueError("用途を確認できない既存フォルダです。新しい出力先を指定してください。")
        output.mkdir(parents=True, exist_ok=True)
        save(marker, identity)
    else:
        raise ValueError("まだ学習が完了していません。「学習する」を押してください。")


def model_hashes(model):
    return {p.name: digest(p) for p in sorted(model.iterdir()) if p.is_file()}


def completed(root, output):
    protocol, identity = bundle(root)
    output = Path(output).resolve()
    bind(output, identity)
    active_path = output / "active-run.json"
    if not active_path.is_file():
        raise ValueError("まだ学習が完了していません。中断後は「学習する」で新しい試行を開始してください。")
    active = read(active_path)
    attempt = inside(output, active["attempt"])
    frozen_path = attempt / "frozen-selection.json"
    if digest(frozen_path) != active["frozen_sha256"]:
        raise ValueError("モデル選択記録が変更されています。")
    frozen = read(frozen_path)
    if frozen["bundle_identity"] != identity or frozen["model_sha256"] != model_hashes(attempt / "run/model"):
        raise ValueError("保存モデルまたは教材が選択時と異なります。")
    for name, expected in frozen["training_record_sha256"].items():
        if digest(attempt / "run" / name) != expected:
            raise ValueError("学習・選択の記録が変更されています: " + name)
    return protocol, attempt, frozen


def new_directory(parent, prefix):
    parent.mkdir(parents=True, exist_ok=True)
    for number in range(1, 100000):
        path = parent / f"{prefix}-{number:04d}"
        try:
            path.mkdir()
            return path
        except FileExistsError:
            continue
    raise ValueError("保存先の試行数が多すぎます。")


def run_command(command, log_path):
    with log_path.open("w", encoding="utf-8") as log:
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding="utf-8", bufsize=1) as process:
            for line in process.stdout:
                log.write(line)
                log.flush()
                print(line, end="", flush=True)
            if process.wait():
                raise RuntimeError("処理が中断しました。ログを保持しました: " + str(log_path))


def train_bundle(root, output, *, device="cuda", _test_base=None):
    """CPUとテストbaseはローカル検証専用。Colab CLIは必ずCUDAを検査する。"""
    root, output = Path(root).resolve(), Path(output).resolve()
    protocol, identity = bundle(root)
    bind(output, identity, create=True)
    if (output / "active-run.json").is_file():
        _, attempt, _ = completed(root, output)
        print("完了済みの学習を保持します:", attempt, flush=True)
        return attempt
    config = protocol["training"]
    if _test_base is not None and device != "cpu":
        raise ValueError("検証用baseはCPU接続試験専用です。")
    base = Path(_test_base) if _test_base is not None else root / "public-base"
    if _test_base is None:
        run_command([sys.executable, str(root / "shared/download_base.py"), "--output", str(base)], output / "base-download.log")
    attempt = new_directory(output / "attempts", "attempt")
    save(attempt / "started.json", {"started_utc": datetime.now(timezone.utc).isoformat(), "device": device,
                                  "local_tiny_model_test": _test_base is not None, "bundle_identity": identity})
    command = [sys.executable, "-u", str(root / "shared/train.py"), "--base", str(base), "--data", str(root / "data"),
               "--output", str(attempt / "run"), "--device", device, "--epochs", str(config["epochs"]),
               "--batch-size", str(config["batch_size"]), "--learning-rate", str(config["learning_rate"]),
               "--seed", str(config["seed"]), "--early-stopping-patience", str(config["early_stopping_patience"]), "--defer-evaluation"]
    run_command(command, attempt / "training-console.log")
    done = read(attempt / "run/training-complete.json")
    selection = read(attempt / "run/selection.json")
    history = read(attempt / "run/training-history.json")
    scores = [(r["dev_metrics"]["all"]["exact"], r["dev_metrics"]["all"]["span_f1"] or 0.0) for r in history]
    if not scores or selection["epoch"] != scores.index(max(scores)) + 1:
        raise ValueError("devの最良epochと保存モデルの選択が一致しません。")
    contract = {"format": INPUT_FORMAT, "labels": ["O", "B-from", "I-from", "B-to", "I-to"],
                "offsets": "Unicode codepoint; start inclusive, end exclusive; original utterance",
                "encoder": "shared/span_model.py:CharacterCodec", "decoder": "shared/span_model.py:decode_spans",
                "context": "current_verse; original newlines encoded as SEP; not predicted",
                "max_positions": 512, "runtime_adoption": False}
    save(attempt / "run/model/input-format.json", contract)
    frozen = {"bundle_identity": identity, "selection": selection, "training_record_sha256": {name: digest(attempt / "run" / name) for name in
                  ("selection.json", "training-history.json", "training-complete.json", "manifest.json")},
              "training_complete": done, "model_sha256": model_hashes(attempt / "run/model"),
              "scope": "devだけで選択した重み。test/regressionはこの固定後に評価する。",
              "local_tiny_model_test": _test_base is not None}
    save(attempt / "frozen-selection.json", frozen)
    save(output / "active-run.json", {"attempt": str(attempt.relative_to(output)),
                                     "frozen_sha256": digest(attempt / "frozen-selection.json")})
    print(f"学習完了: {done['epochs_completed']}周、保存epoch {selection['epoch']}。評価・テストは別ボタンです。", flush=True)
    return attempt


def span_module(root):
    spec = importlib.util.spec_from_file_location("colab_span_model", Path(root) / "shared/span_model.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def summary_metrics(rows, shared):
    result = shared.metrics(rows)
    result["by_role"] = {}
    for role in ("from", "to"):
        selected = [{**r, "spans": [s for s in r["spans"] if s["role"] == role],
                     "predicted": [s for s in r["predicted"] if s["role"] == role]} for r in rows]
        for row in selected:
            row["exact"] = shared.span_key(row["spans"]) == shared.span_key(row["predicted"])
        result["by_role"][role] = shared.metrics(selected)["all"]
    result["by_form"] = {form: shared.metrics([r for r in rows if r.get("form", "unspecified") == form])["all"]
                         for form in sorted({r.get("form", "unspecified") for r in rows})}
    return result


def load_model(root, attempt, device):
    from transformers import BertForTokenClassification
    shared = span_module(root)
    model = BertForTokenClassification.from_pretrained(attempt / "run/model", local_files_only=True,
                                                       attn_implementation="sdpa").to(device)
    return shared, model, shared.CharacterCodec(attempt / "run/model/vocab.txt")


def show_spans(spans):
    return " / ".join(f"{s['role']}={s['text']!r} [{s['start']}:{s['end']}]" for s in spans) or "なし"


def package(root, attempt, evaluation):
    destination = evaluation / "edit-span-training-results.zip"
    with zipfile.ZipFile(destination, "x", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for path in sorted((attempt / "run/model").iterdir()):
            if path.is_file():
                archive.write(path, "model/" + path.name)
        for name in ("selection.json", "training-history.json", "training-complete.json", "manifest.json"):
            archive.write(attempt / "run" / name, "training/" + name)
        archive.write(attempt / "training-console.log", "training/training-console.log")
        archive.write(attempt / "frozen-selection.json", "frozen-selection.json")
        for path in sorted(evaluation.iterdir()):
            if path.is_file() and path.suffix != ".zip":
                archive.write(path, "evaluation/" + path.name)
        # ZIPに列挙・照合された教材だけを保存する。出力ディレクトリ全体は走査しない。
        for name in read(root / "input-sha256.json"):
            archive.write(inside(root, name), "materials/" + name)
        archive.write(root / "input-sha256.json", "materials/input-sha256.json")
        archive.writestr("README.txt", "保存モデルと語句抽出評価結果\n\nmodel/ の文字vocabと materials/shared/span_model.py の CharacterCodec / predict_rows を組で使います。\n一般のMeCab tokenizerやpipelineは使いません。入力と出力の契約は model/input-format.json。\ntraining/ は学習各周の成績、evaluation/ は保存した最良モデルだけによる評価です。\ntrainは学習内、devはモデル選択用、testは選択後、regressionは既知例の確認です。\n本体への採用・音声認識・Minecraft上の動作は未検証です。\n")
    return destination


def evaluate_bundle(root, output, *, device="cuda"):
    root, output = Path(root).resolve(), Path(output).resolve()
    protocol, attempt, frozen = completed(root, output)
    shared, model, codec = load_model(root, attempt, device)
    evaluation = new_directory(attempt / "evaluations", "evaluation")
    results = {}
    lines = ["語句抽出の評価・テスト結果", "", f"選択epoch: {frozen['selection']['epoch']}",
             "trainは学習内確認、devはモデル選択用、testは学習・選択の対象外。regressionは既知例の確認。", ""]
    for split in ("test", "regression", "dev", "train"):
        path = root / "data" / (split + ".jsonl")
        if not path.is_file():
            continue
        rows = shared.predict_rows(model, codec, shared.read_jsonl(path), device, protocol["training"]["batch_size"])
        results[split] = summary_metrics(rows, shared)
        (evaluation / (split + "-predictions.jsonl")).write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        m = results[split]["all"]
        lines += [f"{split}: 完全一致 {m['exact']}/{m['count']}、span F1={m['span_f1']}", ""]
        wrong = [r for r in rows if not r["exact"]]
        lines.append(f"{split}の全失敗: {len(wrong)}件")
        for row in wrong:
            lines += [str(row.get("id", "")), "現在句: " + row.get("current_verse", ""), "発話: " + row["text"],
                      "正解: " + show_spans(row["spans"]), "予測: " + show_spans(row["predicted"]), ""]
    del model
    # 再評価でも重み・選択・元の教材は変更しない。
    completed(root, output)
    summary = {"schema": "edit_span_evaluation.v1", "attempt": str(attempt.relative_to(output)),
               "evaluation": str(evaluation.relative_to(output)), "metrics": results,
               "selected_epoch": frozen["selection"]["epoch"], "training_complete": frozen["training_complete"],
               "model_sha256": frozen["model_sha256"], "runtime_adoption": False,
               "scope": "保存テキストの原文範囲抽出。実ASR・Minecraft・変更実行・新しい未知対話の品質は別確認。"}
    save(evaluation / "metrics.json", summary)
    (evaluation / "REPORT.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    archive = package(root, attempt, evaluation)
    summary["archive"] = str(archive.relative_to(output))
    summary["archive_sha256"] = digest(archive)
    save(output / "evaluation-summary.json", summary)
    for split, result in results.items():
        m = result["all"]
        print(f"{split}: 完全一致 {m['exact']}/{m['count']} / span F1={m['span_f1']}", flush=True)
    print("評価完了:", archive, flush=True)
    return summary


def predict_one(root, output, text, current_verse="", *, device="cuda"):
    if not isinstance(text, str) or not text.strip() or not isinstance(current_verse, str):
        raise ValueError("発話原文を入力してください。現在の句は空欄でも構いません。")
    root, output = Path(root).resolve(), Path(output).resolve()
    _, attempt, _ = completed(root, output)
    shared, model, codec = load_model(root, attempt, device)
    result = shared.predict_rows(model, codec, [{"text": text, "current_verse": current_verse}], device, 1)[0]
    del model
    return result


def require_colab_gpu():
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError("GPUに接続してください。ColabのランタイムをT4 GPUへ変更します。CPUへは切り替えません。")
    print("使用GPU:", torch.cuda.get_device_name(0), file=sys.stderr, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--evaluate-only", action="store_true")
    action.add_argument("--predict-stdin", action="store_true")
    args = parser.parse_args()
    require_colab_gpu()
    if args.predict_stdin:
        request = json.load(sys.stdin)
        print(json.dumps(predict_one(args.root, args.output, request["text"], request.get("current_verse", "")), ensure_ascii=False))
    elif args.evaluate_only:
        evaluate_bundle(args.root, args.output)
    else:
        train_bundle(args.root, args.output)


if __name__ == "__main__":
    main()
