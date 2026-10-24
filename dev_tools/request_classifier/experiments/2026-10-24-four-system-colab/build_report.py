"""保存済みの全予測を読み、単体・合成・seed間の比較を表示用資料にする。"""
from collections import Counter
import csv
import html
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def read(path):
    return json.loads(path.read_text())


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def group_metrics(predictions, groups):
    result = {}
    for name, labels in groups.items():
        gold = [r for r in predictions if r["gold"] in labels]
        result[name] = {"n": len(gold), "correct": sum(r["gold"] == r["predicted"] for r in gold),
                       "wrongly_entered": sum(r["gold"] not in labels and r["predicted"] in labels for r in predictions)}
    return result


def family_chart(path, statistics, baseline):
    """平均±標本SD。7seedの重み平均モデルの点数とは分けて表示する。"""
    height = 85 + 42 * len(statistics)
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="{height}" viewBox="0 0 1000 {height}">',
           '<rect width="100%" height="100%" fill="white"/>',
           '<g font-family="sans-serif" font-size="14" fill="#1f2937">',
           '<text x="20" y="25" font-size="18">Known test: macro F1 — seven-seed mean ± sample SD</text>']
    x0, width = 235, 570
    for tick in range(11):
        x = x0 + width * tick / 10
        svg += [f'<line x1="{x}" y1="45" x2="{x}" y2="{height-35}" stroke="#e5e7eb"/>',
                f'<text x="{x-8}" y="{height-12}">{tick / 10:.1f}</text>']
    bx = x0 + width * baseline
    svg += [f'<line x1="{bx}" y1="42" x2="{bx}" y2="{height-35}" stroke="#dc2626" stroke-dasharray="5 4"/>',
            f'<text x="820" y="48" fill="#b91c1c">base {baseline:.4f}</text>']
    for index, (name, values) in enumerate(statistics.items()):
        y = 65 + 42 * index
        m, s = values["macro_f1"]["mean"], values["macro_f1"]["sample_sd"]
        left, right, center = x0 + width * max(0, m - s), x0 + width * min(1, m + s), x0 + width * m
        svg += [f'<text x="18" y="{y+5}">{html.escape(name)}</text>',
                f'<line x1="{left}" y1="{y}" x2="{right}" y2="{y}" stroke="#2563eb" stroke-width="3"/>',
                f'<circle cx="{center}" cy="{y}" r="5" fill="#2563eb"/>',
                f'<text x="820" y="{y+5}">{m:.4f} ± {s:.4f}</text>']
    svg += ['</g></svg>']
    path.write_text("\n".join(svg) + "\n")


