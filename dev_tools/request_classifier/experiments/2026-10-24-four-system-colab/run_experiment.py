"""固定教材・同一初期重みから28本を学習し、固定候補の合成と診断を行う。"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
from functools import partial
import gc
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import shutil
from statistics import mean, stdev
import sys
import time
import traceback
import zipfile


def read(path):
    return json.loads(path.read_text())


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temp.replace(path)


def now():
    return datetime.now(timezone.utc).isoformat()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def file_hashes(folder):
    return {p.name: digest(p) for p in sorted(folder.iterdir()) if p.is_file()}


def archive_files(root, destination, files):
    """モデルを一度にメモリへ載せずZIPと内部hash一覧を作る。"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(".partial.zip")
    checksums = {str(p.relative_to(root)): digest(p) for p in sorted(set(files))}
    with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as z:
        for name in checksums:
            z.write(root / name, name)
        z.writestr("archive-sha256.json", json.dumps(checksums, indent=2) + "\n")
    temp.replace(destination)
    record = {"file": str(destination.relative_to(root)), "bytes": destination.stat().st_size,
              "sha256": digest(destination), "members": len(checksums), "created_at_utc": now()}
    save(destination.with_suffix(".json"), record)
    return record


class Experiment:
    def __init__(self, root):
        self.root = root
        self.protocol = read(root / "training-protocol.json")
        self.inputs = read(root / "input-sha256.json")
        self.release = root / self.protocol["release"]
        self.base = root / "base"
        self.out = root / "run"
        self.labels = self.protocol["label_to_id"]
        self.seeds = self.protocol["seeds"]
        self.experts = list(self.protocol["experts"])
        self.recipes = self.protocol["recipes"]
        self.families = self.experts + [r["id"] for r in self.recipes]
        self.candidates = self.protocol["candidates"]
        self.identity = {"protocol_sha256": digest(root / "training-protocol.json"),
                         "input_manifest_sha256": digest(root / "input-sha256.json")}
        self.ready = False

    def check_inputs(self):
        for name, expected in self.inputs.items():
            require(digest(self.root / name) == expected, f"Input changed: {name}")
        require(file_hashes(self.base) == self.protocol["base_files_sha256"], "Common checkpoint changed")
        require(read(self.base / "config.json")["label2id"] == self.labels, "Base labels differ")
        self.out.mkdir(exist_ok=True)
        path = self.out / "identity.json"
        if path.exists():
            require(read(path) == self.identity, "Output belongs to a different input or protocol")
        else:
            save(path, self.identity)

    def runtime(self):
        if self.ready:
            return
        os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                          TOKENIZERS_PARALLELISM="false", CUBLAS_WORKSPACE_CONFIG=":4096:8")
        sys.path.insert(0, str(self.root / "dev_tools/request_classifier"))
        import torch
        import train as shared
        from safetensors.torch import load_file, save_file
        self.torch, self.shared = torch, shared
        self.load_file, self.save_file = load_file, save_file
        require(torch.cuda.is_available(), "CUDA GPU is required; CPU fallback is disabled")
        torch.set_num_threads(4)
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        torch.backends.cudnn.benchmark = False
        torch.set_float32_matmul_precision("highest")
        torch.use_deterministic_algorithms(True)
        self.ready = True
        environment = {"at_utc": now(), "python": sys.version,
            "versions": {n: importlib.metadata.version(n) for n in
                         ("torch", "transformers", "accelerate", "safetensors", "numpy", "tokenizers")},
            "gpu": torch.cuda.get_device_name(0), "gpu_bytes": torch.cuda.get_device_properties(0).total_memory,
            "cuda": torch.version.cuda, "dtype": "float32", "attention": "sdpa", "tf32": False,
            "deterministic_algorithms": torch.are_deterministic_algorithms_enabled()}
        save(self.out / "environment-latest.json", environment)
        if not (self.out / "environment-first.json").exists():
            save(self.out / "environment-first.json", environment)

    def job_dir(self, seed, expert):
        return self.out / "trained" / f"seed{seed}-{expert}"

    def checkpoint(self, name):
        if name == "base-seed43":
            return self.base
        for seed in self.seeds:
            for expert in self.experts:
                if name == f"seed{seed}-{expert}":
                    return self.job_dir(seed, expert) / "best"
        return self.out / "merged" / name / "checkpoint"

    def record_path(self, name):
        checkpoint = self.checkpoint(name)
        return checkpoint.parent / "done.json"

    def check_model(self, name):
        if name == "base-seed43":
            expected = self.protocol["base_files_sha256"]
        else:
            record = read(self.record_path(name))
            require(record["identity"] == self.identity, f"Model provenance differs: {name}")
            expected = record["checkpoint_sha256"]
        require(file_hashes(self.checkpoint(name)) == expected, f"Checkpoint changed: {name}")
        return expected

    def fresh_job(self, path):
        if path.exists():
            partials = self.out / "interrupted"
            partials.mkdir(exist_ok=True)
            dest = partials / (path.name + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f"))
            path.rename(dest)
            print(f"Interrupted files preserved: {dest.relative_to(self.root)}", flush=True)
        path.mkdir(parents=True)

    def validate_state(self, state, base):
        torch = self.torch
        require(state.keys() == base.keys(), "Tensor names differ")
        for key, value in state.items():
            require(value.shape == base[key].shape and value.dtype == torch.float32,
                    f"Tensor shape/dtype differs: {key}")
            require(bool(torch.isfinite(value).all()), f"Nonfinite parameter: {key}")

    def train(self):
        self.check_inputs()
        self.runtime()
        from transformers import AutoModelForSequenceClassification, AutoTokenizer, DataCollatorWithPadding, TrainingArguments, set_seed
        from dataset import read_material_splits
        splits, labels = read_material_splits(self.release)
        require(labels == self.labels, "Material label map differs")
        require(len(splits["train"]) == 818, "Shared material count differs")
        torch, shared = self.torch, self.shared
        source = self.load_file(self.base / "model.safetensors", device="cpu")
        require(len(source) == 66, "Expected the archived 66-tensor checkpoint")
        tokenizer = AutoTokenizer.from_pretrained(self.base, local_files_only=True)
        dev = shared.EncodedRows(splits["dev"], tokenizer, 128, labels)
        training = self.protocol["training"]
        inventory = read(self.release / "expert-inventory.json")
        for seed in self.seeds:
            for expert in self.experts:
                name = f"seed{seed}-{expert}"
                directory = self.job_dir(seed, expert)
                if (directory / "done.json").exists():
                    self.check_model(name)
                    print(f"KEEP completed training: {name}", flush=True)
                    continue
                self.fresh_job(directory)
                save(directory / "started.json", {"identity": self.identity, "at_utc": now(), "seed": seed})
                data = rows(self.release / "experts" / expert / "train.jsonl")
                require(len(data) == inventory[expert]["training_occurrences_per_epoch"], "Training occurrences differ")
                require(len({r["id"] for r in data}) == 818, "A shared row was lost")
                set_seed(seed)
                model = AutoModelForSequenceClassification.from_pretrained(self.base, local_files_only=True,
                    dtype=torch.float32, attn_implementation="sdpa")
                state = model.state_dict()
                self.validate_state(state, source)
                require(all(torch.equal(state[k], source[k]) for k in source), "Initial weights/head differ")
                require(model.config.label2id == labels and all(p.requires_grad for p in model.parameters()),
                        "Expected all-parameter 16-class training")
                del state
                encoded = shared.EncodedRows(data, tokenizer, 128, labels)
                callback = shared.SaveBestDevelopment(directory, self.seeds.index(seed) + 1,
                                                       training["learning_rate"], tokenizer)
                args = TrainingArguments(output_dir=str(directory / "work"),
                    learning_rate=training["learning_rate"], num_train_epochs=training["epochs"],
                    per_device_train_batch_size=16, per_device_eval_batch_size=16,
                    weight_decay=.01, warmup_steps=0, max_grad_norm=1.0, optim="adamw_torch",
                    lr_scheduler_type="linear", eval_strategy="epoch", save_strategy="no",
                    logging_strategy="epoch", report_to="none", seed=seed, data_seed=seed,
                    dataloader_num_workers=0, dataloader_pin_memory=False, disable_tqdm=True,
                    torch_compile=False, fp16=False, bf16=False, tf32=False)
                trainer = shared.MeasuredTrainer(model=model, args=args, train_dataset=encoded,
                    eval_dataset=dev, processing_class=tokenizer,
                    data_collator=DataCollatorWithPadding(tokenizer=tokenizer),
                    compute_metrics=partial(shared.compute_metrics, labels=list(labels)), callbacks=[callback])
                require(str(next(trainer.model.parameters()).device).startswith("cuda"), "Training device differs")
                save(directory / "arguments.json", args.to_dict())
                print(f"TRAIN {name}: {len(data)} occurrences, up to {training['epochs']} epochs", flush=True)
                shared.synchronize("cuda")
                started = time.perf_counter()
                result = trainer.train()
                shared.synchronize("cuda")
                seconds = time.perf_counter() - started
                require(callback.best is not None, "No best-dev checkpoint was saved")
                save(directory / "trainer-history.json", trainer.state.log_history)
                best = self.load_file(directory / "best/model.safetensors", device="cpu")
                self.validate_state(best, source)
                save(directory / "done.json", {"identity": self.identity, "completed_at_utc": now(),
                    "seed": seed, "expert": expert, "unique_train": 818, "train_occurrences": len(data),
                    "initial_all_66_tensors_equal": True, "all_parameters_trainable": True,
                    "best_development": callback.best, "steps": trainer.state.global_step,
                    "max_tokens": max(encoded.lengths), "seconds": seconds, "metrics": result.metrics,
                    "changed_tensors": [k for k in source if not torch.equal(best[k], source[k])],
                    "checkpoint_sha256": file_hashes(directory / "best")})
                del best, model, trainer, encoded, callback
                gc.collect()
                torch.cuda.empty_cache()
            self.export_seed(seed)
        save(self.out / "training-completed.json", {"at_utc": now(), "jobs": 28, "identity": self.identity})
        del source
        gc.collect()

    def export_seed(self, seed):
        available = [n for n in self.experts if (self.job_dir(seed, n) / "done.json").exists()]
        directories = [self.job_dir(seed, n) for n in available]
        if not directories:
            return
        for expert in available:
            self.check_model(f"seed{seed}-{expert}")
        dest = self.root / "exports" / f"weights-seed{seed}.zip"
        source_hash = {d.name: read(d / "done.json")["checkpoint_sha256"] for d in directories}
        fingerprint = dest.with_suffix(".sources.json")
        if dest.exists() and fingerprint.exists() and read(fingerprint) == source_hash:
            require(digest(dest) == read(dest.with_suffix(".json"))["sha256"], "Saved seed archive changed")
            return
        files = [p for d in directories for p in d.rglob("*") if p.is_file() and "work" not in p.relative_to(d).parts]
        files += [self.root / "training-protocol.json", self.root / "source-provenance.json"]
        record = archive_files(self.root, dest, files)
        record.update(seed=seed, experts=available, complete_seed=len(available) == 4)
        save(dest.with_suffix(".json"), record)
        save(fingerprint, source_hash)
        print(json.dumps({"event": "seed_archive_ready", **record}), flush=True)

    def ties_delta(self, vectors):
        wanted = {"topk_values_mask", "resolve_zero_signs", "resolve_sign", "disjoint_merge"}
        path = self.root / "reference/merge_utils.py"
        nodes = [n for n in ast.parse(path.read_text()).body if isinstance(n, ast.FunctionDef) and n.name in wanted]
        require({n.name for n in nodes} == wanted, "TIES functions missing")
        namespace = {"torch": self.torch}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
        trimmed, density = namespace["topk_values_mask"](vectors, K=20)
        sign = namespace["resolve_sign"](trimmed, "mass")
        delta = namespace["disjoint_merge"](trimmed, "dis-mean", sign)
        return delta, {"actual_density": density.tolist(), "parameters_per_model": vectors.shape[1], "models": 4}

    def save_merged(self, name, state, base, recipe):
        directory = self.checkpoint(name).parent
        if (directory / "done.json").exists():
            self.check_model(name)
            return
        self.fresh_job(directory)
        checkpoint = directory / "checkpoint"
        checkpoint.mkdir()
        self.validate_state(state, base)
        self.save_file(state, checkpoint / "model.safetensors", metadata={"format": "pt"})
        for filename in self.protocol["base_files_sha256"]:
            if filename != "model.safetensors":
                shutil.copyfile(self.base / filename, checkpoint / filename)
        restored = self.load_file(checkpoint / "model.safetensors", device="cpu")
        require(all(self.torch.equal(restored[k], state[k]) for k in state), "Saved merge differs")
        save(directory / "done.json", {"identity": self.identity, "completed_at_utc": now(),
            "recipe": recipe, "checkpoint_sha256": file_hashes(checkpoint)})

    def merge(self):
        self.check_inputs()
        self.runtime()
        torch = self.torch
        base = self.load_file(self.base / "model.safetensors", device="cpu")
        names = sorted(base)
        for seed in self.seeds:
            for expert in self.experts:
                self.check_model(f"seed{seed}-{expert}")
            if all(self.record_path(f"seed{seed}-{r['id']}").exists() for r in self.recipes):
                for recipe in self.recipes:
                    self.check_model(f"seed{seed}-{recipe['id']}")
                continue
            vectors = []
            for expert in self.experts:
                state = self.load_file(self.checkpoint(f"seed{seed}-{expert}") / "model.safetensors", device="cpu")
                self.validate_state(state, base)
                vectors.append(torch.cat([(state[k] - base[k]).reshape(-1) for k in names]))
                del state
            vectors = torch.stack(vectors)
            arithmetic = vectors.sum(dim=0)
            ties, statistics = self.ties_delta(vectors)
            del vectors
            save(self.out / "merge-statistics" / f"seed{seed}.json", statistics)
            for recipe in self.recipes:
                delta = arithmetic if recipe["method"] == "arithmetic" else ties
                merged, offset = {}, 0
                for key in names:
                    size = base[key].numel()
                    merged[key] = (base[key] + recipe["scale"] * delta[offset:offset + size].reshape(base[key].shape)).contiguous()
                    offset += size
                require(offset == delta.numel(), "Merge vector length differs")
                self.save_merged(f"seed{seed}-{recipe['id']}", merged, base,
                    {**recipe, "seed": seed, "sources": [f"seed{seed}-{e}" for e in self.experts]})
                del merged
            del arithmetic, ties, delta
            gc.collect()
        # 同じ方式の7反復を等重み平均する。評価平均とは別の実モデル。
        for family in self.families:
            name = f"mean7-{family}"
            if self.record_path(name).exists():
                self.check_model(name)
                continue
            average = {key: torch.zeros_like(value) for key, value in base.items()}
            for seed in self.seeds:
                source = f"seed{seed}-{family}"
                self.check_model(source)
                state = self.load_file(self.checkpoint(source) / "model.safetensors", device="cpu")
                self.validate_state(state, base)
                for key in average:
                    average[key].add_(state[key], alpha=1 / len(self.seeds))
                del state
            self.save_merged(name, average, base,
                {"method": "equal_mean", "sources": [f"seed{s}-{family}" for s in self.seeds], "scale": 1 / 7})
            del average
        save(self.out / "merging-completed.json", {"at_utc": now(), "merged_checkpoints": 44,
            "per_seed_four_expert_merges": 35, "seven_seed_weight_means": 9, "identity": self.identity})
        del base
        gc.collect()

    def evaluate(self, name, split):
        checkpoint_hashes = self.check_model(name)
        directory = self.out / "reports" / name / split
        done = directory / "done.json"
        expected = {"identity": self.identity, "checkpoint_sha256": checkpoint_hashes,
                    "split_sha256": digest(self.release / f"{split}.jsonl")}
        if done.exists():
            record = read(done)
            require(record["source"] == expected, "Evaluation sources differ")
            for filename, value in record["report_sha256"].items():
                require(digest(directory / filename) == value, "Completed prediction report changed")
            return read(directory / "metrics.json")[split]
        self.fresh_job(directory)
        selected = None
        if split == "dev" and name.startswith("seed") and name.rsplit("-", 1)[-1] in self.experts:
            selected = read(self.record_path(name))["best_development"]
        _, metrics = self.shared.evaluate_checkpoint(self.checkpoint(name),
            {split: rows(self.release / f"{split}.jsonl")}, directory,
            self.out / "evaluation-work" / name / split,
            batch_size=16, max_length=128, device="cuda", selected=selected, label_to_id=self.labels)
        save(done, {"completed_at_utc": now(), "source": expected,
                    "report_sha256": file_hashes(directory)})
        m = metrics[split]
        print(f"{split} {name}: {m['correct']}/{m['n']} macro F1={m['macro_f1']:.6f}", flush=True)
        return m

    def family_statistics(self, metrics):
        result = {}
        for family in self.families:
            values = [metrics[f"seed{s}-{family}"] for s in self.seeds]
            result[family] = {k: {"mean": mean(v[k] for v in values), "sample_sd": stdev(v[k] for v in values)}
                              for k in ("accuracy", "macro_f1", "loss")}
            result[family]["seeds"] = self.seeds
        return result

    def dev(self):
        self.check_inputs()
        self.runtime()
        results = {name: self.evaluate(name, "dev") for name in self.candidates}
        statistics = self.family_statistics(results)
        key = lambda n: (-results[n]["macro_f1"], results[n]["loss"], self.candidates.index(n))
        trained = [f"seed{s}-{e}" for s in self.seeds for e in self.experts]
        four_merges = [f"seed{s}-{r['id']}" for s in self.seeds for r in self.recipes] + [f"mean7-{r['id']}" for r in self.recipes]
        recipe_ids = [r["id"] for r in self.recipes]
        best_recipe = min(recipe_ids, key=lambda n: (-statistics[n]["macro_f1"]["mean"],
                           statistics[n]["loss"]["mean"], recipe_ids.index(n)))
        selection = {"identity": self.identity, "selection_split": "dev", "test_used": False,
            "best_overall": min(self.candidates, key=key), "best_trained_single": min(trained, key=key),
            "best_four_expert_merge": min(four_merges, key=key), "best_recipe_by_seven_seed_mean": best_recipe,
            "recipe_mean_checkpoint": f"mean7-{best_recipe}", "ranking": sorted(self.candidates, key=key),
            "dev_metrics": results, "seven_seed_dev_statistics": statistics,
            "checkpoint_sha256": {n: self.check_model(n) for n in self.candidates}}
        path = self.out / "selection.json"
        if path.exists():
            previous = read(path)
            require({k: v for k, v in previous.items() if k != "frozen_at_utc"} == selection,
                    "Frozen development selection differs; preserve prior run")
        else:
            require(not (self.out / "test-started.json").exists(), "Cannot select after test began")
            save(path, {"frozen_at_utc": now(), **selection})
        print("DEV SELECTION FROZEN: " + selection["best_overall"], flush=True)

    def test(self):
        self.check_inputs()
        self.runtime()
        selection = read(self.out / "selection.json")
        require(selection["identity"] == self.identity and selection["test_used"] is False, "Bad dev selection")
        freeze = digest(self.out / "selection.json")
        path = self.out / "test-started.json"
        if path.exists():
            require(read(path)["selection_sha256"] == freeze, "Selection changed after test started")
        else:
            save(path, {"at_utc": now(), "selection_sha256": freeze})
        results = {}
        for name in self.candidates:
            require(self.check_model(name) == selection["checkpoint_sha256"][name], "Post-selection checkpoint changed")
            results[name] = self.evaluate(name, "test")
        save(self.out / "test-summary.json", results)
        save(self.out / "seven-seed-statistics.json", {"dev": selection["seven_seed_dev_statistics"],
             "test": self.family_statistics(results), "baseline": "one common checkpoint; no replicated baseline SD",
             "weight_means": "mean7 models are separately evaluated checkpoints, not averages of scores"})
        selected = list(dict.fromkeys(["base-seed43", selection["best_overall"],
                       selection["best_trained_single"], selection["best_four_expert_merge"],
                       selection["recipe_mean_checkpoint"]]))
        for name in selected:
            self.evaluate(name, "train")
        require(digest(self.out / "selection.json") == freeze, "Selection changed during test")
        save(self.out / "completed.json", {"at_utc": now(), "identity": self.identity,
             "training_jobs": 28, "merged_checkpoints": 44, "evaluated_candidates": 73,
             "dev_test_prediction_rows": 73 * (46 + 50), "train_diagnostic_models": selected,
             "train_diagnostic_rows": len(selected) * 818, "selection_sha256": freeze,
             "runtime_adoption": False})

    def package(self):
        """全予測＋devで選んだ重みを主ZIPへ、全28学習重みはseed別ZIPへ。"""
        self.check_inputs()
        for seed in self.seeds:
            self.export_seed(seed)
        selected = []
        if (self.out / "selection.json").exists():
            selection = read(self.out / "selection.json")
            selected = list(dict.fromkeys([selection[k] for k in
                ("best_overall", "best_trained_single", "best_four_expert_merge", "recipe_mean_checkpoint")]))
        files = [self.root / n for n in self.inputs]
        files.append(self.root / "input-sha256.json")
        for p in self.out.rglob("*"):
            parts = p.relative_to(self.out).parts
            if not p.is_file() or p.name.endswith(".tmp") or any(n in parts for n in ("interrupted", "best", "checkpoint", "work")):
                continue
            if p.suffix in {".json", ".jsonl", ".md", ".csv", ".svg", ".txt", ".log"}:
                files.append(p)
        for name in selected:
            self.check_model(name)
            files.extend(p for p in self.checkpoint(name).iterdir() if p.is_file())
        files += [p for p in self.root.iterdir() if p.is_file() and p.suffix in {".json", ".txt", ".log"}]
        files += list((self.root / "exports").glob("weights-seed*.json"))
        complete = (self.out / "completed.json").exists() and (self.out / "report-completed.json").exists()
        if complete:
            marker = read(self.out / "report-completed.json")
            complete = marker["completed_sha256"] == digest(self.out / "completed.json") and all(
                (self.out / name).is_file() and digest(self.out / name) == expected
                for name, expected in marker["report_sha256"].items())
        filename = "modernbert-four-system-results.zip" if complete else "modernbert-four-system-partial.zip"
        record = archive_files(self.root, self.root / "exports" / filename, files)
        save(self.root / "exports/downloads.json", {"complete": complete, "main": record,
             "included_selected_models": selected,
             "all_trained_models": [read(self.root / "exports" / f"weights-seed{s}.json")
                                    for s in self.seeds if (self.root / "exports" / f"weights-seed{s}.json").exists()],
             "other_merged_weights": "Reconstructable from the seven seed archives, original common checkpoint and fixed merge recipes; not duplicated in the main ZIP"})
        print("RESULT ARCHIVE: " + record["file"], flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["all", "train", "merge", "dev", "test", "report", "package"])
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    exp = Experiment(args.root.resolve())
    try:
        if args.phase == "all":
            if not (exp.out / "identity.json").exists():
                require(shutil.disk_usage(exp.root).free >= 20 * 1024 ** 3,
                        "An initial 20 GiB free disk space is required for checkpoints and exports")
            for phase in ("train", "merge", "dev", "test"):
                getattr(exp, phase)()
            from build_report import build_report
            build_report(exp.root)
        elif args.phase == "report":
            from build_report import build_report
            build_report(exp.root)
        else:
            getattr(exp, args.phase)()
    except Exception:
        save(exp.out / "last-failure.json", {"at_utc": now(), "phase": args.phase, "traceback": traceback.format_exc()})
        raise


if __name__ == "__main__":
    main()
