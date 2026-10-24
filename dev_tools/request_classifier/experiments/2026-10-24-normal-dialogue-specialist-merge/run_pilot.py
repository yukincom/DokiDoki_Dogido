"""共通seed43から3専門モデルを追加学習し、固定条件で重み合成を診断する。"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
import gc
import hashlib
import importlib.metadata
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
PREVIOUS = HERE.parent / "2026-10-24-normal-dialogue-seed-merge"
sys.path.insert(0, str(PREVIOUS))
import compare_seed_merges as common

read, rows, save, digest, utc_now = common.read, common.rows, common.save, common.digest, common.utc_now
EXPERTS = ("gaze", "recall", "sword")
TRAINING_NAMES = (*EXPERTS, "joint")
RECIPES = [
    {"id": "arithmetic-third", "method": "arithmetic", "scale": 1 / 3},
    {"id": "arithmetic-two-thirds", "method": "arithmetic", "scale": 2 / 3},
    {"id": "arithmetic-one", "method": "arithmetic", "scale": 1.0},
    {"id": "ties20-half", "method": "ties", "keep_percent": 20, "scale": 0.5},
    {"id": "ties20-one", "method": "ties", "keep_percent": 20, "scale": 1.0},
]
CANDIDATES = ["base-seed43", *TRAINING_NAMES, *[r["id"] for r in RECIPES]]


def stable_key(value):
    return hashlib.sha256(("specialist-pilot-20261024:" + value).encode()).hexdigest()


def sample_groups(cases, count):
    """train内だけを、groupを巡回する固定hash順で重複なしに選ぶ。"""
    groups = defaultdict(list)
    for row in cases:
        groups[row["group_id"]].append(row)
    for values in groups.values():
        values.sort(key=lambda r: stable_key(r["id"]))
    selected = []
    order = sorted(groups, key=stable_key)
    while len(selected) < count:
        before = len(selected)
        for key in order:
            if groups[key] and len(selected) < count:
                selected.append(groups[key].pop(0))
        if len(selected) == before:
            raise ValueError("Insufficient unique training rows")
    return selected


def make_datasets(train, labels):
    targets = {"gaze": [k for k in labels if k.startswith("look_")],
               "recall": ["recall_mount", "dogido_recall"], "sword": ["equip_sword"]}
    datasets, inventory = {}, {}
    for name, target in targets.items():
        positive = [r for r in train if r["label"] in target]
        total = len(positive)
        quotas = {"not_target": total // 2, "enemy_information": (total + 2) // 4}
        other = [k for k in labels if k not in target and k not in quotas]
        left = total - sum(quotas.values())
        for i, label in enumerate(other):
            quotas[label] = left // len(other) + int(i < left % len(other))
        replay = [r for label, n in quotas.items()
                  for r in sample_groups([v for v in train if v["label"] == label], n)]
        selected = positive + replay
        selected.sort(key=lambda r: r["id"])
        assert len(selected) == 2 * total == len({r["id"] for r in selected})
        assert set(r["label"] for r in selected) == set(labels)
        datasets[name] = selected
        inventory[name] = {"target_labels": target, "target_count": total,
            "replay_count": len(replay), "total": len(selected), "replay_label_counts": quotas,
            "label_counts": dict(Counter(r["label"] for r in selected)),
            "source_counts": dict(Counter(r["source"] for r in selected)),
            "group_count": len({r["group_id"] for r in selected})}
    # 同じ例が専門別に出現する回数も保つ。独立した新規例としては数えない。
    datasets["joint"] = [r for name in EXPERTS for r in datasets[name]]
    inventory["joint"] = {"total_occurrences": len(datasets["joint"]),
        "unique_rows": len({r["id"] for r in datasets["joint"]}),
        "label_counts": dict(Counter(r["label"] for r in datasets["joint"])),
        "purpose": "same aggregate training row occurrences as all three specialists"}
    assert [len(datasets[n]) for n in TRAINING_NAMES] == [198, 288, 150, 636]
    return datasets, inventory


def code_hashes():
    return {str(p.relative_to(REPO)): digest(p) for p in
            (Path(__file__), PREVIOUS / "compare_seed_merges.py",
             HERE / "reference/merge_utils.py", HERE / "reference/LICENSE.txt")}


def prepare(out):
    if out.exists():
        raise ValueError("Use a new output directory")
    labels, paths = common.verify_sources()
    datasets, inventory = make_datasets(rows(common.RELEASE / "train.jsonl"), labels)
    out.mkdir(parents=True)
    shutil.copyfile(__file__, out / "source-at-execution.py")
    for name, data in datasets.items():
        path = out / "data" / f"{name}.jsonl"
        path.parent.mkdir(exist_ok=True)
        path.write_text("".join(__import__("json").dumps(r, ensure_ascii=False) + "\n" for r in data))
    save(out / "data-inventory.json", inventory)
    protocol = {"created_at_utc": utc_now(), "source_git_head": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        "purpose": "Existing-data pilot: specialize a trained common model, then merge the changes",
        "base": {"seed": 43, "path": str(paths[43].relative_to(REPO)), "sha256": common.hashes(paths[43]),
                 "choice": "Previous dev macro F1 winner; already trained on all 482 source training rows"},
        "source_sha256": code_hashes(),
        "source_data_sha256": {s: digest(common.RELEASE / f"{s}.jsonl") for s in ("train", "dev", "test")},
        "source_training_sha256": read(common.PREVIOUS / "source-provenance.json")["unchanged_training_sources_sha256"],
        "data_sha256": {n: digest(out / "data" / f"{n}.jsonl") for n in datasets},
        "label_to_id": labels, "inventory": inventory,
        "selection_policy": "target rows all retained; equal number replay; replay half not_target, quarter enemy_information, rest balanced over other labels; hash/group round robin; original labels unchanged",
        "training": {"runs": 4, "seed": 42, "learning_rate": 1e-5, "epochs": 3,
            "batch_size": 16, "weight_decay": .01, "warmup_steps": 0, "max_grad_norm": 1.0,
            "optimizer": "adamw_torch", "scheduler": "linear", "max_length": 128, "max_context": 0,
            "device": "cpu", "dtype": "float32", "attention": "sdpa", "full_finetuning": True,
            "initialization": "All tensors including final classifier loaded from the same seed43 checkpoint; new optimizer each run",
            "checkpoint_selection": "fixed final epoch 3; no per-specialist epoch search",
            "training_occurrences_per_specialist": {n: len(datasets[n]) * 3 for n in EXPERTS},
            "aggregate_specialist_occurrences": 1908, "joint_occurrences": 1908,
            "optimizer_steps": {n: math.ceil(len(datasets[n]) / 16) * 3 for n in datasets},
            "compute_caveat": "Same row occurrences; specialists sum to 123 optimizer steps, joint 120. Training is not exactly compute matched."},
        "recipes": RECIPES, "candidate_order": CANDIDATES,
        "merge_scope": "all 66 float32 tensors, including identical-initialized classifier",
        "arithmetic_formula": "base + scale * sum(expert - base); expert order gaze, recall, sword",
        "ties_formula": "base + scale * author TIES topk20 / mass / dis-mean of three expert-minus-base vectors; flatten all tensors together, sorted name order",
        "ties_reference": {"repository": "https://github.com/prateeky2806/ties-merging",
            "commit": "44e7891fc84f3de7e4caa52664cd864ca3715e91", "path": "src/utils/merge_utils.py",
            "reuse": "Previously archived author source; execute only four inspected pure functions"},
        "selection": "All 10 candidates dev macro F1 descending, loss ascending, declared order; freeze before any current-run test predictions",
        "test_policy": "Evaluate every fixed candidate after selection; no coefficient or training changes based on test",
        "evaluation_scope": "Known development/material-design-referenced data; not fresh generalization evidence",
        "runtime_adoption": False}
    save(out / "protocol.json", protocol)
    print("PREPARED: " + str({n: len(d) for n, d in datasets.items()}), flush=True)


def check_inputs(out):
    protocol = read(out / "protocol.json")
    common.verify_sources()
    assert code_hashes() == protocol["source_sha256"]
    assert common.hashes(REPO / protocol["base"]["path"]) == protocol["base"]["sha256"]
    for split, expected in protocol["source_data_sha256"].items():
        assert digest(common.RELEASE / f"{split}.jsonl") == expected
    for name, expected in protocol["data_sha256"].items():
        assert digest(out / "data" / f"{name}.jsonl") == expected
    return protocol


def train_phase(out):
    protocol = check_inputs(out)
    if (out / "training-started.json").exists():
        raise ValueError("Training already started; preserve it")
    torch, load_file, _, _, _ = common.prepare_runtime()
    from transformers import AutoModelForSequenceClassification, AutoTokenizer, DataCollatorWithPadding, Trainer, TrainingArguments, set_seed
    from train import EncodedRows
    labels = protocol["label_to_id"]
    base = REPO / protocol["base"]["path"]
    tokenizer = AutoTokenizer.from_pretrained(base, local_files_only=True)
    source = load_file(base / "model.safetensors", device="cpu")
    save(out / "training-started.json", {"started_at_utc": utc_now(), "protocol_sha256": digest(out / "protocol.json"),
        "python": sys.version, "versions": {k: importlib.metadata.version(k) for k in
            ("torch", "transformers", "accelerate", "safetensors", "numpy", "tokenizers")}})
    results = {}
    for name in TRAINING_NAMES:
        set_seed(42)
        model = AutoModelForSequenceClassification.from_pretrained(base, local_files_only=True,
            dtype=torch.float32, attn_implementation="sdpa")
        state = model.state_dict()
        assert state.keys() == source.keys() and all(torch.equal(state[k], source[k]) for k in source)
        assert model.config.label2id == labels and all(p.requires_grad for p in model.parameters())
        del state
        data = rows(out / "data" / f"{name}.jsonl")
        encoded = EncodedRows(data, tokenizer, 128, labels)
        args = TrainingArguments(output_dir=str(out / "work" / name), learning_rate=1e-5,
            per_device_train_batch_size=16, num_train_epochs=3, weight_decay=.01, warmup_steps=0,
            max_grad_norm=1.0, optim="adamw_torch", lr_scheduler_type="linear",
            eval_strategy="no", save_strategy="no", logging_strategy="steps", logging_steps=5,
            report_to="none", push_to_hub=False, seed=42, data_seed=42, use_cpu=True,
            dataloader_num_workers=0, dataloader_pin_memory=False, disable_tqdm=True,
            torch_compile=False, fp16=False, bf16=False)
        save(out / "training" / name / "arguments.json", args.to_dict())
        trainer = Trainer(model=model, args=args, train_dataset=encoded, processing_class=tokenizer,
            data_collator=DataCollatorWithPadding(tokenizer=tokenizer))
        assert str(next(model.parameters()).device) == "cpu"
        print(f"TRAINING {name}: {len(data)} examples x 3 epochs", flush=True)
        start = time.perf_counter()
        outcome = trainer.train()
        seconds = time.perf_counter() - start
        checkpoint = out / "weights" / name
        model.save_pretrained(checkpoint)
        tokenizer.save_pretrained(checkpoint)
        reloaded = load_file(checkpoint / "model.safetensors", device="cpu")
        assert all(torch.equal(model.state_dict()[k], reloaded[k]) for k in source)
        assert all(bool(torch.isfinite(t).all()) for t in reloaded.values())
        results[name] = {"initial_all_tensors_equal_base": True, "steps": trainer.state.global_step,
            "final_epoch": trainer.state.epoch, "max_tokens": max(encoded.lengths),
            "seconds": seconds, "metrics": outcome.metrics, "checkpoint_sha256": common.hashes(checkpoint),
            "changed_tensors": [k for k in source if not torch.equal(source[k], reloaded[k])]}
        assert results[name]["steps"] == protocol["training"]["optimizer_steps"][name]
        save(out / "training" / name / "history.json", trainer.state.log_history)
        save(out / "training-results.json", results)
        print(f"FINISHED {name}: {seconds:.1f}s", flush=True)
        del model, trainer, encoded, reloaded
        gc.collect()
    check_inputs(out)
    save(out / "training-completed.json", {"finished_at_utc": utc_now(), "runs": 4})


def ties_delta(torch, vectors):
    # 以前にレビューして保存した作者実装の4関数だけを使う。
    path = HERE / "reference/merge_utils.py"
    wanted = {"topk_values_mask", "resolve_zero_signs", "resolve_sign", "disjoint_merge"}
    definitions = [n for n in ast.parse(path.read_text()).body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    assert {n.name for n in definitions} == wanted
    namespace = {"torch": torch}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(path), "exec"), namespace)
    trimmed, density = namespace["topk_values_mask"](vectors, K=20)
    sign = namespace["resolve_sign"](trimmed, "mass")
    merged = namespace["disjoint_merge"](trimmed, "dis-mean", sign)
    return merged, {"actual_density": density.tolist(), "nonzero_merged": int((merged != 0).sum()),
        "parameters_per_model": vectors.shape[1], "models": vectors.shape[0]}


def checkpoint_path(out, name, protocol):
    return REPO / protocol["base"]["path"] if name == "base-seed43" else out / "weights" / name


def check_trained(out):
    assert read(out / "training-completed.json")["runs"] == 4
    for name, result in read(out / "training-results.json").items():
        assert common.hashes(out / "weights" / name) == result["checkpoint_sha256"]


def dev_phase(out):
    protocol = check_inputs(out)
    check_trained(out)
    if (out / "dev-started.json").exists():
        raise ValueError("Dev phase already started")
    save(out / "dev-started.json", {"started_at_utc": utc_now()})
    torch, load_file, save_file, evaluate_checkpoint, _ = common.prepare_runtime()
    base_path = REPO / protocol["base"]["path"]
    base = load_file(base_path / "model.safetensors", device="cpu")
    names = sorted(base)
    expert = [load_file(out / "weights" / n / "model.safetensors", device="cpu") for n in EXPERTS]
    assert len(base) == 66 and all(t.dtype == torch.float32 for t in base.values())
    assert all(s.keys() == base.keys() for s in expert)
    vectors = torch.stack([torch.cat([(s[k] - base[k]).reshape(-1) for k in names]) for s in expert])
    del expert
    arithmetic = vectors.sum(dim=0)
    ties, statistics = ties_delta(torch, vectors)
    del vectors
    save(out / "ties-statistics.json", statistics)
    merged_files = {}
    for recipe in RECIPES:
        delta = arithmetic if recipe["method"] == "arithmetic" else ties
        state, offset = {}, 0
        for key in names:
            length = base[key].numel()
            state[key] = (base[key] + recipe["scale"] * delta[offset:offset + length].reshape(base[key].shape)).contiguous()
            offset += length
        assert offset == delta.numel() and all(bool(torch.isfinite(t).all()) for t in state.values())
        checkpoint = out / "weights" / recipe["id"]
        checkpoint.mkdir()
        save_file(state, checkpoint / "model.safetensors", metadata={"format": "pt"})
        for f in ("config.json", "tokenizer.json", "tokenizer_config.json"):
            shutil.copyfile(base_path / f, checkpoint / f)
        actual = load_file(checkpoint / "model.safetensors", device="cpu")
        assert all(torch.equal(actual[k], state[k]) for k in names)
        merged_files[recipe["id"]] = common.hashes(checkpoint)
        del state, actual
    save(out / "merged-models.json", merged_files)
    del base, arithmetic, ties, delta
    gc.collect()
    results = {}
    for name in CANDIDATES:
        _, metrics = common.evaluate(out, name, checkpoint_path(out, name, protocol),
            {"dev": rows(common.RELEASE / "dev.jsonl")}, protocol["label_to_id"], evaluate_checkpoint)
        results[name] = metrics
        save(out / "dev-results.json", results)
    key = lambda n: (-results[n]["dev"]["macro_f1"], results[n]["dev"]["loss"], CANDIDATES.index(n))
    selection = {"frozen_at_utc": utc_now(), "selection_split": "dev", "test_used": False,
        "best_overall": min(CANDIDATES, key=key), "best_merge": min([r["id"] for r in RECIPES], key=key),
        "ranking": sorted(CANDIDATES, key=key), "results": results}
    save(out / "selection.json", selection)
    check_inputs(out)
    check_trained(out)
    print("DEV SELECTION FROZEN: " + selection["best_overall"] + " / merge " + selection["best_merge"], flush=True)


def test_phase(out):
    protocol = check_inputs(out)
    check_trained(out)
    selection = read(out / "selection.json")
    if (out / "test-started.json").exists():
        raise ValueError("Test phase already started")
    for name, expected in read(out / "merged-models.json").items():
        assert common.hashes(out / "weights" / name) == expected
    save(out / "test-started.json", {"started_at_utc": utc_now(), "selection_sha256": digest(out / "selection.json")})
    _, _, _, evaluate_checkpoint, compare_replicates = common.prepare_runtime()
    from train import write_misclassifications
    results, predictions = {}, {}
    for name in CANDIDATES:
        pred, metrics = common.evaluate(out, name, checkpoint_path(out, name, protocol),
            {"test": rows(common.RELEASE / "test.jsonl")}, protocol["label_to_id"], evaluate_checkpoint)
        results[name] = {**selection["results"][name], **metrics}
        predictions[name] = pred["test"]
        save(out / "reports" / name / "metrics.json", results[name])
        write_misclassifications(out / "reports" / name / "misclassifications.md",
            {"dev": rows(out / "reports" / name / "dev-predictions.jsonl"), **pred})
        save(out / "summary.json", results)
    save(out / "test-comparison.json", compare_replicates(predictions["base-seed43"], predictions, list(protocol["label_to_id"])))
    check_inputs(out)
    assert digest(out / "selection.json") == read(out / "test-started.json")["selection_sha256"]
    save(out / "completed.json", {"finished_at_utc": utc_now(), "training_runs": 4,
        "merged_models": 5, "evaluated_candidates": 10, "prediction_rows": 960, "runtime_adoption": False})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare", "train", "dev", "test"])
    parser.add_argument("--output", type=Path, default=HERE / "runs/pilot")
    args = parser.parse_args()
    {"prepare": prepare, "train": train_phase, "dev": dev_phase, "test": test_phase}[args.phase](args.output.resolve())
