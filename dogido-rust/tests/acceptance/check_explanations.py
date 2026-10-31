#!/usr/bin/env python3
"""テストファイル冒頭の目的と各テスト直前の狙いが、それぞれ200字以内か確認する。説明の欠落や長いコメントを検出し、読む場所をコードのそばに揃える。"""

import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent


def main():
    manifest = json.loads((ROOT / "parts.json").read_text(encoding="utf-8"))
    files = sorted({path for part in manifest.values() for path in part["files"] if path.endswith(".rs")})
    errors = []
    checked = 0
    maximum = 0
    for name in files:
        lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
        header = []
        for line in lines:
            if not line.startswith("//!"):
                break
            header.append(line[3:].strip())
        length = len("".join(header))
        if not 0 < length <= 200:
            errors.append(f"{name}: 冒頭の目的が{length}字")
        maximum = max(maximum, length)
        for i, line in enumerate(lines):
            if not re.match(r"\s*#\[(?:tokio::)?test(?:\([^]]*\))?\]", line):
                continue
            previous = i - 1
            comments = []
            while previous >= 0 and lines[previous].lstrip().startswith("//"):
                comments.append(re.sub(r"^\s*//[/!]?\s?", "", lines[previous]))
                previous -= 1
            length = len("".join(comments))
            checked += 1
            maximum = max(maximum, length)
            if not 0 < length <= 200:
                errors.append(f"{name}:{i + 1}: 狙いが{length}字")
    case_count = 0
    for filename, keys in [("part45_model_cases.json", ("expectation", "acceptance", "scope")),
                           ("part8_model_cases.json", ("expectation", "review", "scope"))]:
        cases = json.loads((ROOT / filename).read_text(encoding="utf-8"))["cases"]
        case_count += len(cases)
        for case in cases:
            for key in keys:
                length = len(case.get(key, ""))
                if not 0 < length <= 200:
                    errors.append(f"{filename}:{case['id']}:{key}が{length}字")
                maximum = max(maximum, length)
    for error in errors:
        print(error)
    print(f"説明検査: {len(files)}ファイル、{checked}テスト、モデル評価{case_count}ケース、最長{maximum}字")
    return int(bool(errors))


if __name__ == "__main__":
    sys.exit(main())
