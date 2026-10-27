#!/usr/bin/env python3
"""環境警告を含む履歴が一回の通常生成へ届くことを模擬HTTPで確認する。"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

from check_dialogue import dependencies, running, register, request, submit, wait_for, row, snapshot
from body_senses_fixture import attach_body


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="dogido-spoken-history-") as temporary, dependencies() as (dep, control, seen):
        folder = Path(temporary)
        with running(args.binary.resolve(), folder, dep, haiku_settings={"enabled": False, "memory_enabled": False}) as (base, process, log):
            sid = register(base, preview=False, body=True)

            def event(seq, hot=False):
                return request(base, "/api/v1/game-events", attach_body({
                    "schema_version": "2026-05-24", "adapter": "fixture", "sequence": seq,
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "event": {"name": "status_snapshot", "source_kind": "system", "priority_hint": "background", "certainty": "high"},
                    "player": {"name": "試験", "dimension": "minecraft:overworld", "health": 20},
                    "world": {"weather": "clear", "biome": "plains", "sky_visible": True,
                              "ceiling_height": 20, "local_light": 15, "time_phase": "day",
                              "nearby_damaging_light_source_count": 1 if hot else 0,
                              "nearest_damaging_light_source_distance": 1.73 if hot else 99},
                    "combat": {"combat_active_hint": False}, "smell_observation": {"status":"none","components":[],"basis":"smell_policy_v2"}}, activity="holding"), sid=sid)

            event(1)
            control["leaf"] = "今日は一緒に散歩しよな。"
            turn = submit(base, sid, "今日は一緒に歩こう")
            wait_for(lambda: row(base, turn, {"completed"}))
            control["leaf"] = "触るとあちちやで！"
            event(2, True)
            wait_for(lambda: any(r["source"] == "game_observation" and r.get("text") == "触るとあちちやで！"
                                 and r["playback_status"] == "completed" for r in snapshot(base)["utterances"]))
            before = next(s["history"] for s in snapshot(base)["sessions"] if s["session_id"] == sid)
            call_count = len([r for r in seen if r["path"] == "/v1/chat/completions"])
            control["leaf"] = "そやな、気をつけよか。"
            turn = submit(base, sid, "本当だ暑そうだね")
            result = wait_for(lambda: row(base, turn, {"completed"}))
            calls = [r["body"] for r in seen if r["path"] == "/v1/chat/completions"]
            prompt = "\n".join(m["content"] for m in calls[-1]["messages"])
            checks = {
                "previous_user_in_prompt": "今日は一緒に歩こう" in prompt,
                "previous_reply_in_prompt": "今日は一緒に散歩しよな。" in prompt,
                "completed_environment_warning_in_prompt": "触るとあちちやで！" in prompt,
                "environment_warning_recorded_once": sum(p["text"] == "触るとあちちやで！" for p in before) == 1,
                "followup_uses_one_model_call": len(calls) - call_count == len(result["llm_reports"]) == 1,
            }
            assert all(checks.values()), (checks, before, calls[-1], log.read_text())
        assert process.poll() == 0
    print(json.dumps({"checks": checks, "model": "mock", "tts": "mock", "real_service_touched": False,
                      "all_owned_processes_stopped": True}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
