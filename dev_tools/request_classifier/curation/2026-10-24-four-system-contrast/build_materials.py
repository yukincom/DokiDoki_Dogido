"""既存16分類を保持し、対照教材と4専門分の学習ストリームを作る。学習はしない。"""
from collections import Counter
from difflib import SequenceMatcher
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]
REPO = LAB.parents[1]
OLD = LAB / "releases/2026-10-24-normal-dialogue-578"
OUT = LAB / "releases/2026-10-24-normal-dialogue-four-system"
ARCHIVED = LAB / "experiments/2026-10-24-normal-dialogue-colab/artifacts/colab-results"
HELPER = ARCHIVED / "dev_tools/request_classifier/dataset.py"
BASE = ARCHIVED / "weights/trial-2/best"
ANCHOR = "dcd20db"
SEEDS = [42, 43, 44, 45, 46, 47, 48]


def read(path):
    return json.loads(path.read_text())


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def save_rows(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in data))


def load_helper():
    provenance = read(LAB / "experiments/2026-10-24-normal-dialogue-colab/source-provenance.json")
    key = "dev_tools/request_classifier/dataset.py"
    assert digest(HELPER) == provenance["unchanged_training_sources_sha256"][key]
    spec = importlib.util.spec_from_file_location("reviewed_material_dataset", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def family(group):
    # 同型の方向置換・対照文を別splitへ分けない。例数を独立group数と混同しない。
    if group.startswith("relative_"):
        return "four_direction_parallel_family"
    if group in {"attention_photo", "attention_conversation", "attention_meeting", "attention_waiting"}:
        return "four_player_look_recall_family"
    if group.startswith("attention_"):
        return "four_scene_target_family"
    return "four_" + group


def label_reason(label):
    if label == "not_target":
        return "分類対象の現在の操作・モブ照会・注目誘導ではなく、本人の予定・未決定の相談・取消・一般知識・雑談を表す。文全体の意味で採った対照例。"
    if label == "dogido_recall":
        return "ドギド本人にプレイヤーのそばへ移動するよう求める。顔や視線だけを向ける依頼とは区別する。"
    if label == "look_player":
        return "プレイヤーへ顔・視線を向けるよう求める。プレイヤーの場所への移動とは区別する。"
    if label == "look_direction":
        return "プレイヤーと同じ方向にある現在の景色へ注意を向ける。広い遠景の共有を含む既存定義。"
    if label == "look_target":
        return "現在指している対象や目の前の物へ注意を向ける。指示だけでなく対象への問い・感想も含む既存定義。"
    if label.startswith("look_relative_"):
        return "指定した相対方向へ現在の注意を向ける。直接依頼だけでなく現在の変化による注目誘導も含む既存定義。"
    if label == "recall_mount":
        return "動物を今いる場所へ呼び寄せる依頼。実行できる種類・個体・位置の検査は分類後のコードの担当。"
    if label == "equip_sword":
        return "現在の手持ちを剣へ変更するよう相手に依頼する。本人の操作予定や道具の説明とは区別する。"
    return "現在把握しているモブの存在・種類・数・位置・距離を尋ねる。友好モブも含み、一般知識や現在の対象への注目誘導とは区別する。"


def main():
    helper = load_helper()
    original, labels = helper.read_material_splits(OLD)
    baseline = read(OLD / "data_report.json")
    for split in original:
        assert digest(OLD / f"{split}.jsonl") == baseline[split]["sha256"]
    assert len(original["train"]) == 482 and len(labels) == 16
    focus = {"spatial": [k for k in labels if k.startswith("look_") or k == "dogido_recall"],
             "mount": ["recall_mount"], "sword": ["equip_sword"], "mob": ["enemy_information"]}
    authored = read(HERE / "authored-groups.json")
    additions, evidence = [], []
    contrast_ids = {name: set() for name in focus}
    for group_index, group in enumerate(authored, 1):
        related = [name for name, target in focus.items() if any(label in target for label, _ in group["rows"])]
        assert related
        for row_index, (label, text) in enumerate(group["rows"], 1):
            ident = f"four_contrast_20261024_{group_index:02}_{row_index:02}"
            row = {"id": ident, "group_id": family(group["group"]), "text": text,
                   "context": [], "label": label, "needs_clarification": False,
                   "tags": ["contrast", group["group"]], "source": "synthetic",
                   "rationale": label_reason(label) + " 対照: " + group["boundary"],
                   "source_keys": ["four_system_authored:" + ident]}
            additions.append(row)
            evidence.append({"id": ident, "source": "Codex authored synthetic; not user/live speech",
                             "authored_group": group["group"], "split_group": row["group_id"],
                             "contrast_experts": related, "boundary": group["boundary"]})
            for name in related:
                contrast_ids[name].add(ident)
    reused = read(HERE / "reused-evidence.json")
    for source in reused["rows"]:
        ident = "legacy__" + source["source_id"]
        row = {"id": ident, "group_id": source["group_id"], "text": source["text"],
               "context": [], "label": "not_target", "needs_clarification": False,
               "tags": ["contrast", "restored_train_material"], "source": source["source"],
               "rationale": source["readoption_reason"], "source_keys": [source["source_key"]]}
        additions.append(row)
        evidence.append({"id": ident, "source": "reused old training material, original text",
                         "split_group": row["group_id"], "contrast_experts": source["contrast_experts"]})
        for name in source["contrast_experts"]:
            contrast_ids[name].add(ident)
    all_train = original["train"] + additions
    combined = {**original, "train": all_train}
    helper.validate_material_splits(combined, labels)
    heldout = original["dev"] + original["test"]
    forbidden_keys = {key for row in heldout for key in row["source_keys"]}
    assert not forbidden_keys.intersection(key for row in additions for key in row["source_keys"])
    # 基準releaseをそのまま先頭に保持。dev/testとlabel対応もバイト単位で維持する。
    OUT.mkdir(parents=True, exist_ok=True)
    for name in ("dev.jsonl", "test.jsonl", "labels.json"):
        shutil.copyfile(OLD / name, OUT / name)
    save_rows(OUT / "train.jsonl", all_train)
    assert (OUT / "train.jsonl").read_bytes().startswith((OLD / "train.jsonl").read_bytes())
    save_rows(HERE / "candidates.jsonl", additions)
    save_rows(HERE / "evidence.jsonl", evidence)
    save(OUT / "data_report.json", helper.report(combined, OUT, labels))
    inventory = {}
    for name, target in focus.items():
        # 既存pilotのjointと同じ行連結方式。原文・ID・教師値を変えず再提示回数を指定。
        replay = [r for r in all_train if r["label"] in target or r["id"] in contrast_ids[name]]
        stream = all_train + replay
        folder = OUT / "experts" / name
        save_rows(folder / "train.jsonl", stream)
        save_rows(folder / "replay.jsonl", replay)
        frequencies = Counter(r["id"] for r in stream)
        assert set(frequencies) == {r["id"] for r in all_train}
        assert set(frequencies.values()) <= {1, 2}
        assert stream[:len(all_train)] == all_train
        counts = Counter(r["label"] for r in stream)
        inventory[name] = {"focus_labels": target, "unique_rows": len(all_train),
            "focus_unique_rows": sum(r["label"] in target for r in all_train),
            "not_target_unique_rows": sum(r["label"] == "not_target" for r in all_train),
            "replay_occurrences": len(replay), "training_occurrences_per_epoch": len(stream),
            "focus_occurrences": sum(counts[label] for label in target),
            "not_target_occurrences": counts["not_target"], "label_occurrences": dict(counts),
            "train_sha256": digest(folder / "train.jsonl"),
            "replay_sha256": digest(folder / "replay.jsonl")}
        save(folder / "inventory.json", inventory[name])
    save(OUT / "expert-inventory.json", inventory)
    protocol = {"status": "materials prepared; training not run", "source_git_checkpoint": ANCHOR,
        "label_to_id": labels, "experts": focus, "common_other": "not_target",
        "seed_plan": SEEDS, "planned_specialist_runs": len(SEEDS) * len(focus),
        "seed_scope": "training/data order randomness; material split and row membership fixed",
        "initialization_for_merge": {"path": str(BASE.relative_to(REPO)),
            "model_sha256": digest(BASE / "model.safetensors"), "existing_seed": 43,
            "policy": "Within all four experts and seven repeats use the same saved starting tensors, including classifier head. Do not create independent heads and average them.",
            "limitation": "Base already trained on the original 482 rows. Seven seeds do not represent seven fresh base-model initializations."},
        "input": {"context": 0, "max_length": 128, "output_labels": 16,
            "metadata_is_not_model_input": True},
        "training_stream": "Every unique shared row once, then focus rows and linked contrast rows once more; concatenate before the normal seeded training shuffle. No replacement sampling and no third copy.",
        "consumer": "Reuse EncodedRows in the archived train.py and the row-concatenation route used by the previous specialist pilot. Expert train.jsonl is an occurrence stream with intentional repeated IDs, not a unique-row --material-release. Shared root is a valid material release.",
        "comparison": "The same seven seeds are paired across four experts. Report all seeds, mean and sample SD. Split never changes with seed. Merge only compatible checkpoints from the same declared starting state.",
        "selection": "Freeze any training hyperparameters and merge candidates before running. Select with dev only, fix selection before test. No seed cherry-picking from test.",
        "evaluation_scope": "Existing dev 46 / test 50 were already used in material design; diagnostic only. No new independent heldout or real-world accuracy evidence created.",
        "training_hyperparameters": "Not selected or run in this materials-only step; keep separate from the seed and dataset contract.",
        "runtime_adoption": False}
    save(OUT / "training-plan.json", protocol)
    save(OUT / "training-jobs.json", [
        {"seed": seed, "data_seed": seed, "expert": name,
         "train": f"experts/{name}/train.jsonl", "dev": "dev.jsonl", "test": "test.jsonl",
         "labels": "labels.json", "initial_state": protocol["initialization_for_merge"],
         "status": "prepared_not_run"}
        for seed in SEEDS for name in focus])
    # 近さは漏れの点検用。意味判定・自動除外・goldの推定には使わない。
    near = []
    old_rows = [(split, row) for split, data in original.items() for row in data]
    for row in additions:
        key = helper.normalized(row["text"])
        best = max(((SequenceMatcher(None, key, helper.normalized(old["text"])).ratio(), split, old)
                    for split, old in old_rows), key=lambda x: x[0])
        if best[0] >= .72:
            near.append({"id": row["id"], "nearest_id": best[2]["id"], "nearest_split": best[1],
                         "ratio": best[0], "same_label": row["label"] == best[2]["label"]})
    save(HERE / "similarity-review.json", {"method": "SequenceMatcher on existing normalized strings; no text generated from heldout", "threshold": .72, "candidates": near})
    save(HERE / "manifest.json", {"authored_rows": sum(len(g["rows"]) for g in authored),
        "authored_topic_groups": len(authored), "authored_split_groups": len({family(g["group"]) for g in authored}),
        "reused_rows": len(reused["rows"]), "additions": len(additions),
        "addition_label_counts": dict(Counter(r["label"] for r in additions)),
        "source_sha256": {p.name: digest(p) for p in (HERE / "authored-groups.json", HERE / "reused-evidence.json", Path(__file__))},
        "base_release_sha256": {n: digest(OLD / n) for n in ("train.jsonl", "dev.jsonl", "test.jsonl", "labels.json")},
        "shared_release_sha256": {n: digest(OUT / n) for n in ("train.jsonl", "dev.jsonl", "test.jsonl", "labels.json")},
        "data_helper_sha256": digest(HELPER), "integrity": {"existing_train_preserved": True,
            "dev_test_labels_byte_identical": True, "all_16_labels_in_every_expert": True,
            "shared_normalized_duplicates": 0, "cross_split_groups": 0,
            "new_source_key_overlap_with_heldout": 0},
        "quality_review": "Parent authored/read review plus independent Codex read-only review; synthetic annotation, no user/live label verification or model evaluation"})
    review = ["# 追加する対照教材（2026-10-24）", "", "新作はすべて合成例。既存8件は原文を保持。全件train用で、未見評価ではない。", ""]
    for group_index, group in enumerate(authored, 1):
        review += [f"## {group_index}. {group['boundary']}", "", "| 分類 | 発話 |", "|---|---|"]
        review += [f"| `{label}` | {text} |" for label, text in group["rows"]]
        review.append("")
    review += ["## 旧教材からの再利用", "", "| 元ID | 分類 | 原文 |", "|---|---|---|"]
    review += [f"| {r['source_id']} | not_target | {r['text']} |" for r in reused["rows"]]
    (HERE / "materials-review.md").write_text("\n".join(review) + "\n")
    print(json.dumps({"unique_train": len(all_train), "additions": len(additions),
        "experts": {n: v["training_occurrences_per_epoch"] for n, v in inventory.items()},
        "similarity_candidates": len(near)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
