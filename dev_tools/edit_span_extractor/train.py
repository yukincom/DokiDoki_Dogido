"""文字BERTの抽出ヘッドとencoderを追加学習し、devだけでcheckpointを選ぶ。

見込み: 句の語句と依頼表現を組み合わせた境界教材で、未使用の句も抽出できる。
合格目安: 分離した合成testの範囲完全一致95%以上。実対話は別集計し全失敗を表示。
範囲: 保存済みテキストによる実モデル試験。ASR再実行・本体編集・実機は対象外。
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import time

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

import torch
from transformers import BertForTokenClassification
import transformers

from span_model import CharacterCodec, LABELS, LABEL_TO_ID, collate, metrics, predict_rows, read_jsonl, write_json
from early_stopping import EarlyStopping


def synchronize(device):
    if device == "mps":
        torch.mps.synchronize()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=["cpu", "mps"], default="mps")
    parser.add_argument("--epochs", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=24)
    parser.add_argument("--learning-rate", type=float, default=3e-5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--defer-evaluation", action="store_true", help="Save dev-selected model; leave all final sets untouched")
    parser.add_argument("--early-stopping-patience", type=int, default=0,
                        help="Stop after this many epochs without dev improvement; 0 disables")
    args = parser.parse_args()
    progress = EarlyStopping(args.early_stopping_patience)
    status_path = args.data / "dataset-status.json"
    if status_path.exists() and json.loads(status_path.read_text()).get("training_allowed") is False:
        raise ValueError("この教材は学習対象外です。dataset-status.jsonを確認してください")
    if args.output.exists():
        raise ValueError("Output already exists; never overwrite a previous trial")
    if args.device == "mps" and not torch.backends.mps.is_available():
        raise RuntimeError("MPS is unavailable; do not silently change the experiment device")
    torch.set_num_threads(4)
    torch.manual_seed(args.seed)
    random.seed(args.seed)
    args.output.mkdir(parents=True)
    codec = CharacterCodec(args.base / "vocab.txt")
    splits = {name: read_jsonl(args.data / f"{name}.jsonl") for name in ["train", "dev"]}
    train_items = [codec.encode(row, with_labels=True) for row in splits["train"]]
    model = BertForTokenClassification.from_pretrained(args.base, local_files_only=True,
        num_labels=len(LABELS), id2label=dict(enumerate(LABELS)), label2id=LABEL_TO_ID,
        attn_implementation="sdpa").to(args.device)
    manifest = {"arguments": {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
        "torch": torch.__version__, "transformers": transformers.__version__,
        "parameter_count": sum(p.numel() for p in model.parameters()),
        "input": "current_verse + utterance; original codepoint offsets; char-vocab-v1",
        "dataset_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in args.data.glob("*.jsonl")},
        "selection": "dev span exact count, then dev span F1; no test/regression checkpoint selection",
        "source_code_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob("*.py")},
        "scope": "offline experimental model, not adopted by Dogido"}
    write_json(args.output / "manifest.json", manifest)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=0.01)
    total_steps = args.epochs * ((len(train_items) + args.batch_size - 1) // args.batch_size)
    warmup = max(1, int(total_steps * 0.08))
    def schedule(step):
        return (step + 1) / warmup if step < warmup else max(0.0, (total_steps - step) / (total_steps - warmup))
    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, schedule)
    loss_fn = torch.nn.CrossEntropyLoss(ignore_index=-100)
    stop_reason = "max_epochs"
    started = time.perf_counter()
    history = []
    for epoch in range(1, args.epochs + 1):
        random.shuffle(train_items)
        model.train()
        loss_sum = 0.0
        for start in range(0, len(train_items), args.batch_size):
            batch = collate(train_items[start:start + args.batch_size], codec.pad_id, args.device)
            labels = batch.pop("labels")
            optimizer.zero_grad(set_to_none=True)
            logits = model(**batch).logits
            loss = loss_fn(logits.reshape(-1, len(LABELS)), labels.reshape(-1))
            if not torch.isfinite(loss):
                raise RuntimeError("Non-finite training loss")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            loss_sum += float(loss.detach().cpu())
            if start // args.batch_size % 25 == 0:
                print(json.dumps({"event": "train", "epoch": epoch, "batch": start // args.batch_size,
                    "loss": round(float(loss.detach().cpu()), 5), "elapsed_s": round(time.perf_counter() - started, 1)}), flush=True)
        dev_rows = predict_rows(model, codec, splits["dev"], args.device, args.batch_size)
        dev_metrics = metrics(dev_rows)
        key = (dev_metrics["all"]["exact"], dev_metrics["all"]["span_f1"] or 0.0)
        selected = progress.observe(key)
        if selected:
            model.save_pretrained(args.output / "model", safe_serialization=True)
            shutil.copy2(args.base / "vocab.txt", args.output / "model" / "vocab.txt")
            shutil.copy2(args.base / "provenance.json", args.output / "model" / "base-provenance.json")
            write_json(args.output / "selection.json", {"epoch": epoch, "dev_metrics": dev_metrics})
        record = {"epoch": epoch, "dev_metrics": dev_metrics, "selected": selected,
            "stale_epochs": progress.stale_epochs,
            "train_loss": loss_sum / ((len(train_items) + args.batch_size - 1) // args.batch_size),
            "elapsed_s": round(time.perf_counter() - started, 3)}
        history.append(record)
        write_json(args.output / "training-history.json", history)
        print(json.dumps(record, ensure_ascii=False), flush=True)
        if progress.should_stop:
            stop_reason = "dev_no_improvement"
            print(json.dumps({"event": "early_stop", "epoch": epoch,
                "stale_epochs": progress.stale_epochs, "patience": progress.patience}), flush=True)
            break
    if args.defer_evaluation:
        write_json(args.output / "training-complete.json", {"elapsed_s": time.perf_counter() - started,
            "evaluation": "deferred", "epochs_completed": len(history), "stop_reason": stop_reason,
            "early_stopping_patience": progress.patience})
        return
    del model, optimizer
    if args.device == "mps":
        torch.mps.empty_cache()
    model = BertForTokenClassification.from_pretrained(args.output / "model", local_files_only=True,
        attn_implementation="sdpa").to(args.device)
    reports = {}
    for path in sorted(args.data.glob("*.jsonl")):
        if path.stem not in {"train", "dev", "test", "real_test", "regression"}:
            continue
        rows = predict_rows(model, codec, read_jsonl(path), args.device, args.batch_size)
        reports[path.stem] = metrics(rows)
        (args.output / f"{path.stem}-predictions.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    # 待ち時間はモデル常駐・1件ずつ・同期込み。ロード時間や音声認識は含めない。
    timing_rows = read_jsonl(args.data / "regression.jsonl") or splits["dev"][:8]
    for row in timing_rows:
        predict_rows(model, codec, [row], args.device, 1)
    times = []
    for _ in range(3):
        for row in timing_rows:
            synchronize(args.device)
            tick = time.perf_counter()
            predict_rows(model, codec, [row], args.device, 1)
            synchronize(args.device)
            times.append((time.perf_counter() - tick) * 1000)
    import statistics
    reports["latency"] = {"device": args.device, "count": len(times), "median_ms": statistics.median(times),
        "min_ms": min(times), "max_ms": max(times), "scope": "warm model, encode+inference+decode, batch 1"}
    reports["elapsed_s"] = time.perf_counter() - started
    write_json(args.output / "metrics.json", reports)
    print(json.dumps({"event": "complete", "output": str(args.output), "metrics": reports}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
