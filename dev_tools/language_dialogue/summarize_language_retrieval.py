"""Export blinded reply review packets; preserve condition mapping separately."""

import argparse
import json
from pathlib import Path
import random
from collections import Counter
import math
import statistics


def export_review(folder):
    rows = [json.loads(line) for line in (folder / "results.jsonl").read_text().splitlines()]
    groups = {"regression": [], "new_first": [], "new_second": []}
    mapping = {}
    evidence = []
    for row in rows:
        case = row["case"]
        group = "regression" if row["cohort"] == "regression" else (
            "new_first" if int(row["case_id"].split("_")[1]) <= 15 else "new_second")
        texts = {}
        for variant, result in row["variants"].items():
            texts.setdefault(result["audited_reply"], []).append(variant)
        for variant, result in row["probes"].items():
            if "not_run" not in result:
                texts.setdefault(result["result"].get("text", ""), []).append(variant)
        entries = list(texts.items())
        random.Random("retrieval-blind-" + row["case_id"]).shuffle(entries)
        answers = []
        for index, (text, variants) in enumerate(entries, 1):
            answer_id = f"{row['case_id']}-r{index}"
            answers.append({"answer_id": answer_id, "text": text})
            mapping[answer_id] = {"case_id": row["case_id"], "variants": variants}
        groups[group].append({"case_id": row["case_id"], "question": case["text"],
            "history": case.get("history", []), "review": case.get("review", case.get("acceptable_answer", "")),
            "unacceptable_claims": case.get("unacceptable_claims", []),
            "answers": answers})
        evidence.append({"case_id": row["case_id"], "facts": row["preparation"]["record"]["search"]["facts"],
                         "review_sources": case.get("review_sources", [])})
    review = folder / "blind_review"
    review.mkdir(exist_ok=True)
    for group, cases in groups.items():
        (review / f"{group}.json").write_text(json.dumps({"cases": cases}, ensure_ascii=False, indent=2) + "\n")
    (review / "condition_mapping.json").write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + "\n")
    (review / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"case_counts": {k: len(v) for k, v in groups.items()}, "unique_answers": len(mapping)}))


def distribution(values):
    values = sorted(values)
    if not values:
        return {"n": 0}
    return {"n": len(values), "median_ms": round(statistics.median(values), 3),
            "mean_ms": round(statistics.mean(values), 3),
            "p95_ms": round(values[math.ceil(len(values) * .95) - 1], 3), "max_ms": round(max(values), 3)}


def analyze(folder):
    rows = [json.loads(line) for line in (folder / "results.jsonl").read_text().splitlines()]
    review = folder / "blind_review"
    mapping = json.loads((review / "condition_mapping.json").read_text())
    grades = {}
    for path in review.glob("grades_*.json"):
        for entry in json.loads(path.read_text())["reviews"]:
            assert entry["answer_id"] not in grades
            grades[entry["answer_id"]] = entry
    mapped = {}
    for answer_id, data in mapping.items():
        if answer_id in grades:
            for variant in data["variants"]:
                mapped[data["case_id"], variant] = grades[answer_id]
    result = {"reviewed_unique_answers": len(grades), "total_unique_answers": len(mapping),
              "cohorts": {}, "timing": {}, "changes": [], "probe_evidence_pairs": {}}
    for cohort in ("regression", "new", "all"):
        selected = [r for r in rows if cohort == "all" or r["cohort"] == cohort]
        section = {"cases": len(selected), "reached_reply": sum(r["reached_reply_stage"] for r in selected)}
        for variant in ("A", "B", "C", "D"):
            replies = [r for r in selected if r["reached_reply_stage"]] if variant in {"A", "B"} else [
                r for r in selected if variant in r["probes"] and "not_run" not in r["probes"][variant]]
            section[variant] = {"generated": len(replies), "grades": dict(Counter(
                mapped.get((r["case_id"], variant), {}).get("grade", "not_reviewed") for r in replies))}
            if variant in {"A", "B"}:
                section[variant]["web_intentions"] = sum(bool(r["variants"][variant]["web_dispatches"]) for r in selected)
        result["cohorts"][cohort] = section
    for label in ("interpretation", "local_search", "A", "B", "C", "D"):
        if label == "interpretation":
            values = [r["preparation"]["calls"][0]["duration_ms"] for r in rows]
        elif label == "local_search":
            values = [c["duration_ms"] for r in rows for c in r["preparation"]["search_calls"]]
        elif label in {"A", "B"}:
            values = [r["variants"][label]["reply_duration_ms"] for r in rows if r["reached_reply_stage"]]
        else:
            values = [r["probes"][label]["duration_ms"] for r in rows if label in r["probes"] and "not_run" not in r["probes"][label]]
        result["timing"][label] = distribution(values)
    for variant in ("A", "B"):
        values = [r["preparation"]["calls"][0]["duration_ms"] + sum(c["duration_ms"] for c in r["preparation"]["search_calls"]) +
                  r["variants"][variant]["reply_duration_ms"] for r in rows if r["reached_reply_stage"]]
        result["timing"][f"{variant}_shared_preparation_plus_reply"] = distribution(values)
    pairs = [r for r in rows if "D" in r["probes"] and "not_run" not in r["probes"]["D"]]
    result["probe_evidence_pairs"]["n"] = len(pairs)
    for variant in ("C", "D"):
        result["probe_evidence_pairs"][variant] = dict(Counter(mapped.get((r["case_id"], variant), {}).get("grade", "not_reviewed") for r in pairs))
        result["timing"][variant + "_paired"] = distribution([r["probes"][variant]["duration_ms"] for r in pairs])
    for row in rows:
        a = row["variants"]["A"]
        b = row["variants"]["B"]
        if bool(a["web_dispatches"]) != bool(b["web_dispatches"]) or (
            mapped.get((row["case_id"], "A"), {}).get("grade") != mapped.get((row["case_id"], "B"), {}).get("grade")
        ):
            result["changes"].append({"case_id": row["case_id"], "A_web": bool(a["web_dispatches"]),
                "B_web": bool(b["web_dispatches"]), "A_grade": mapped.get((row["case_id"], "A")),
                "B_grade": mapped.get((row["case_id"], "B"))})
    result["backend_attempts"] = sum(len(r["preparation"]["attempts"]) +
        sum(len(v["attempts"]) for v in r["variants"].values()) +
        sum(len(p.get("attempts", [])) for p in r["probes"].values()) for r in rows)
    result["unknown_probe_fact_ids"] = [{"case_id": r["case_id"], "variant": v, "ids": p["unknown_fact_ids"]}
        for r in rows for v, p in r["probes"].items() if p.get("unknown_fact_ids")]
    result["reply_rejections"] = [{"case_id": r["case_id"], "variant": v, "reason": p["record"].get("reply_status")}
        for r in rows if r["reached_reply_stage"] for v, p in r["variants"].items() if p["record"].get("reply_status") != "accepted"]
    (folder / "analysis.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--analyze", action="store_true")
    args = parser.parse_args()
    analyze(args.folder) if args.analyze else export_review(args.folder)
