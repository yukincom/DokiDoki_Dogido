"""生成教材をT4で順番に学習する。共通train/dataset/evaluationの部品を使う。"""
from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys
import zipfile


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def materials(root, mode):
    sys.path.insert(0, str(root / "shared"))
    import dataset
    import evaluation
    import train as shared

    folder = root / "data" / mode
    labels = read(folder / "labels.json")
    # 既存共通部品へ今回のモードのラベルを設定する。モデル入力の整形は既存関数。
    dataset.LABEL_TO_ID.clear()
    dataset.LABEL_TO_ID.update(labels)
    evaluation.LABELS = list(labels)
    shared.LABELS = list(labels)
    splits = {s: [json.loads(line) for line in (folder / f"{s}.jsonl").read_text(encoding="utf-8").splitlines()]
              for s in ("train", "dev", "test")}
    if any(row["context"] for rows in splits.values() for row in rows):
        raise ValueError("現在発話のみの契約と異なる履歴付き教材です。")
    return labels, splits, shared, evaluation


def completed(root, output, mode, config):
    directory = output / mode
    identity = {"input": digest(root / "input-sha256.json"), "training": config, "mode": mode}
    if not (directory / "done.json").is_file():
        raise ValueError("先に「学習する」を実行してください: " + mode)
    if read(directory / "identity.json") != identity:
        raise ValueError("別の教材・学習設定のモデルです: " + mode)
    done = read(directory / "done.json")
    for name, expected in done["model_sha256"].items():
        if digest(directory / "best" / name) != expected:
            raise ValueError("保存済みのモデルが変更されています: " + mode)
    return done


def train_profile(root, output, mode, config, device="cuda"):
    """内部の学習部品。公開Notebookの入口runはT4以外を受け付けない。"""
    import torch
    from transformers import (AutoModelForSequenceClassification, AutoTokenizer,
                              DataCollatorWithPadding, Trainer, TrainingArguments, set_seed)
    from transformers.trainer_utils import get_last_checkpoint

    labels, splits, shared, _ = materials(root, mode)
    directory = output / mode
    directory.mkdir(parents=True, exist_ok=True)
    identity = {"input": digest(root / "input-sha256.json"), "training": config, "mode": mode}
    identity_path = directory / "identity.json"
    if identity_path.exists() and read(identity_path) != identity:
        raise ValueError("別の教材・学習設定が保存されています。別の出力先を指定してください。")
    save(identity_path, identity)
    if (directory / "done.json").is_file():
        done = completed(root, output, mode, config)
        print("完了済みの学習を保持します:", mode, flush=True)
        return done
    set_seed(config["seed"])
    options = {"revision": config["revision"], "trust_remote_code": False}
    tokenizer = AutoTokenizer.from_pretrained(config["model"], **options)
    encoded = {s: shared.EncodedRows(splits[s], tokenizer, config["max_length"]) for s in ("train", "dev")}
    model = AutoModelForSequenceClassification.from_pretrained(
        config["model"], **options, num_labels=len(labels), label2id=labels,
        id2label={i: name for name, i in labels.items()}, dtype=torch.float32,
        attn_implementation="sdpa", use_safetensors=True)
    callback = shared.SaveBestDevelopment(directory, 0, config["learning_rate"], tokenizer)
    if (directory / "best_development.json").is_file():
        callback.best = read(directory / "best_development.json")
    arguments = TrainingArguments(
        output_dir=str(directory / "checkpoints"), learning_rate=config["learning_rate"],
        num_train_epochs=config["epochs"], per_device_train_batch_size=config["batch_size"],
        per_device_eval_batch_size=config["batch_size"], weight_decay=.01,
        warmup_steps=0, max_grad_norm=1.0, optim="adamw_torch", lr_scheduler_type="linear",
        eval_strategy="epoch", save_strategy="epoch", save_total_limit=1,
        logging_strategy="epoch", report_to="none", seed=config["seed"], data_seed=config["seed"],
        dataloader_num_workers=0, dataloader_pin_memory=False, use_cpu=device == "cpu",
        fp16=False, bf16=False, tf32=False, torch_compile=False)
    trainer = Trainer(model=model, args=arguments, train_dataset=encoded["train"],
                      eval_dataset=encoded["dev"], processing_class=tokenizer,
                      data_collator=DataCollatorWithPadding(tokenizer=tokenizer),
                      compute_metrics=shared.compute_metrics, callbacks=[callback])
    if not str(next(trainer.model.parameters()).device).startswith(device):
        raise ValueError("要求した学習デバイスと異なります。")
    checkpoint_root = directory / "checkpoints"
    checkpoint = get_last_checkpoint(str(checkpoint_root)) if checkpoint_root.exists() else None
    print(f"学習 {mode}: {len(labels)}分類 / train {len(splits['train'])}件 / 再開 {checkpoint or 'なし'}", flush=True)
    trainer.train(resume_from_checkpoint=checkpoint)
    if callback.best is None:
        raise RuntimeError("devで選択したモデルを保存できませんでした。")
    save(directory / "trainer-history.json", trainer.state.log_history)
    del trainer, model
    gc.collect()
    if device == "cuda":
        torch.cuda.empty_cache()
    done = {"mode": mode, "seed": config["seed"], "best_development": callback.best,
            "model_sha256": {p.name: digest(p) for p in (directory / "best").iterdir() if p.is_file()},
            "runtime_adoption": False, "test_evaluated": False}
    save(directory / "done.json", done)
    return done