def build_report(root):
    out = root / "run"
    protocol = read(root / "training-protocol.json")
    completed = read(out / "completed.json")
    selection = read(out / "selection.json")
    stats = read(out / "seven-seed-statistics.json")
    labels = list(protocol["label_to_id"])
    names = protocol["candidates"]
    groups = {**protocol["experts"], "other": ["not_target"]}
    metrics, predictions, group_results, changes = {}, {}, {}, {}
    for name in names:
        metrics[name], predictions[name], group_results[name] = {}, {}, {}
        for split in ("dev", "test"):
            directory = out / "reports" / name / split
            metrics[name][split] = read(directory / "metrics.json")[split]
            predictions[name][split] = rows(directory / f"{split}-predictions.jsonl")
            group_results[name][split] = group_metrics(predictions[name][split], groups)
            require_ids = [r["id"] for r in predictions[names[0]][split]]
            if [r["id"] for r in predictions[name][split]] != require_ids:
                raise ValueError("Prediction row order differs")
            if sum(r["correct"] for r in predictions[name][split]) != metrics[name][split]["correct"]:
                raise ValueError("Saved correct count differs")
        base = predictions["base-seed43"]["test"]
        current = predictions[name]["test"]
        transitions = {k: [] for k in ("fixed", "new_errors", "still_wrong", "still_correct")}
        for before, after in zip(base, current, strict=True):
            if before["gold"] != after["gold"] or before["text"] != after["text"]:
                raise ValueError("Comparison inputs differ")
            key = ("still_correct" if after["correct"] else "new_errors") if before["correct"] else ("fixed" if after["correct"] else "still_wrong")
            transitions[key].append(after["id"])
        changes[name] = {key: {"count": len(value), "ids": value} for key, value in transitions.items()}
    save(out / "all-metrics.json", metrics)
    save(out / "group-metrics.json", group_results)
    save(out / "error-changes.json", changes)
    save(out / "candidate-checkpoints.json", {n: {"sha256": selection["checkpoint_sha256"][n],
         "kind": "baseline" if n == "base-seed43" else "seven_seed_weight_mean" if n.startswith("mean7-") else
         "trained" if n.rsplit("-", 1)[-1] in protocol["experts"] else "four_expert_merge"} for n in names})
    with (out / "comparison.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["candidate", "dev_correct", "dev_n", "dev_accuracy", "dev_macro_f1", "dev_loss",
                         "test_correct", "test_n", "test_accuracy", "test_macro_f1", "test_loss", "fixed_vs_base", "new_errors_vs_base"])
        for name in names:
            writer.writerow([name] + [metrics[name][s][k] for s in ("dev", "test")
                for k in ("correct", "n", "accuracy", "macro_f1", "loss")] +
                [changes[name]["fixed"]["count"], changes[name]["new_errors"]["count"]])
    with (out / "per-class.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["candidate", "split", "class", "precision", "recall", "f1", "support"])
        for name in names:
            for split in ("dev", "test"):
                for label in labels:
                    m = metrics[name][split]["per_class"][label]
                    writer.writerow([name, split, label] + [m[k] for k in ("precision", "recall", "f1", "support")])
    selected = list(dict.fromkeys(["base-seed43"] + [selection[k] for k in
        ("best_overall", "best_trained_single", "best_four_expert_merge", "recipe_mean_checkpoint")]))
    summary = ["# 通常対話・4系統×7seedの結果", "", "**既知の診断データによる比較。未知の実機精度ではありません。**", "",
        "28本は同じseed43 checkpointからの追加学習。7seedは学習順等の反復であり、7種類の基盤初期化ではありません。", "",
        "学習28本、4系統合成35本、同方式の7seed重み平均9本、元モデル1本の計73候補を比較しました。", "",
        "## devで固定した選択", "", "| 区分 | モデル |", "|---|---|",
        f"| 総合 | {selection['best_overall']} |", f"| 学習単体 | {selection['best_trained_single']} |",
        f"| 4系統の合成 | {selection['best_four_expert_merge']} |",
        f"| 7seedの平均dev成績で選んだ方式 | {selection['best_recipe_by_seven_seed_mean']} |",
        f"| その方式の7seed重み平均 | {selection['recipe_mean_checkpoint']} |", "",
        "最後の重み平均モデルは別途実測しています。7seedの点数の平均と同じとは限りません。自動的な本体採用は行いません。", "",
        "## 選択モデルと元モデル", "", "| モデル | dev正解/46 | dev F1 | test正解/50 | test F1 | 元から改善 | 新たな誤り |", "|---|---:|---:|---:|---:|---:|---:|"]
    for name in selected:
        d, t = metrics[name]["dev"], metrics[name]["test"]
        summary.append(f"| {name} | {d['correct']} | {d['macro_f1']:.4f} | {t['correct']} | {t['macro_f1']:.4f} | {changes[name]['fixed']['count']} | {changes[name]['new_errors']['count']} |")
    summary += ["", "## 7seedの平均と標本標準偏差", "", "平均±標本SD。すべてのseedを含みます。元モデルは1本なので反復のSDは付けません。", "",
        "| 系統・合成方式 | dev macro F1 | test macro F1 | test正解率 |", "|---|---:|---:|---:|"]
    for family in stats["test"]:
        d, t = stats["dev"][family], stats["test"][family]
        summary.append(f"| {family} | {d['macro_f1']['mean']:.4f} ± {d['macro_f1']['sample_sd']:.4f} | {t['macro_f1']['mean']:.4f} ± {t['macro_f1']['sample_sd']:.4f} | {t['accuracy']['mean']:.2%} ± {100*t['accuracy']['sample_sd']:.2f} pt |")
    family_chart(out / "seven-seed-f1.svg", stats["test"], metrics["base-seed43"]["test"]["macro_f1"])
    summary += ["", "![7seedの比較](seven-seed-f1.svg)", "", "## 系統別の正解数と誤流入（test）", "",
        "各欄は正解数/正例数、括弧内は他系統を誤ってこの系統へ分類した件数。視線内部の取り違えも不正解に含みます。", "",
        "| モデル | spatial | mount | sword | mob | other |", "|---|---|---|---|---|---|"]
    for name in selected:
        values = [group_results[name]["test"][g] for g in groups]
        summary.append("| " + name + " | " + " | ".join(f"{v['correct']}/{v['n']}（誤流入{v['wrongly_entered']}）" for v in values) + " |")
    summary += ["", "## 学習内の確認", "", "共有818件のうち今回の追加336件もすべてtrainです。以下は汎化の成績ではありません。", "",
        "| モデル | train正解/818 | 追加336件の正解 |", "|---|---:|---:|"]
    added_ids = {r["id"] for r in rows(root / protocol["additions"])}
    for name in completed["train_diagnostic_models"]:
        p = rows(out / "reports" / name / "train/train-predictions.jsonl")
        subset = [r for r in p if r["id"] in added_ids]
        if len(subset) != 336:
            raise ValueError("Added-material subset differs")
        summary.append(f"| {name} | {sum(r['correct'] for r in p)} | {sum(r['correct'] for r in subset)}/{len(subset)} |")
    summary += ["", "## 全73候補", "", "順番は実行前の候補順です。testを使って係数やseedを選び直していません。", "",
        "| モデル | dev正解 | dev F1 | test正解 | test F1 |", "|---|---:|---:|---:|---:|"]
    for name in names:
        d, t = metrics[name]["dev"], metrics[name]["test"]
        summary.append(f"| {name} | {d['correct']}/46 | {d['macro_f1']:.4f} | {t['correct']}/50 | {t['macro_f1']:.4f} |")
    summary += ["", "分類別precision/recall/F1は `per-class.csv`、混同行列と全予測・誤分類は `reports/<モデル>/<split>/` に保存しています。", "",
        "全28学習重みはseed別ZIPに保存。主結果ZIPにはdevで選んだ重みと全候補の成績・予測・出典が入ります。", ""]
    (out / "report.md").write_text("\n".join(summary))
    artifacts = ["report.md", "seven-seed-f1.svg", "all-metrics.json", "group-metrics.json",
                 "error-changes.json", "candidate-checkpoints.json", "comparison.csv", "per-class.csv"]
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    save(out / "report-completed.json", {"at_utc": datetime.now(timezone.utc).isoformat(),
        "completed_sha256": sha(out / "completed.json"),
        "report_sha256": {name: sha(out / name) for name in artifacts}})
    print("REPORT READY: run/report.md", flush=True)
