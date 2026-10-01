#!/usr/bin/env python3
"""設定OFFと音声プロファイルを模擬HTTPで確認する。実エンジンは使わない。"""
import json
from pathlib import Path
import tempfile

from check_dialogue import ROOT, dependencies, register, request, row, running, snapshot, submit, wait_for
from check_haiku_runtime import fixture


def main():
    passed = []
    with tempfile.TemporaryDirectory(prefix="dogido-settings-") as temp, dependencies() as (dep, control, seen):
        with running(ROOT / "target/debug/dogido-rust", Path(temp), dep,
                     extra_args=("--no-llm", "--no-language", "--no-audio")) as (base, _, log):
            sid = register(base)
            turn = submit(base, sid, "こんにちは")
            result = wait_for(lambda: row(base, turn, {"audio_disabled"}))
            assert result["text"] and not result.get("error"), result
            assert not seen, seen
            assert not request(base, "/healthz")["llm_enabled"]
            assert not any(h["role"] == "assistant" for h in snapshot(base)["sessions"][0]["history"])
            assert 'event="audio_started"' not in log.read_text()
    passed.append("disabled_model_and_audio_produce_text_without_network_or_completed_history")
    with fixture(extra_args=("--no-audio",)) as (base, _, _, _, seen, _, _, _, send, hud, rows, stored, _):
        sid = register(base, preview=False)
        send(sid)
        wait_for(lambda: stored(sid))
        wait_for(lambda: any(r["turn_id"].endswith(":poem") and r["playback_status"] == "audio_disabled" for r in rows(sid)))
        assert hud(sid)["state"] != "closed"
        assert not any(r["path"].startswith(("/audio_query", "/synthesis")) for r in seen)
        assert not any(r["playback_status"] == "completed" for r in rows(sid))
    passed.append("disabled_audio_keeps_haiku_generation_save_and_workshop_without_fake_playback")
    with fixture(extra_args=("--haiku-speed", "0.73")) as (base, _, _, _, seen, _, _, _, send, _, rows, _, _):
        sid = register(base, preview=False)
        send(sid)
        wait_for(lambda: any(r["turn_id"].endswith(":poem") and r["playback_status"] == "completed" for r in rows(sid)))
        syntheses = [r["body"] for r in seen if r["path"].startswith("/synthesis")]
        assert syntheses and all(r["speedScale"] == .73 for r in syntheses), syntheses
    passed.append("haiku_voice_profile_reaches_every_synthesized_sentence")
    report = {"passed": passed, "owned_processes_stopped": True}
    (ROOT / "reports/runtime-settings.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
