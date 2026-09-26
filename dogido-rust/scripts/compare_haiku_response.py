#!/usr/bin/env python3
"""Compare 4,875 parser inputs with a selected current Python checkout.

Run with Python 3.10+ and the existing Rust toolchain:
  python dogido-rust/scripts/compare_haiku_response.py --python-root .

--python-root can select a different dirty checkout before integration. Only
the four pure JSON extraction methods are loaded from its client.py via AST;
no Python service imports, models, audio, or network access are required.
Cargo builds/runs the Rust example with --locked --offline. The report defaults
to the existing ignored dogido-rust/reports/haiku-response-parity.json directory.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess

RUST_ROOT = Path(__file__).resolve().parents[1]
KINDS = (
    "haiku_line_grounding",
    "haiku_draft",
    "haiku_line_regeneration",
    "haiku_irony",
    "haiku_scene",
)
METHODS = {
    "_extract_json_object",
    "_extract_grounding_prefix",
    "_strip_code_fence",
    "_parse_json_mapping",
}


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def corpus():
    """Preserve the original all-prefix, malformed-tail, kind/finish sweep."""
    reports = [
        {
            "verdicts": {"0": "pass", "1": "meaning_fail"},
            "assessments": [
                {"line_index": 0, "atom_ids": [1]},
                {"line_index": 1, "atom_ids": []},
            ],
            "failure_reasons": {"1": "材料がない。"},
        },
        {
            "assessments": [{
                "line_index": 0,
                "atom_ids": ["observation:0"],
                "meaning_retained": True,
                "natural_japanese": True,
            }],
            "extra": '文字列内の{と}、引用"',
        },
    ]
    raw_cases = []
    for index, report in enumerate(reports):
        raw = compact(report)
        raw_cases.extend(
            (f"report_{index}:prefix_{end}", raw[:end])
            for end in range(len(raw) + 1)
        )
        for name, text in [
            ("prose", "前置き " + raw + " 後書き"),
            ("array", "[" + raw + "]"),
            ("nested", '{"broken":[' + raw),
            ("fence_lf", "```json\n" + raw + "\n```"),
            ("fence_crlf", "```json\r\n" + raw + "\r\n```"),
            ("fence_unicode", "```json\u2028" + raw + "\u2028```"),
        ]:
            raw_cases.append((f"report_{index}:{name}", text))
    prefix = compact(reports[0]).split(',"failure_reasons"')[0]
    for index, suffix in enumerate([
        ',"verdicts":',
        ',"verdicts"',
        ',"assessments":[],"failure_reasons":',
        ',42:',
        ',"verdicts":{"0":"pass"}',
        ',"note":trueXXX',
        ',"note":1x',
        ',"note":1e',
        ',"note":falsex',
        ',"note":nullx',
        ',"note":01',
        ',"note":0.',
        ',"note":-12.5e-2x',
    ]):
        raw_cases.append((f"malformed_tail_{index}", prefix + suffix))
    for index, text in enumerate([
        "```json\n" + prefix + ',"failure_reasons":',
        "{}", "null", "false", "[]", "", '{"x":{"s":"} 日本語 {"}}',
    ]):
        raw_cases.append((f"other_{index}", text))
    rows = []
    for name, text in raw_cases:
        for kind in KINDS:
            for finish in ("length", "stop", None):
                rows.append({
                    "name": f"{name}:{kind}:{finish}",
                    "kind": kind,
                    "generated": {
                        "text": text,
                        "finish_reason": finish,
                        "completion_tokens": 512,
                        "prompt_tokens": 1234,
                    },
                    "fallback": {"assessments": []},
                })
    assert len(rows) == 4875, "the recorded comparison corpus changed"
    return rows


def reference_parser(source):
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    original = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "DogidoLLM"
    )
    methods = [
        node for node in original.body
        if isinstance(node, ast.FunctionDef) and node.name in METHODS
    ]
    if {method.name for method in methods} != METHODS:
        raise ValueError("selected Python client is missing the current JSON extraction methods")
    original.bases = []
    original.keywords = []
    original.decorator_list = []
    original.body = methods
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        original,
    ], type_ignores=[])
    namespace = {"json": json}
    exec(compile(ast.fix_missing_locations(module), str(source), "exec"), namespace)
    return namespace["DogidoLLM"]()


def expected(row, parser):
    generated = row["generated"]
    grounding = row["kind"] == "haiku_line_grounding"
    payload = parser._extract_json_object(generated["text"], allow_nested=not grounding)
    if payload is None and grounding:
        payload = parser._extract_grounding_prefix(generated["text"])
    if payload is not None:
        return dict(payload, __dogido_status="accepted")
    truncated = generated["finish_reason"] in {"length", "max_tokens", "MAX_TOKENS"}
    return dict(row["fallback"], __dogido_status="output_truncated" if truncated else "invalid_json")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--python-root", type=Path, default=RUST_ROOT.parent)
    ap.add_argument("--report", type=Path, default=RUST_ROOT / "reports/haiku-response-parity.json")
    args = ap.parse_args()
    source = args.python_root.resolve() / "dogido_server/llm/client.py"
    if not source.is_file():
        ap.error(f"Python reference client not found: {source}")
    parser = reference_parser(source)
    rows = corpus()
    python_results = [expected(row, parser) for row in rows]
    corpus_jsonl = "".join(compact(row) + "\n" for row in rows)
    process = subprocess.run(
        [str(RUST_ROOT / "cargo.sh"), "run", "--locked", "--offline", "--quiet",
         "--example", "check_haiku_response"],
        cwd=RUST_ROOT, input=corpus_jsonl, text=True, encoding="utf-8",
        stdout=subprocess.PIPE, check=True, timeout=300,
    )
    rust_results = [json.loads(line) for line in process.stdout.splitlines()]
    if len(rust_results) != len(rows):
        raise RuntimeError(f"Rust returned {len(rust_results)} rows, expected {len(rows)}")
    failures = [
        {"index": index, "case": row, "python": py, "rust": rust}
        for index, (row, py, rust) in enumerate(zip(rows, python_results, rust_results))
        if py != rust
    ]
    report = {
        "cases": len(rows),
        "matched": len(rows) - len(failures),
        "failures": failures,
        "scope": "five-kind transport parser; no domain validation/model/audio/server",
        "python_reference": str(source),
        "python_source_sha256": sha256(source),
        "rust_source_sha256": sha256(RUST_ROOT / "src/haiku_response.rs"),
        "corpus_sha256": hashlib.sha256(corpus_jsonl.encode()).hexdigest(),
        "known_stricter_rust_inputs_not_in_corpus": [
            "non-finite numbers", "unpaired Unicode surrogates", "excessive nesting",
        ],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if failures:
        print(json.dumps(failures[:4], ensure_ascii=False, indent=2))
        raise SystemExit(f"FAIL {len(failures)}/{len(rows)}; {args.report}")
    print(f"PASS {len(rows)} Python/Rust haiku parser inputs; {args.report}")


if __name__ == "__main__":
    main()
