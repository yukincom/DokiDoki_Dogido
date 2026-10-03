"""Archive the earlier fifteen-case Rust probes without calling a model again."""
import copy
from pathlib import Path

from .core import append_json, digest, inspect_output, now, read_json, read_jsonl, safe_id, write_json


def import_probe(lab, source: Path, identifier: str, title: str, model_name: str, candidate_id: str):
    identifier = safe_id(identifier)
    directory = lab.runs / identifier
    if directory.exists():
        raise ValueError("取り込み先が存在します。上書きしません。")
    meta = read_json(source / "metadata.json")
    original = read_jsonl(source / "results.jsonl")
    parts = copy.deepcopy(lab.canonical()["components"])
    parts["base"] = meta["candidate_base"]
    first = read_json(source / "01-cow.request.json")["messages"][0]["content"]
    after_base = first.removeprefix(parts["base"] + "\n")
    parts["normal"] = after_base.split("\n" + parts["grounding"], 1)[0]
    assert "\n".join(parts[p] for p in ("base", "normal", "grounding", "dialogue", "choice_contract")) == first
    candidate = {"schema_version": 1, "id": candidate_id, "title": title, "author": "User / Codex",
                 "hypothesis": "過去の実測。base末尾の動物・怖がり段落は、当時の試験と同様に含めていない。",
                 "components": parts, "body_mode": "production", "response_format": "speech_json"}
    requests, rows = [], []
    for index, old in enumerate(original):
        request = read_json(source / Path(old["request_path"]).name)
        payload = {"model": request["model"], "messages": request["messages"],
                   "temperature": request["temperature"], "max_tokens": request["max_tokens"],
                   "stream": False, "chat_template_kwargs": {"enable_thinking": request["enable_thinking"]}}
        row = {"index": index, "case_id": old["mob"], "repeat": 1, "label": old["label"], "group": old["group"],
               "request_sha256": digest(payload), "metrics": {"elapsed_ms": old.get("elapsed_ms"),
                    "completion_tokens": old.get("completion_tokens"), "ttft_ms": None, "cpu_percent": None,
                    "peak_rss_bytes": None, "peak_mlx_bytes": None,
                    "tokens_per_second": 1000 * old["completion_tokens"] / old["elapsed_ms"]
                         if old.get("completion_tokens") and old.get("elapsed_ms") else None}}
        requests.append({k: row[k] for k in ("index", "case_id", "repeat", "label", "group", "request_sha256")} |
                        {"payload": payload, "source_request": request})
        if old.get("error"):
            row["error"] = old["error"]
        else:
            row.update(raw_output=old["raw_output"], finish_reason=old.get("finish_reason"), response_model=old.get("response_model"))
            row.update(inspect_output(old["raw_output"], watch_terms=["あら", "模样", "直视", "ホストムブ"]))
            report = source / Path(old["report_path"]).name
            row["raw_response"] = read_json(report) if report.exists() else None
        rows.append(row)
    manifest = {"schema_version": 1, "id": identifier, "created_at": now(), "status": "imported",
                "case_count": len(requests), "candidate": candidate, "candidate_sha256": digest(candidate),
                "model": {"id": "imported", "backend": "http", "title": model_name, "model": meta["model"],
                          "base_url": meta["base_url"]}, "settings": {"temperature":meta["temperature"],
                          "max_tokens":meta["max_tokens"], "enable_thinking":False, "repeats":1, "seed":None},
                "requests_sha256": digest(requests), "source_metadata": meta, "source_directory": str(source),
                "measurement_scope": "Earlier Rust HTTP probe. CPU/RSS/TTFT were not measured per case; no guessed values.",
                "scope": "isolated speech generation; no planner, TTS, Minecraft or production writes"}
    write_json(directory / "manifest.json", manifest)
    write_json(directory / "requests.json", requests)
    metrics = source / "process_metrics.json"
    if metrics.exists():
        write_json(directory / "whole_process_metrics.json", read_json(metrics))
    for row in rows:
        append_json(directory / "results.jsonl", row)
    (directory / "report.html").write_text(lab.export_html(identifier), encoding="utf-8")
    return candidate
