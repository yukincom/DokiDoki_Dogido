"""通常対話578件の3シードを等重み合成し、固定devで選択後に診断する。"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import itertools
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
PREVIOUS = HERE.parent / "2026-10-24-normal-dialogue-colab"
SOURCE = PREVIOUS / "artifacts/colab-results"
LAB = SOURCE / "dev_tools/request_classifier"
RELEASE = LAB / "releases/2026-10-24-normal-dialogue-578"
SEEDS = (42, 43, 44)
GROUPS = list(itertools.combinations(SEEDS, 2)) + [SEEDS]
RECIPES = [{"id": "seed-" + str(s), "seeds": [s]} for s in SEEDS] + [
    {"id": "mean-" + "-".join(map(str, group)), "seeds": list(group)} for group in GROUPS]


def read(path):
    return json.loads(path.read_text())


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def source_paths():
    return {s: SOURCE / "weights" / f"trial-{i}/best" for i, s in enumerate(SEEDS, 1)}


def hashes(path):
    return {p.name: digest(p) for p in sorted(path.iterdir()) if p.is_file()}


def prepare_runtime():
    provenance = read(PREVIOUS / "source-provenance.json")
    for name, expected in provenance["unchanged_training_sources_sha256"].items():
        if digest(SOURCE / name) != expected:
            raise ValueError(f"Reviewed evaluation source changed: {name}")
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false")
    sys.path.insert(0, str(LAB))
    import torch
    from safetensors.torch import load_file, save_file
    from train import evaluate_checkpoint
    from evaluation import compare_replicates
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    torch.use_deterministic_algorithms(True)
    return torch, load_file, save_file, evaluate_checkpoint, compare_replicates


def verify_sources():
    receipt = read(PREVIOUS / "results-review.json")
    manifest = read(SOURCE / "reports/manifest.json")
    paths = source_paths()
    config = read(paths[42] / "config.json")
    labels = read(RELEASE / "labels.json")
    if len(labels) != 16 or config["label2id"] != labels or manifest["label_to_id"] != labels:
        raise ValueError("Expected the fixed 16-label mapping")
    for seed, path in paths.items():
        if digest(path / "model.safetensors") != receipt["weight_sha256"][str(seed)]:
            raise ValueError(f"Source checkpoint hash mismatch: {seed}")
        if read(path / "config.json") != config:
            raise ValueError("Model configurations differ")
        for name in ("tokenizer.json", "tokenizer_config.json"):
            if digest(path / name) != digest(paths[42] / name):
                raise ValueError(f"Tokenizer mismatch: {seed}/{name}")
    for split in ("train", "dev", "test"):
        if digest(RELEASE / f"{split}.jsonl") != manifest["data_sha256"][split]:
            raise ValueError(f"Material changed: {split}")
    return labels, paths


def check_run_inputs(out, protocol):
    if digest(Path(__file__)) != protocol["driver_sha256"]:
        raise ValueError("Driver changed after dev selection")
    for seed, path in source_paths().items():
        if hashes(path) != protocol["source_models"][str(seed)]["sha256"]:
            raise ValueError(f"Source checkpoint changed: {seed}")
    for split, expected in protocol["data_sha256"].items():
        if digest(RELEASE / f"{split}.jsonl") != expected:
            raise ValueError(f"Input changed: {split}")
    for name, files in read(out / "merged-models.json").items():
        if hashes(out / "weights" / name) != files:
            raise ValueError(f"Merged checkpoint changed: {name}")


def evaluate(out, name, checkpoint, cases, labels, evaluate_checkpoint):
    predictions, metrics = evaluate_checkpoint(checkpoint, cases, out / "reports" / name,
        out / "work" / name, batch_size=16, max_length=128, device="cpu", label_to_id=labels)
    for split, values in metrics.items():
        print(f"{name} {split}: {values['correct']}/{values['n']}, macro F1={values['macro_f1']:.6f}, loss={values['loss']:.6f}", flush=True)
    return predictions, metrics


def rank_key(name, results):
    metric = results[name]["dev"]
    return (-metric["macro_f1"], metric["loss"], next(i for i, r in enumerate(RECIPES) if r["id"] == name))


def dev_phase(out):
    if out.exists():
        raise ValueError("Use a new output directory; previous runs are preserved")
    labels, paths = verify_sources()
    torch, load_file, save_file, evaluate_checkpoint, compare_replicates = prepare_runtime()
    source = {seed: load_file(path / "model.safetensors", device="cpu") for seed, path in paths.items()}
    for name, tensor in source[42].items():
        for seed in SEEDS:
            other = source[seed][name]
            if other.shape != tensor.shape or other.dtype != tensor.dtype:
                raise ValueError(f"Incompatible tensor: {seed}/{name}")
            if tensor.is_floating_point():
                if other.dtype != torch.float32 or not bool(torch.isfinite(other).all()):
                    raise ValueError("Expected finite float32 parameters")
            elif not torch.equal(other, tensor):
                raise ValueError("Nonfloating buffers differ")
    if any(state.keys() != source[42].keys() for state in source.values()):
        raise ValueError("Parameter keys differ")
    out.mkdir(parents=True)
    shutil.copyfile(__file__, out / "source-at-execution.py")
    protocol = {
        "started_at_utc": utc_now(), "source_git_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        "driver_sha256": digest(Path(__file__)), "recipes": RECIPES,
        "merge_formula": "sum(source_tensor * (1 / number_of_sources)); ascending seed order; float32",
        "merge_scope": "all floating tensors including head and classifier; equal nonfloating buffers copied",
        "source_models": {str(s): {"path": str(p.relative_to(REPO)), "sha256": hashes(p)} for s, p in paths.items()},
        "data_sha256": {s: digest(RELEASE / f"{s}.jsonl") for s in ("train", "dev", "test")},
        "evaluation_source_sha256": read(PREVIOUS / "source-provenance.json")["unchanged_training_sources_sha256"],
        "label_to_id": labels, "device": "cpu", "dtype": "float32", "attention": "sdpa",
        "batch_size": 16, "max_length": 128, "max_context": 0,
        "torch_threads": 4, "torch_interop_threads": 1, "deterministic_algorithms": True,
        "selection": "dev macro F1 descending, dev cross-entropy loss ascending, fixed recipe order; best single and best merge frozen before test",
        "test_policy": "report all seven fixed candidates after selection; no recipe or coefficient changes based on test",
        "evaluation_scope": "Known material-design-referenced dev/test; exploratory merge comparison, not unknown live-input accuracy",
        "training_runs": 0, "runtime_adoption": False,
        "python": sys.version, "platform": platform.platform(),
        "versions": {n: importlib.metadata.version(n) for n in
            ("torch", "transformers", "accelerate", "numpy", "safetensors", "tokenizers")},
    }
    save(out / "protocol.json", protocol)
    checkpoints = {f"seed-{s}": p for s, p in paths.items()}
    merged_hashes = {}
    for recipe in RECIPES[3:]:
        seeds, name = recipe["seeds"], recipe["id"]
        merged = {}
        for key, first in source[seeds[0]].items():
            if first.is_floating_point():
                value = torch.zeros_like(first)
                for seed in seeds:
                    value.add_(source[seed][key] * (1 / len(seeds)))
                merged[key] = value.contiguous()
            else:
                merged[key] = first.clone()
        checkpoint = out / "weights" / name
        checkpoint.mkdir(parents=True)
        save_file(merged, checkpoint / "model.safetensors", metadata={"format": "pt"})
        for filename in ("config.json", "tokenizer.json", "tokenizer_config.json"):
            shutil.copyfile(paths[42] / filename, checkpoint / filename)
        actual = load_file(checkpoint / "model.safetensors", device="cpu")
        if actual.keys() != merged.keys() or not all(torch.equal(actual[k], merged[k]) for k in merged):
            raise ValueError("Reloaded merge differs from the specified formula")
        merged_hashes[name] = hashes(checkpoint)
        checkpoints[name] = checkpoint
        del merged, actual
    save(out / "merged-models.json", merged_hashes)
    del source
    predictions, results, cpu_comparison = {}, {}, {}
    cases = rows(RELEASE / "dev.jsonl")
    for name, checkpoint in checkpoints.items():
        pred, metric = evaluate(out, name, checkpoint, {"dev": cases}, labels, evaluate_checkpoint)
        predictions[name], results[name] = pred["dev"], metric
        if name.startswith("seed-"):
            reference = rows(SOURCE / "reports" / name / "dev-predictions.jsonl")
            cpu_comparison[name] = {
                "changed_prediction_ids": [a["id"] for a, b in zip(reference, pred["dev"], strict=True) if a["predicted"] != b["predicted"]],
                "max_absolute_probability_difference": max(abs(a["probabilities"][label] - b["probabilities"][label])
                    for a, b in zip(reference, pred["dev"], strict=True) for label in labels),
            }
    singles = [r["id"] for r in RECIPES[:3]]
    merges = [r["id"] for r in RECIPES[3:]]
    best_single = min(singles, key=lambda n: rank_key(n, results))
    best_merge = min(merges, key=lambda n: rank_key(n, results))
    selection = {"frozen_at_utc": utc_now(), "selection_split": "dev", "best_single": best_single,
        "best_merge": best_merge, "best_overall": min(results, key=lambda n: rank_key(n, results)),
        "dev_ranking": sorted(results, key=lambda n: rank_key(n, results)),
        "dev_results": results, "test_used_for_selection": False}
    save(out / "dev-comparison.json", compare_replicates(predictions[best_single], predictions, list(labels)))
    save(out / "cpu-vs-colab-dev.json", cpu_comparison)
    save(out / "selection.json", selection)
    check_run_inputs(out, protocol)
    print("DEV SELECTION FROZEN: " + json.dumps({k: selection[k] for k in ("best_single", "best_merge", "best_overall")}), flush=True)


def test_phase(out):
    labels, paths = verify_sources()
    protocol, selection = read(out / "protocol.json"), read(out / "selection.json")
    check_run_inputs(out, protocol)
    if (out / "completed.json").exists() or any((out / "reports" / r["id"] / "test-predictions.jsonl").exists() for r in RECIPES):
        raise ValueError("Test phase already started; preserve its outputs")
    _, _, _, evaluate_checkpoint, compare_replicates = prepare_runtime()
    selection_sha = digest(out / "selection.json")
    predictions, results = {}, {}
    cases = rows(RELEASE / "test.jsonl")
    # Read the exact IDs of the two approved additions from their curation records.
    curation = LAB / "curation"
    feedback = rows(curation / "2026-10-24-normal-dialogue-feedback-1548/candidates.jsonl")
    contrast = rows(curation / "2026-10-24-normal-dialogue-tool-contrast/candidates.jsonl")
    wanted = {r["id"] for r in feedback + contrast}
    additions = [r for r in rows(RELEASE / "train.jsonl") if r["id"] in wanted]
    if len(additions) != 47:
        raise ValueError("Expected all 47 added training examples")
    from train import write_misclassifications
    for recipe in RECIPES:
        name = recipe["id"]
        checkpoint = paths[recipe["seeds"][0]] if len(recipe["seeds"]) == 1 else out / "weights" / name
        pred, metric = evaluate(out, name, checkpoint, {"test": cases, "train_additions": additions}, labels, evaluate_checkpoint)
        results[name] = {**selection["dev_results"][name], **metric}
        save(out / "reports" / name / "metrics.json", results[name])
        write_misclassifications(out / "reports" / name / "misclassifications.md",
            {"dev": rows(out / "reports" / name / "dev-predictions.jsonl"), **pred})
        predictions[name] = pred["test"]
        for group_name, group in (("live_feedback", feedback), ("synthetic_contrast", contrast)):
            ids = {r["id"] for r in group}
            selected = [r for r in pred["train_additions"] if r["id"] in ids]
            results[name][group_name] = {"n": len(selected), "correct": sum(r["correct"] for r in selected), "scope": "training_examples"}
    save(out / "summary.json", results)
    save(out / "test-comparison.json", compare_replicates(predictions[selection["best_single"]], predictions, list(labels)))
    if digest(out / "selection.json") != selection_sha:
        raise ValueError("Selection changed during test evaluation")
    check_run_inputs(out, protocol)
    save(out / "completed.json", {"finished_at_utc": utc_now(), "selection_sha256": selection_sha,
        "candidates": len(RECIPES), "dev_predictions": 7 * 46, "test_predictions": 7 * 50,
        "training_addition_predictions": 7 * 47, "training_runs": 0, "runtime_adoption": False})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("dev", "test"), required=True)
    parser.add_argument("--output-dir", type=Path, default=HERE / "runs/equal-weights")
    args = parser.parse_args()
    (dev_phase if args.phase == "dev" else test_phase)(args.output_dir.resolve())
