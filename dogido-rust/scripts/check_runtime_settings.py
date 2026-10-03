#!/usr/bin/env python3
"""設定OFFと音声プロファイルを模擬HTTPで確認する。実エンジンは使わない。"""
import json
from pathlib import Path
import tempfile
from urllib.error import HTTPError

from check_dialogue import ROOT, dependencies, register, request, row, running, snapshot, submit, wait_for
from check_haiku_runtime import fixture


def main():
    passed = []
    with tempfile.TemporaryDirectory(prefix="dogido-http-settings-") as temp, dependencies() as (dep, _, _):
        settings = {"max_batch_size": 1, "max_body_kb": 1,
                    "heartbeat_interval_ms": 900, "accepted_schema_version": "fixture-version"}
        with running(ROOT / "target/debug/dogido-rust", Path(temp), dep,
                     extra_args=("--server-settings", json.dumps(settings), "--no-llm", "--no-audio")) as (base, _, _):
            registration = {"adapter_name": "fixture", "adapter_version": "fixture", "game": "minecraft-java",
                            "schema_version": "fixture-version", "player_name": "fixture"}
            session = request(base, "/api/v1/adapter-sessions", registration)
            for key in ("max_batch_size", "heartbeat_interval_ms", "accepted_schema_version"):
                assert session[key] == settings[key], session
            event = {"schema_version": "fixture-version", "adapter": "fixture", "sequence": 1,
                     "observed_at": "2026-10-04T00:00:00Z", "event": {"name": "status_snapshot",
                     "source_kind": "system", "priority_hint": "background", "certainty": "high"}}
            for path, body, expected in [
                ("/api/v1/game-events/batch", {"events": [event, event]}, 400),
                ("/api/v1/adapter-sessions", registration | {"player_name": "x" * 1500}, 413),
            ]:
                try:
                    request(base, path, body, sid=session["session_id"])
                except HTTPError as error:
                    assert error.code == expected, error
                    error.close()
                else:
                    raise AssertionError("configured HTTP limit was not applied")
    passed.append("configured_http_contract_and_limits_reach_the_running_rust_server")
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