def evaluate_profile(root, output, mode, config, device="cuda"):
    """保存済み最良モデルを読むだけ。train()とモデル保存は呼ばない。"""
    import torch
    from transformers import (AutoModelForSequenceClassification, AutoTokenizer,
                              DataCollatorWithPadding, Trainer, TrainingArguments)
    done = completed(root, output, mode, config)
    labels, splits, shared, evaluation = materials(root, mode)
    directory = output / mode
    tokenizer = AutoTokenizer.from_pretrained(directory / "best", local_files_only=True, trust_remote_code=False)
    encoded = {s: shared.EncodedRows(rows, tokenizer, config["max_length"]) for s, rows in splits.items()}
    best = AutoModelForSequenceClassification.from_pretrained(
        directory / "best", local_files_only=True, trust_remote_code=False,
        use_safetensors=True, dtype=torch.float32, attn_implementation="sdpa")
    evaluator = Trainer(model=best, args=TrainingArguments(
        output_dir=str(directory / "evaluation"), use_cpu=device == "cpu",
        per_device_eval_batch_size=config["batch_size"], report_to="none",
        dataloader_pin_memory=False), processing_class=tokenizer,
        data_collator=DataCollatorWithPadding(tokenizer=tokenizer))
    metrics = {}
    mistakes = ["# 誤分類一覧", "", "devは選択用、testは既知の診断用、trainは学習内確認です。", ""]
    for split in ("test", "dev", "train"):
        prediction = evaluator.predict(encoded[split])
        predicted_rows = shared.prediction_rows(splits[split], prediction)
        metrics[split] = evaluation.classification_metrics(
            [row["gold"] for row in predicted_rows], [row["predicted"] for row in predicted_rows], list(labels))
        metrics[split]["loss"] = float(prediction.metrics["test_loss"])
        metrics[split]["labels_without_examples"] = [name for name in labels if not metrics[split]["per_class"][name]["support"]]
        (directory / f"{split}-predictions.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in predicted_rows), encoding="utf-8")
        mistakes += [f"## {split}", ""]
        for row in predicted_rows:
            if not row["correct"]:
                mistakes += [f"- {row['id']}: {row['text']}", f"  正解 {row['gold']} / 予測 {row['predicted']}"]
    save(directory / "metrics.json", metrics)
    (directory / "誤分類.md").write_text("\n".join(mistakes) + "\n", encoding="utf-8")
    result = {**done, "metrics": metrics, "test_evaluated": True,
              "evaluation_scope": "testは学習・epoch選択から独立。既存testは既知の診断集合。trainは学習内確認。"}
    save(directory / "evaluation.json", result)
    del evaluator, best
    gc.collect()
    if device == "cuda":
        torch.cuda.empty_cache()
    return result


def run(root, output, evaluate_only=False):
    import torch
    if not torch.cuda.is_available() or "T4" not in torch.cuda.get_device_name(0):
        raise RuntimeError("このNotebookはT4用です。ランタイムのタイプをT4 GPUへ変更してください。")
    for name, expected in read(root / "input-sha256.json").items():
        if digest(root / name) != expected:
            raise ValueError("教材が変更されています: " + name)
    protocol = read(root / "training-protocol.json")
    output.mkdir(parents=True, exist_ok=True)
    save(output / "training-protocol.json", protocol)
    save(output / "environment.json", {"python": sys.version, "gpu": torch.cuda.get_device_name(0),
        "cuda": torch.version.cuda, "versions": {name: importlib.metadata.version(name) for name in
        ("torch", "transformers", "accelerate", "numpy", "safetensors")}})
    summary = {}
    for mode in protocol["profiles"]:
        action = evaluate_profile if evaluate_only else train_profile
        summary[mode] = action(root, output, mode, protocol["training"])
    save(output / ("evaluation-summary.json" if evaluate_only else "summary.json"), summary)
    if evaluate_only:
        for mode, result in summary.items():
            metrics = result["metrics"]["test"]
            print(f"テスト {mode}: {metrics['correct']}/{metrics['n']}件 正解率 {metrics['accuracy']:.1%} macro F1 {metrics['macro_f1']:.3f}", flush=True)
    # 途中再開用のoptimizer状態はDriveへ残し、配布するZIPは最良モデルと報告だけ。
    destination = output / "dogido-training-results.zip"
    temporary = output / "dogido-training-results.partial.zip"
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for path in sorted(output.rglob("*")):
            relative = path.relative_to(output)
            if path.is_file() and not any(part in ("checkpoints", "evaluation") for part in relative.parts) and path.suffix != ".zip":
                archive.write(path, str(relative))
        archive.write(root / "input-sha256.json", "input-sha256.json")
        for path in sorted((root / "sources").glob("*.csv")):
            archive.write(path, str(path.relative_to(root)))
    temporary.replace(destination)
    print("完了しました:", destination, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--evaluate-only", action="store_true", help="学習せず保存済みモデルだけをテストする")
    args = parser.parse_args()
    run(args.root.resolve(), args.output.resolve(), args.evaluate_only)
