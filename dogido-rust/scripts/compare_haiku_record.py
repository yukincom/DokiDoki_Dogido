#!/usr/bin/env python3
"""Round-trip native haiku records through Python MemoryStore, using temp roots only.

--python-root selects the current Python checkout, including uncommitted changes.
The existing Python environment must contain its dependencies. No model, audio,
server, or existing memory is started/read/written. Cargo runs locked and offline.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace

RUST_ROOT = Path(__file__).resolve().parents[1]


def fixtures():
    base = {
        "text": "くさちのひ くろきつるぎの かげのさむさ",
        "surface_text": "草地の日\n黒き剣の\n影の寒さ",
        "reading_text": "くさちのひ\nくろきつるぎの\nかげのさむさ",
        "lines": [
            {
                "line_id": f"line_{index + 1}", "line_index": index,
                "position": ("upper", "middle", "lower")[index],
                "canonical_name": ("上五", "中七", "下五")[index],
                "surface_text": ("草地の日", "黒き剣の", "影の寒さ")[index],
                "reading_text": ("くさちのひ", "くろきつるぎの", "かげのさむさ")[index],
                "source_atom_ids": [f"observation:{index}"],
                "source_atoms": [{"atom_id": f"observation:{index}", "text": "根拠", "extra": [1, "元の資料"]}],
                "provenance": "generated",
            }
            for index in range(3)
        ],
        "materials": {"motifs": ["日", "剣", "影"], "nested": {"kept": True}},
        "preface": "ここで一句。", "interpretation": "草地の黒い剣。",
        "biome": "minecraft:plains", "structure": None, "time_phase": "day",
        "dimension": "minecraft:overworld", "event_sequence": 42, "route": "haiku",
    }
    variants = []
    for sequence in (42, None, 0):
        for micros in (0, 1, 123456):
            for hidden in (False, True):
                emission = deepcopy(base)
                emission["event_sequence"] = sequence
                if hidden:
                    emission["materials"]["material_visibility"] = {"biome": False, "sky": False}
                    emission["structure"] = "stronghold"
                    emission["materials"]["interpretation"] = "保存済みの解釈"
                variants.append((f"sequence_{sequence}:micros_{micros}:hidden_{hidden}", emission, micros))
    empty = deepcopy(base)
    for line in empty["lines"]:
        line["source_atom_ids"] = []
        line["source_atoms"] = []
    empty.update(materials={}, preface=None, interpretation=None, biome=None, structure=None,
                 time_phase=None, dimension=None, route=None)
    variants.append(("no_sources_or_world", empty, 123456))
    traces = [
        [
            {"op": "project", "thinking": True, "sequence": 99},
            {"op": "project", "mode": "alert"},
            {"op": "project", "mode": "panic"},
            {"op": "expire", "ms": 119999},
            {"op": "expire", "ms": 120000, "thinking": True},
        ],
        [
            {"op": "pause", "ms": 30000},
            {"op": "pause", "ms": 40000},
            {"op": "expire", "ms": 1000000},
            {"op": "resume", "ms": 1000000},
            {"op": "resume", "ms": 1000001},
            {"op": "activity", "ms": 1100000},
            {"op": "expire", "ms": 1209999},
            {"op": "expire", "ms": 1210000},
        ],
        [
            {"op": "activity", "ms": 100000},
            {"op": "activity", "ms": 10000},
            {"op": "expire", "ms": 219999},
            {"op": "close", "ms": 219999, "reason": "next_haiku"},
            {"op": "pause", "ms": 220000},
            {"op": "resume", "ms": 230000},
            {"op": "project", "thinking": True},
        ],
    ]
    return [(f"{name}:trace_{i}", emission, micros, steps)
            for name, emission, micros in variants for i, steps in enumerate(traces)]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--python-root", type=Path, default=RUST_ROOT.parent)
    ap.add_argument("--report", type=Path, default=RUST_ROOT / "reports/haiku-record-parity.json")
    args = ap.parse_args()
    python_root = args.python_root.resolve()
    sys.path.insert(0, str(python_root))
    from dogido_server.memory import MemoryStore
    from dogido_server.memory_types import HaikuEmission, HaikuLine
    from dogido_server.haiku.workshop import (
        close_workshop, maybe_close_for_time, open_from_emission,
        pause_workshop_for_combat, record_workshop_activity, resume_workshop_after_combat,
    )
    from dogido_server.haiku.hud import project_workshop

    def normalize(result):
        result = deepcopy(result)
        for row in result["trace"]:
            if row["snapshot"]["workshop_id"] is not None:
                row["snapshot"]["workshop_id"] = "$workshop"
        return result

    rows, expected, names = [], [], []
    failures = []
    with tempfile.TemporaryDirectory(prefix="dogido-haiku-record-") as directory:
        temporary = Path(directory)
        for index, (name, data, micros, steps) in enumerate(fixtures()):
            created_at = datetime(2026, 9, 26, 12, 34, 56, micros, timezone.utc)
            values = deepcopy(data)
            values["lines"] = tuple(HaikuLine(**line) for line in values["lines"])
            emission = HaikuEmission(created_at=created_at, **values)
            prepared = asdict(emission)
            prepared.pop("created_at")
            session_id = f"ses_test_{index}"
            python_store = MemoryStore(temporary / f"python_{index}")
            entry, inserted = python_store.save_agent_haiku(emission)
            _, duplicate = python_store.save_agent_haiku(emission)
            python_store.append_haiku_emission(session_id, emission)
            short = python_store._read_jsonl(python_store.short_term_path)[0]
            workshop = open_from_emission(emission, entry_id=entry["id"], now=created_at)
            trace = []
            for step in steps:
                now = created_at + timedelta(milliseconds=step.get("ms", 0))
                changed = None
                operation = step["op"]
                if operation == "pause":
                    changed = pause_workshop_for_combat(workshop, now=now)
                elif operation == "resume":
                    changed = resume_workshop_after_combat(workshop, now=now, reason="clear", ask_confirmation=False)
                elif operation == "activity":
                    record_workshop_activity(workshop, now=now)
                elif operation == "close":
                    close_workshop(workshop, reason=step["reason"])
                elif operation == "expire":
                    before = workshop.open
                    maybe_close_for_time(workshop, now=now)
                    changed = before and not workshop.open
                session = SimpleNamespace(
                    haiku_workshop=workshop, session_id=session_id,
                    last_sequence=step.get("sequence"),
                    machine=SimpleNamespace(
                        state=SimpleNamespace(mode=step.get("mode", "normal")),
                        # Intended phase-5b contract: an open poem wins over the
                        # next thinking indicator; Python's old HUD lacks this gate.
                        haiku_thinking_depth=int(step.get("thinking", False) and not workshop.open),
                    ),
                )
                trace.append({"changed": changed, "close_reason": workshop.close_reason,
                              "snapshot": project_workshop(session)})
            expected.append(normalize({"entry": entry, "inserted": inserted,
                "duplicate_inserted": duplicate, "short_entry": short,
                "materials": workshop.materials, "trace": trace}))
            rows.append({"emission": prepared, "created_at": created_at.isoformat(),
                "memory_root": str(temporary / f"rust_{index}"), "session_id": session_id,
                "player_name": "試験プレイヤー", "steps": steps})
            names.append(name)
        process = subprocess.run(
            [str(RUST_ROOT / "cargo.sh"), "run", "--locked", "--offline", "--quiet",
             "--example", "check_haiku_record"], cwd=RUST_ROOT,
            input="".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            stdout=subprocess.PIPE, text=True, encoding="utf-8", check=True, timeout=300,
        )
        actual = [normalize(json.loads(line)) for line in process.stdout.splitlines()]
        if len(actual) != len(rows):
            raise RuntimeError("Rust probe result count mismatch")
        for index, (py, rust) in enumerate(zip(expected, actual)):
            if py != rust:
                failures.append({"case": names[index], "python": py, "rust": rust})
            # The existing Python store must read native bytes without a converter.
            native_store = MemoryStore(Path(rows[index]["memory_root"]))
            entries = native_store.list_haiku_entries()
            short = native_store._read_jsonl(native_store.short_term_path)
            if entries != [py["entry"]] or short != [py["short_entry"]]:
                failures.append({"case": names[index], "error": "native JSONL Python roundtrip",
                                 "entries": entries, "short": short})
            # Original Python deduplication must recognize the native ID too.
            data = deepcopy(rows[index]["emission"])
            data["lines"] = tuple(HaikuLine(**line) for line in data["lines"])
            _, inserted = native_store.save_agent_haiku(HaikuEmission(
                created_at=datetime.fromisoformat(rows[index]["created_at"]), **data))
            if inserted:
                failures.append({"case": names[index], "error": "Python resaved native ID"})
        report = {
            "cases": len(rows), "failures": failures, "jsonl_roundtrips": len(rows),
            "scope": "AGENT autosave/short record/initial HUD/lifecycle in temporary directories only",
            "python_root": str(python_root),
            "python_memory_sha256": hashlib.sha256((python_root / "dogido_server/memory.py").read_bytes()).hexdigest(),
            "intentional_differences": [
                "thinking requires no open workshop", "file lock and durable append",
                "prepared emission requires three canonical helper-resolved lines",
            ],
        }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if failures:
        print(json.dumps(failures[:2], ensure_ascii=False, indent=2))
        raise SystemExit(f"FAIL {len(failures)} differences; {args.report}")
    print(f"PASS {len(rows)} record/HUD series and Python JSONL roundtrips; {args.report}")


if __name__ == "__main__":
    main()
