#!/usr/bin/env python3
"""保存済みの読み/音数/出典のRust実測を既存Pythonと照合する。モデルと保存は使わない。"""
from copy import deepcopy
from datetime import datetime, timezone
from itertools import permutations
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
from dogido_server.haiku.workshop import RecentHaikuWorkshop
from dogido_server.haiku.workshop_agent import inspect_workshop
from dogido_server.memory_types import HaikuLine


def fixtures():
    for readings in (["くさちのひ", "くろきつるぎの", "かげのさむさ"],
                     ["きゃ", "しゅう", "ぎょく"], ["きゃ く", "くろいおのへと", "あさのいろ"]):
        for missing_sources in ((), (1,), (0, 1, 2)):
            lines = []
            for i in range(3):
                lines.append({"line_id": f"line_{i+1}", "line_index": i, "position": ["upper", "middle", "lower"][i],
                    "canonical_name": ["上五", "中七", "下五"][i], "surface_text": ["草地の日", "黒き剣の", "影の寒さ"][i],
                    "reading_text": readings[i], "source_atom_ids": [] if i in missing_sources else [f"source:{i}"],
                    "source_atoms": [] if i in missing_sources else [{"atom_id": f"source:{i}", "text": "出典の記録" * 60}],
                    "provenance": "generated"})
            for size in (1, 2, 3):
                for checks in permutations(("reading", "meter", "source"), size):
                    yield {"lines": deepcopy(lines), "checks": list(checks)}


def main():
    cases = list(fixtures())
    subprocess.run([str(ROOT / "cargo.sh"), "build", "--offline", "--example", "inspect_workshop"], check=True)
    run = subprocess.run([str(ROOT / "target/debug/examples/inspect_workshop")],
        input="".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), text=True, capture_output=True, check=True)
    outputs = [json.loads(line) for line in run.stdout.splitlines()]
    assert len(outputs) == len(cases)
    for case, actual in zip(cases, outputs):
        w = RecentHaikuWorkshop(surface_text="\n".join(l["reading_text"] for l in case["lines"]),
            emitted_at=datetime.now(timezone.utc), current_lines=tuple(HaikuLine(**l) for l in case["lines"]))
        expected = inspect_workshop(w, case["checks"])
        assert actual["observation"] == expected, (case, actual, expected)
    print(json.dumps({"inspection_parity": len(cases)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
