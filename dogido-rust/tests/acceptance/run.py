#!/usr/bin/env python3
"""指定したパートの受入テストを実行する。未配線・0件・無視されたテスト・実行失敗を成功扱いせず、各結果とログをtests配下に残す。"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

SUITE = Path(__file__).resolve().parent
REPO = SUITE.parents[2]


def command(args, log, env):
    result = subprocess.run(args, cwd=REPO, env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    log.write_text(result.stdout, encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--part", type=int, action="append", choices=range(1, 9))
    parser.add_argument("--output-dir", type=Path, default=SUITE / "results" / "latest")
    args = parser.parse_args()
    manifest = json.loads((SUITE / "parts.json").read_text(encoding="utf-8"))
    parts = args.part or list(range(1, 9))
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["DOGIDO_ACCEPTANCE_PAYLOAD_DIR"] = str(output / "payloads")
    sources = {}
    for relative in ["dogido-rust/src", "dogido_server/llm", "adapter/minecraft-fabric/src/main", "adapter/minecraft-fabric/src/test", "dogido-rust/tests/acceptance"]:
        for source in sorted((REPO / relative).rglob("*")):
            if source.is_file() and source.suffix in {".rs", ".java", ".json", ".py", ".sh"} and "results" not in source.relative_to(REPO).parts:
                sources[str(source.relative_to(REPO))] = hashlib.sha256(source.read_bytes()).hexdigest()
    (output / "tested-source-sha256.json").write_text(json.dumps(sources, indent=2) + "\n", encoding="utf-8")
    check = command([sys.executable, str(SUITE / "check_explanations.py")],
                    output / "explanations.log", env)
    if check.returncode:
        print(check.stdout)
        return 2
    cargo = [str(REPO / "dogido-rust" / "cargo.sh"), "test", "--lib", "--locked", "--offline"]
    summary = []
    for number in dict.fromkeys(parts):
        part = manifest[str(number)]
        row = {"part": number, "title": part["title"], "status": "error"}
        prefix = f"part{number}"
        for preparation in part.get("prepare", []):
            prepared = command([sys.executable, str(SUITE / preparation)],
                               output / f"{prefix}-prepare.log", env)
            if prepared.returncode:
                row["reason"] = "境界試験の準備失敗"
                break
        else:
            selected = command(cargo + [part["filter"], "--", "--list"],
                               output / f"{prefix}-list.log", env)
            names = re.findall(r"^(.+): test$", selected.stdout, re.MULTILINE)
            expected = part["tests"]
            if selected.returncode or sorted(names) != sorted(expected) or not names:
                row["reason"] = "ビルド失敗またはテスト一覧の不一致"
                row["detected"] = len(names)
                row["expected"] = len(expected)
            else:
                tested = command(cargo + [part["filter"], "--", "--test-threads=1"],
                                 output / f"{prefix}-test.log", env)
                result = re.search(r"test result: (?:ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored;", tested.stdout)
                if result:
                    row.update(zip(["passed", "failed", "ignored"], map(int, result.groups())))
                    complete = row["passed"] + row["failed"] == len(expected) and row["ignored"] == 0
                    row["status"] = "pass" if complete and tested.returncode == 0 else "fail" if complete and row["failed"] else "error"
                else:
                    row["reason"] = "テスト結果が完了していない"
        summary.append(row)
        print(f"パート{number} {part['title']}: {row['status']} "
              f"成功{row.get('passed', 0)} / 失敗{row.get('failed', 0)}", flush=True)
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"ログ: {output}")
    if any(row["status"] == "error" for row in summary):
        return 2
    return int(any(row["status"] != "pass" for row in summary))


if __name__ == "__main__":
    sys.exit(main())
