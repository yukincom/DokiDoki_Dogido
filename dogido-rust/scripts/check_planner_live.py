#!/usr/bin/env python3
"""起動済みモデルで合成会話6件を逐次確認。サーバー起動・切替・音声・保存なし。"""
import argparse
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = {
    "casual": {"continue_conversation"},
    "explicit_repair": {"repair_conversation"},
    "clarify_repair": {"clarify_repair"},
    "quoted_signal": {"continue_conversation"},
    "unobserved_cat": {"check_entity_presence"},
    "pending_explanation": {"repair_conversation"},
}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
    parser.add_argument("--binary", type=Path, default=ROOT / "target/release/dogido-rust")
    args = parser.parse_args()
    results = []
    report_path = ROOT / "reports/planner-live.json"
    report_path.parent.mkdir(exist_ok=True)
    for name, actions in SCENARIOS.items():
        start = time.monotonic()
        done = subprocess.run([str(args.binary), "plan-chat", str(ROOT / f"fixtures/planner/{name}.json"),
                               "--base-url", args.base_url], capture_output=True, text=True, timeout=45)
        if done.returncode:
            row = {"name": name, "passed": False, "process_error": done.stderr}
        else:
            report = json.loads(done.stdout)
            row = {"name": name, "passed": report["plan"]["action"] in actions and report["plan"]["source"] == "model",
                   "expected_actions": sorted(actions), "total_ms": round((time.monotonic()-start)*1000), **report}
        results.append(row)
        report_path.write_text(json.dumps({"scenarios": results, "servers_started": 0,
            "scope": "planner only; synthetic completed history/current observations; no Minecraft/STT/TTS",
            "requested_model": "default_model"}, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({k: row[k] for k in ("name", "passed", "result", "calls", "total_ms") if k in row} |
              {"action": row.get("plan", {}).get("action")}, ensure_ascii=False), flush=True)
    print(f"passed={sum(r['passed'] for r in results)}/{len(results)} report={report_path}", flush=True)
    if not all(r["passed"] for r in results): raise SystemExit(1)

if __name__ == "__main__": main()
