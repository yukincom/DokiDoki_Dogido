"""初回だけ、収集済みの実発話から編集用CSVを作る。既存CSVは上書きしない。"""
import argparse
import csv
import importlib.util
import json
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
REPO = SOURCE.parents[1]


def prepare(project):
    spec = importlib.util.spec_from_file_location("training_builder", SOURCE / "builder.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    classifier = project / "dev_tools/request_classifier"
    workspace = classifier / "教材作成"
    workspace.mkdir(exist_ok=True)
    targets = [workspace / name for name in ("追加データ.csv", "空のテンプレート.csv", "分類一覧.csv")]
    if any(path.exists() for path in targets):
        raise SystemExit("編集用CSVが既にあります。既存の入力は上書きしません。")
    names = {}
    for name, label in builder.LABEL_NAMES.items():
        names.setdefault(label, name)
    rows = []
    for day in ("2026-10-28", "2026-10-30"):
        folder = classifier / "curation" / (day + "-live-feedback")
        evidence = {row["id"]: row for row in map(json.loads, (folder / "evidence.jsonl").read_text().splitlines())}
        for row in map(json.loads, (folder / "candidates.jsonl").read_text().splitlines()):
            original = evidence[row["id"]]
            notes = ["記録ID: " + row["id"]]
            for display in original["classifier_displays"]:
                notes.append("元の分類: " + display["label"] + " / 確率: " + str(display["confidence_display"]) + " / 推論ms: " + str(display["latency_ms"]))
            if original.get("input_time"):
                notes.append("時刻: " + original["input_time"])
            notes.append(row["rationale"])
            rows.append(["保留" if row["label"] == "look_relative_up" else "はい",
                         "通常" if row["classifier_mode"] == "normal" else "川柳", row["text"],
                         names.get(row["label"], row["label"]), " / ".join(notes), row["group_id"]])
    with targets[0].open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(builder.HEADERS)
        writer.writerows(rows)
    with targets[1].open("w", encoding="utf-8-sig", newline="") as stream:
        csv.writer(stream, lineterminator="\n").writerow(builder.HEADERS)
    with targets[2].open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["モード", "正解分類", "機械名", "備考"])
        for mode, release in (("通常", "2026-10-24-normal-dialogue-four-system"), ("川柳", "2026-10-24-senryu-only-424")):
            labels = json.loads((classifier / "releases" / release / "labels.json").read_text())
            if mode == "通常":
                labels.update(look_relative_up=16, look_relative_down=17)
            for label in labels:
                writer.writerow([mode, names.get(label, label), label,
                                 "上下は両方の学習例が必要。本体採用は別途。" if label in ("look_relative_up", "look_relative_down") else ""])
    print(f"{workspace}: {len(rows)}件の編集用CSVを作成しました。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=REPO)
    args = parser.parse_args()
    prepare(args.project_root.resolve())
