"""Shared, file-based experiment inputs and results. No inference on import."""
from __future__ import annotations

import copy
import fcntl
import hashlib
import html
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COMPONENTS = ("base", "normal", "tension", "grounding", "dialogue", "choice_contract")
WATCH_TERMS = ["あら", "模样", "直视", "ホストムブ"]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def append_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
        stream.flush()
        fcntl.flock(stream, fcntl.LOCK_UN)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    # A reader can arrive between a writer's write and flush; ignore only its last partial line.
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    result = []
    for index, line in enumerate(lines):
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            if index != len(lines) - 1 or line.endswith("\n"):
                raise
    return result


def safe_id(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}", value):
        raise ValueError("IDは英数字・ハイフン・アンダースコアで指定してください。")
    return value


@contextmanager
def exclusive(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("別の試験が実行中です。完了後に実行してください。") from exc
        try:
            yield stream
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def inspect_output(raw: str, expected: str = "speech_json", watch_terms=None) -> dict:
    parsed = None
    error = None
    if expected == "text":
        speech, valid = raw, None
    else:
        try:
            parsed = json.loads(raw)
            valid = (
                isinstance(parsed, dict) and set(parsed) == {"action", "speech"}
                and parsed.get("action") in ("speak", "silent")
                and isinstance(parsed.get("speech"), str)
                and ((parsed["action"] == "silent" and parsed["speech"] == "")
                     or (parsed["action"] == "speak" and bool(parsed["speech"].strip())))
            )
            speech = parsed.get("speech", raw) if isinstance(parsed, dict) else raw
            if not isinstance(speech, str):
                speech = raw
            if not valid:
                error = "action/speech の形が契約と一致しません。"
        except (json.JSONDecodeError, TypeError):
            valid, speech, error = False, raw, "JSONとして読めません。"
    return {"parsed": parsed, "speech": speech, "format_valid": valid, "format_error": error,
            "watch_hits": {term: speech.count(term) for term in (watch_terms or []) if term in speech}}


def summary(rows: list[dict]) -> dict:
    good = [row for row in rows if not row.get("error")]
    def mean(key):
        values = [r.get("metrics", {}).get(key) for r in good]
        values = [x for x in values if isinstance(x, (int, float))]
        return statistics.mean(values) if values else None
    def maximum(key):
        values = [r.get("metrics", {}).get(key) for r in good]
        values = [x for x in values if isinstance(x, (int, float))]
        return max(values) if values else None
    return {"count": len(rows), "errors": len(rows) - len(good),
            "format_ok": sum(r.get("format_valid") is True for r in good),
            "format_checked": sum(r.get("format_valid") is not None for r in good),
            "watch_cases": sum(bool(r.get("watch_hits")) for r in good),
            "mean_elapsed_ms": mean("elapsed_ms"), "mean_ttft_ms": mean("ttft_ms"),
            "mean_tokens_per_second": mean("tokens_per_second"),
            "mean_cpu_percent": mean("cpu_percent"),
            "peak_rss_bytes": maximum("peak_rss_bytes"),
            "peak_mlx_bytes": maximum("peak_mlx_bytes")}


def cache_root() -> Path:
    return Path(os.environ.get("HF_HUB_CACHE") or os.environ.get("HUGGINGFACE_HUB_CACHE")
                or str(Path(os.environ.get("HF_HOME", Path.home() / ".cache/huggingface")) / "hub"))


def cached_model(repo_id: str) -> dict:
    root = cache_root() / ("models--" + repo_id.replace("/", "--"))
    ref = root / "refs/main"
    snapshots = []
    if ref.exists() and re.fullmatch(r"[a-f0-9]{40,64}", ref.read_text().strip()):
        snapshots.append(root / "snapshots" / ref.read_text().strip())
    snapshots.extend(sorted((root / "snapshots").glob("*"), reverse=True))
    for path in dict.fromkeys(snapshots):
        config = path / "config.json"
        if not config.is_file():
            continue
        index = path / "model.safetensors.index.json"
        weights = (set(read_json(index).get("weight_map", {}).values()) if index.exists()
                   else {f.name for f in path.glob("*.safetensors")})
        if not weights or not all((path / filename).is_file() for filename in weights):
            continue
        data = read_json(config)
        quant = data.get("quantization", data.get("quantization_config", {})) or {}
        return {"available": True, "local_path": str(path), "revision": path.name,
                "weights_bytes": sum((path / f).stat().st_size for f in weights),
                "model_type": data.get("model_type"),
                "quantization": {k: quant[k] for k in ("bits", "group_size", "mode") if k in quant}}
    return {"available": False, "local_path": None, "weights_bytes": None}


class Lab:
    def __init__(self, repo: Path | None = None, directory: Path | None = None):
        self.directory = (directory or Path(__file__).parent).resolve()
        self.repo = (repo or self.directory.parents[1]).resolve()
        self.runs = self.directory / "runs"

    def canonical(self) -> dict:
        data = read_json(self.repo / "dogido_server/llm/companion_prompts.json")
        parts = {k: (data["modes"]["normal"] if k == "normal"
                     else data["modes"][k] if k == "tension" else data[k]) for k in COMPONENTS}
        return {"schema_version": 1, "id": "canonical", "title": "採用中のプロンプト（読み取り専用）",
                "author": "本体", "hypothesis": "現在の正本。試験はコピーを使い、正本へ書き戻しません。",
                "components": parts, "body_mode": "production", "response_format": "speech_json"}

    def candidate(self, identifier: str) -> dict:
        if identifier == "canonical":
            return self.canonical()
        result = read_json(self.directory / "candidates" / (safe_id(identifier) + ".json"))
        return self.validate_candidate(result)

    def candidates(self) -> list[dict]:
        result = [self.canonical()]
        for path in sorted((self.directory / "candidates").glob("*.json")):
            result.append(self.validate_candidate(read_json(path)))
        return [{**c, "etag": digest(c)} for c in result]

    @staticmethod
    def validate_candidate(value: dict) -> dict:
        result = copy.deepcopy(value)
        if result.get("schema_version") != 1:
            raise ValueError("candidate.schema_version は 1 を指定してください。")
        safe_id(result.get("id"))
        if not isinstance(result.get("title"), str) or not result["title"].strip():
            raise ValueError("候補名が必要です。")
        parts = result.get("components")
        if not isinstance(parts, dict) or set(parts) != set(COMPONENTS):
            raise ValueError("components は base/normal/tension/grounding/dialogue/choice_contract の6項目です。")
        if not all(isinstance(v, str) for v in parts.values()):
            raise ValueError("プロンプトの各項目は文字列で指定してください。")
        if result.get("body_mode") not in ("production", "minimal"):
            raise ValueError("body_mode は production または minimal です。")
        if result.get("response_format") not in ("speech_json", "text"):
            raise ValueError("response_format は speech_json または text です。")
        result.pop("etag", None)
        return result

    def save_candidate(self, value: dict, expected_etag: str | None = None) -> dict:
        value = self.validate_candidate(value)
        if value["id"] == "canonical":
            raise ValueError("本体の正本は編集しません。別IDで候補を保存してください。")
        path = self.directory / "candidates" / (value["id"] + ".json")
        with exclusive(self.directory / "runs/.candidate.lock"):
            if path.exists():
                current = read_json(path)
                if not expected_etag or digest(self.validate_candidate(current)) != expected_etag:
                    raise ValueError("別の人が候補を更新しました。読み直すか別IDで保存してください。")
            elif expected_etag:
                raise ValueError("更新対象の候補が見つかりません。")
            write_json(path, value)
        return {**value, "etag": digest(value)}

    def models(self) -> list[dict]:
        profiles = read_json(self.directory / "models.json")
        for model in profiles:
            if model["backend"] == "mlx":
                model.update(cached_model(model["model"]))
                model.setdefault("python", str(self.repo / "dogido-llm/bin/python"))
        return profiles

    def suites(self) -> list[dict]:
        return [read_json(p) for p in sorted((self.directory / "suites").glob("*.json"))]

    def suite(self, identifier: str) -> dict:
        data = read_json(self.directory / "suites" / (safe_id(identifier) + ".json"))
        if not data.get("cases"):
            raise ValueError("試験ケースが空です。")
        ids = [safe_id(c.get("id")) for c in data["cases"]]
        if len(ids) != len(set(ids)) or any(not isinstance(c.get("details"), dict) for c in data["cases"]):
            raise ValueError("case IDの重複またはdetailsの形式不正です。")
        return data

    def build_requests(self, candidate: dict, suite: dict, model: dict, settings: dict) -> list[dict]:
        parts = candidate["components"]
        result = []
        for repeat in range(settings["repeats"]):
            for index, case in enumerate(suite["cases"]):
                details = copy.deepcopy(case["details"])
                details["dialogue_choice"] = True
                mode = details.get("character_mode", "normal")
                if mode not in ("base", "normal", "tension"):
                    raise ValueError("この試験ツールのcharacter_modeは base/normal/tension です。")
                details["character_mode"] = mode
                system = "\n".join(v for v in (
                    parts["base"], parts.get(mode, "") if mode != "base" else "",
                    parts["grounding"], parts["dialogue"], parts["choice_contract"]
                ) if v)
                if candidate["body_mode"] == "production":
                    binary = self.repo / "dogido-rust/target/release/dogido-rust"
                    with tempfile.NamedTemporaryFile(mode="w+", suffix=".json", encoding="utf-8") as request:
                        json.dump(details, request, ensure_ascii=False)
                        request.flush()
                        output = subprocess.run([str(binary), "build-chat-prompt", request.name],
                                                check=True, capture_output=True, text=True, timeout=10)
                    messages = json.loads(output.stdout)
                    messages[0]["content"] = system
                else:
                    messages = [{"role": "system", "content": system}, {"role": "user", "content":
                        f"プレイヤー:「{details.get('user_text', '')}」\n"
                        f"観測: {details.get('observation_summary', '')}"}]
                seed = settings["seed"]
                if seed is not None:
                    seed += repeat * len(suite["cases"]) + index
                payload = {"model": model["model"], "messages": messages,
                           "temperature": settings["temperature"], "max_tokens": settings["max_tokens"],
                           "stream": settings["stream"],
                           "chat_template_kwargs": {"enable_thinking": settings["enable_thinking"]}}
                if seed is not None:
                    payload["seed"] = seed
                if settings["top_p"] is not None:
                    payload["top_p"] = settings["top_p"]
                result.append({"index": len(result), "case_id": case["id"], "repeat": repeat + 1,
                               "label": case.get("label", case["id"]), "group": case.get("group", ""),
                               "details": details, "payload": payload,
                               "request_sha256": digest(payload)})
        return result

    def prepare(self, spec: dict) -> dict:
        settings = {"temperature": 0.65, "max_tokens": 384, "repeats": 1, "seed": None,
                    "top_p": None, "enable_thinking": False, "stream": False,
                    "timeout_seconds": 60, "watch_terms": WATCH_TERMS, **spec.get("settings", {})}
        for k in ("max_tokens", "repeats", "timeout_seconds"):
            if type(settings[k]) is not int or settings[k] <= 0:
                raise ValueError(f"{k} は正の整数です。")
        if settings["seed"] is not None and type(settings["seed"]) is not int:
            raise ValueError("seed は整数または null です。")
        if not isinstance(settings["temperature"], (float, int)) or not 0 <= settings["temperature"] <= 2:
            raise ValueError("temperature は0〜2です。")
        if settings["top_p"] is not None and not 0 < settings["top_p"] <= 1:
            raise ValueError("top_p は0より大きく1以下、または null です。")
        if not isinstance(settings["watch_terms"], list) or not all(isinstance(x, str) and x for x in settings["watch_terms"]):
            raise ValueError("watch_terms は空でない文字列の配列です。")
        candidate_ids = spec.get("candidate_ids") or ["canonical"]
        model_ids = spec.get("model_ids") or ["http-current"]
        if len(candidate_ids) != len(set(candidate_ids)) or len(model_ids) != len(set(model_ids)):
            raise ValueError("候補またはモデルの重複があります。")
        candidates = [self.candidate(cid) for cid in candidate_ids]
        known_models = {m["id"]: m for m in self.models()}
        if not all(mid in known_models for mid in model_ids):
            raise ValueError("モデルIDが見つかりません。")
        models = [copy.deepcopy(known_models[mid]) for mid in model_ids]
        for m in models:
            override = spec.get("model_overrides", {}).get(m["id"], {})
            if set(override) - {"base_url", "model", "metrics_pid", "api_key_env", "python", "local_path"}:
                raise ValueError("未知のモデル設定です。")
            m.update(override)
        suite = self.suite(spec.get("suite_id", "mobs15"))
        if spec.get("case_ids"):
            selected = spec["case_ids"]
            if set(selected) - {c["id"] for c in suite["cases"]}:
                raise ValueError("未知のケースIDです。")
            suite["cases"] = [c for c in suite["cases"] if c["id"] in selected]
        batch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
        batch = {"schema_version": 1, "id": batch_id, "created_at": now(), "runs": []}
        # Freeze every input before creating artifacts; preparation never calls a model.
        prepared = []
        for model in models:
            for candidate in candidates:
                run_id = f"{batch_id}-{model['id']}-{candidate['id']}"
                safe_id(run_id)
                requests = self.build_requests(candidate, suite, model, settings)
                manifest = {"schema_version": 1, "id": run_id, "batch_id": batch_id,
                            "created_at": now(), "status": "prepared", "candidate": candidate,
                            "model": model, "settings": settings, "suite": suite,
                            "candidate_sha256": digest(candidate), "suite_sha256": digest(suite),
                            "requests_sha256": digest(requests), "case_count": len(requests),
                            "scope": "isolated speech generation; no planner, TTS, Minecraft, or production writes",
                            "source_hashes": {str(p.relative_to(self.repo)): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in [self.repo / "dogido_server/llm/companion_prompts.json",
                                          self.repo / "dogido-rust/src/chat_prompt.rs",
                                          self.repo / "dogido-rust/target/release/dogido-rust"]}}
                prepared.append((run_id, manifest, requests))
        for run_id, manifest, requests in prepared:
            write_json(self.runs / run_id / "manifest.json", manifest)
            write_json(self.runs / run_id / "requests.json", requests)
            batch["runs"].append(run_id)
        write_json(self.runs / (batch_id + ".batch.json"), batch)
        return batch

    def run(self, identifier: str) -> dict:
        path = self.runs / safe_id(identifier)
        manifest = read_json(path / "manifest.json")
        rows = read_jsonl(path / "results.jsonl")
        return {"manifest": manifest, "results": rows, "summary": summary(rows),
                "notes": [n for n in self.notes() if n.get("run_id") == identifier]}

    def list_runs(self) -> list[dict]:
        result = []
        for path in sorted(self.runs.glob("*/manifest.json"), reverse=True):
            data = self.run(path.parent.name)
            result.append({"manifest": data["manifest"], "summary": data["summary"]})
        return result

    def notes(self) -> list[dict]:
        return read_jsonl(self.directory / "discussion/notes.jsonl")

    def add_note(self, note: dict) -> dict:
        if not isinstance(note.get("body"), str) or not note["body"].strip():
            raise ValueError("メモ本文が必要です。")
        if note.get("run_id"):
            safe_id(note["run_id"])
        saved = {"id": uuid.uuid4().hex, "created_at": now(), "author": str(note.get("author", "User")),
                 "body": note["body"], "run_id": note.get("run_id"), "case_id": note.get("case_id"),
                 "verdict": note.get("verdict", "discussion")}
        append_json(self.directory / "discussion/notes.jsonl", saved)
        return saved

    def export_html(self, identifier: str) -> str:
        data = self.run(identifier)
        m = data["manifest"]
        esc = lambda value: html.escape(str(value))
        rows = "".join(f"<tr><td>{esc(r.get('group', ''))}</td><td>{esc(r.get('label', ''))}</td>"
                       f"<td>{esc(r.get('speech', r.get('error', '')))}</td>"
                       f"<td>{esc(r.get('metrics', {}).get('elapsed_ms'))}</td></tr>" for r in data["results"])
        return ("<!doctype html><html lang='ja'><meta charset='utf-8'><title>Dogido Prompt Lab</title>"
                "<style>body{font:16px/1.7 system-ui;max-width:1100px;margin:40px auto;padding:20px}"
                "table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:12px;text-align:left}"
                "pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f4f4;padding:20px}</style>"
                f"<h1>{esc(m['candidate']['title'])}</h1><p>{esc(m['model']['title'])} · {esc(m['status'])}</p>"
                "<p>生成原文。注目語の件数は検索結果であり、言語・安全性・品質の自動判定ではありません。</p>"
                "<table><tr><th>分類</th><th>ケース</th><th>返答</th><th>時間 ms</th></tr>" + rows + "</table>"
                f"<details><summary>試験条件と測定範囲</summary><pre>{esc(json.dumps(m, ensure_ascii=False, indent=2))}</pre></details>"
                f"<details><summary>結果JSON</summary><pre>{esc(json.dumps(data, ensure_ascii=False, indent=2))}</pre></details></html>")
