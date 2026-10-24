"""全体平均のdev低下を受け、最終分類層だけ保持した9構成を追加比較する。"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import compare_seed_merges as common


HERE = Path(__file__).resolve().parent
FIRST = HERE / "runs/equal-weights"
FIXED_KEYS = ("classifier.weight", "classifier.bias")
EXTRA = [{"id": "mean-" + "-".join(map(str, group)) + f"-classifier-{seed}",
          "mean": "mean-" + "-".join(map(str, group)), "classifier_seed": seed}
         for group in common.GROUPS for seed in group]
ALL_NAMES = [r["id"] for r in common.RECIPES] + [r["id"] for r in EXTRA]


def model_paths(out):
    paths = {f"seed-{s}": p for s, p in common.source_paths().items()}
    paths.update({r["id"]: FIRST / "weights" / r["id"] for r in common.RECIPES[3:]})
    paths.update({r["id"]: out / "weights" / r["id"] for r in EXTRA})
    return paths


def check_sources():
    labels, sources = common.verify_sources()
    common.check_run_inputs(FIRST, common.read(FIRST / "protocol.json"))
    if any((FIRST / "reports" / name / "test-predictions.jsonl").exists() for name in ALL_NAMES[:7]):
        raise ValueError("The additional dev experiment must precede merge test evaluation")
    return labels, sources


def check_saved(out):
    protocol = common.read(out / "protocol.json")
    if common.digest(Path(__file__)) != protocol["driver_sha256"]:
        raise ValueError("Follow-up driver changed")
    for name, expected in protocol["first_stage_sha256"].items():
        if common.digest(FIRST / name) != expected:
            raise ValueError(f"First-stage input changed: {name}")
    for name, expected in common.read(out / "merged-models.json").items():
        if common.hashes(out / "weights" / name) != expected:
            raise ValueError(f"Saved classifier-fixed merge changed: {name}")


def dev_phase(out):
    if out.exists():
        raise ValueError("Use a new output directory")
    labels, sources = check_sources()
    torch, load_file, save_file, evaluate_checkpoint, compare_replicates = common.prepare_runtime()
    out.mkdir(parents=True)
    shutil.copyfile(__file__, out / "source-at-execution.py")
    first_protocol = common.read(FIRST / "protocol.json")
    protocol = {**first_protocol, "started_at_utc": common.utc_now(),
        "driver_sha256": common.digest(Path(__file__)),
        "first_stage": str(FIRST.relative_to(common.REPO)),
        "first_stage_sha256": {n: common.digest(FIRST / n) for n in ("protocol.json", "selection.json", "merged-models.json")},
        "design": "Exploratory follow-up after four whole-model equal means degraded dev; no merged test results observed before designing these nine candidates",
        "extra_recipes": EXTRA, "all_candidate_order": ALL_NAMES,
        "merge_formula": "Reuse the exact whole-model equal mean; replace only classifier.weight and classifier.bias with one constituent seed's original tensors",
        "fixed_keys": list(FIXED_KEYS),
        "selection": "All sixteen candidates: dev macro F1 descending, dev loss ascending, fixed candidate order; freeze before test",
        "test_policy": "After freezing all choices, evaluate all sixteen fixed candidates on test and the 47 added training examples; no tuning using test",
    }
    common.save(out / "protocol.json", protocol)
    classifiers = {s: {k: v.clone() for k, v in load_file(p / "model.safetensors").items() if k in FIXED_KEYS}
                   for s, p in sources.items()}
    manifests = {}
    for recipe in EXTRA:
        state = load_file(FIRST / "weights" / recipe["mean"] / "model.safetensors")
        state.update(classifiers[recipe["classifier_seed"]])
        checkpoint = out / "weights" / recipe["id"]
        checkpoint.mkdir(parents=True)
        save_file(state, checkpoint / "model.safetensors", metadata={"format": "pt"})
        for name in ("config.json", "tokenizer.json", "tokenizer_config.json"):
            shutil.copyfile(sources[42] / name, checkpoint / name)
        actual = load_file(checkpoint / "model.safetensors")
        if actual.keys() != state.keys() or not all(torch.equal(actual[k], state[k]) for k in state):
            raise ValueError("Saved merge differs from fixed-classifier recipe")
        manifests[recipe["id"]] = common.hashes(checkpoint)
        del state, actual
    common.save(out / "merged-models.json", manifests)
    results = common.read(FIRST / "selection.json")["dev_results"]
    predictions = {}
    for name in ALL_NAMES[:7]:
        directory = out / "reports" / name
        directory.mkdir(parents=True)
        for filename in ("dev-predictions.jsonl", "metrics.json", "misclassifications.md"):
            shutil.copyfile(FIRST / "reports" / name / filename, directory / filename)
        predictions[name] = common.rows(directory / "dev-predictions.jsonl")
    cases = common.rows(common.RELEASE / "dev.jsonl")
    for recipe in EXTRA:
        name = recipe["id"]
        pred, metric = common.evaluate(out, name, out / "weights" / name, {"dev": cases}, labels, evaluate_checkpoint)
        predictions[name], results[name] = pred["dev"], metric
    key = lambda n: (-results[n]["dev"]["macro_f1"], results[n]["dev"]["loss"], ALL_NAMES.index(n))
    best_single = min(ALL_NAMES[:3], key=key)
    selection = {"frozen_at_utc": common.utc_now(), "selection_split": "dev",
        "best_single": best_single, "best_whole_mean": min(ALL_NAMES[3:7], key=key),
        "best_fixed_classifier": min(ALL_NAMES[7:], key=key), "best_merge": min(ALL_NAMES[3:], key=key),
        "best_overall": min(ALL_NAMES, key=key), "dev_ranking": sorted(ALL_NAMES, key=key),
        "dev_results": results, "test_used_for_selection": False}
    common.save(out / "selection.json", selection)
    common.save(out / "dev-comparison.json", compare_replicates(predictions[best_single], predictions, list(labels)))
    check_saved(out)
    print("ALL DEV CHOICES FROZEN: " + str({k: selection[k] for k in ("best_single", "best_merge", "best_overall")}), flush=True)


def test_phase(out):
    labels, _ = check_sources()
    check_saved(out)
    if (out / "completed.json").exists() or any((out / "reports" / name / "test-predictions.jsonl").exists() for name in ALL_NAMES):
        raise ValueError("Test phase already started; preserve its outputs")
    _, _, _, evaluate_checkpoint, compare_replicates = common.prepare_runtime()
    from train import write_misclassifications
    selection = common.read(out / "selection.json")
    selection_sha = common.digest(out / "selection.json")
    common.save(out / "test-started.json", {"started_at_utc": common.utc_now(), "selection_sha256": selection_sha})
    groups = {"live_feedback": common.rows(common.LAB / "curation/2026-10-24-normal-dialogue-feedback-1548/candidates.jsonl"),
              "synthetic_contrast": common.rows(common.LAB / "curation/2026-10-24-normal-dialogue-tool-contrast/candidates.jsonl")}
    wanted = {r["id"] for group in groups.values() for r in group}
    additions = [r for r in common.rows(common.RELEASE / "train.jsonl") if r["id"] in wanted]
    if len(additions) != 47:
        raise ValueError("Expected the fixed 47 added training rows")
    cases = {"test": common.rows(common.RELEASE / "test.jsonl"), "train_additions": additions}
    results, predictions = {}, {}
    for name, checkpoint in model_paths(out).items():
        pred, metric = common.evaluate(out, name, checkpoint, cases, labels, evaluate_checkpoint)
        results[name] = {**selection["dev_results"][name], **metric}
        common.save(out / "reports" / name / "metrics.json", results[name])
        write_misclassifications(out / "reports" / name / "misclassifications.md",
            {"dev": common.rows(out / "reports" / name / "dev-predictions.jsonl"), **pred})
        predictions[name] = pred["test"]
        for group_name, group in groups.items():
            ids = {r["id"] for r in group}
            values = [r for r in pred["train_additions"] if r["id"] in ids]
            results[name][group_name] = {"n": len(values), "correct": sum(r["correct"] for r in values), "scope": "training_examples"}
    common.save(out / "summary.json", results)
    common.save(out / "test-comparison.json", compare_replicates(predictions[selection["best_single"]], predictions, list(labels)))
    cpu_comparison = {}
    for name in ALL_NAMES[:3]:
        original = common.rows(common.SOURCE / "reports" / name / "test-predictions.jsonl")
        cpu_comparison[name] = {"changed_prediction_ids": [a["id"] for a, b in zip(original, predictions[name], strict=True) if a["predicted"] != b["predicted"]],
            "max_absolute_probability_difference": max(abs(a["probabilities"][label] - b["probabilities"][label])
                for a, b in zip(original, predictions[name], strict=True) for label in labels)}
    common.save(out / "cpu-vs-colab-test.json", cpu_comparison)
    if common.digest(out / "selection.json") != selection_sha:
        raise ValueError("Dev selection changed during test")
    check_sources()
    check_saved(out)
    common.save(out / "completed.json", {"finished_at_utc": common.utc_now(), "selection_sha256": selection_sha,
        "candidates": len(ALL_NAMES), "dev_predictions": len(ALL_NAMES) * 46,
        "test_predictions": len(ALL_NAMES) * 50, "training_addition_predictions": len(ALL_NAMES) * 47,
        "training_runs": 0, "runtime_adoption": False})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("dev", "test"), required=True)
    parser.add_argument("--output-dir", type=Path, default=HERE / "runs/classifier-fixed")
    args = parser.parse_args()
    (dev_phase if args.phase == "dev" else test_phase)(args.output_dir.resolve())
