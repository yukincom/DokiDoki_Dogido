#!/usr/bin/env python3
"""部分通知を視認消失と誤解しないことを実HTTPで確認。LLM/TTS/playerは模擬。"""
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sys
import tempfile
import time

from check_dialogue import dependencies, running, register, request, snapshot, wait_for, row, submit
from compare_threats import event

ROOT = Path(__file__).resolve().parents[1]


def main():
    passed = []
    with tempfile.TemporaryDirectory(prefix="dogido-observations-") as temp, dependencies() as (dep, control, seen):
        folder = Path(temp)
        cues = folder / "cues"
        for name in ["mob/zombie.mp3", "common/counts/2.mp3", "common/phrases/orude.mp3",
                     "panic/freesound_community-male-gasp-1-7183.mp3"]:
            p = cues / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"fixture")
        player = folder / "player"
        player.write_text(f'''#!{sys.executable}
from pathlib import Path
import sys,time
root=Path(__file__).parent
with (root/'played').open('a') as f:f.write(sys.argv[1]+'\\n')
delay=float((root/'delay').read_text())
time.sleep(.04 if '/panic/' in sys.argv[1] else delay)
''')
        player.chmod(0o700)
        (folder / "delay").write_text(".5")
        with running(ROOT / "target/debug/dogido-rust", folder, dep, player=player,
                     warning_settings={"cue_dir": str(cues)}) as (base, process, log):
            seq = 0

            def send(sid, *, name="status_snapshot", empty=False, count=1, stale=False, audio_only=False, **kw):
                nonlocal seq
                seq += 1
                e = event(0, **kw)
                e["sequence"] = seq
                e["observed_at"] = (datetime.now(timezone.utc) - timedelta(seconds=30 if stale else 0)).isoformat()
                e["event"]["name"] = name
                if count > 1:
                    e["visual_threats"] = [dict(e["visual_threats"][0], entity_id=f"z{i}") for i in range(count)]
                audio_only = audio_only or name == "hostile_audio_detected"
                if empty or audio_only or name == "ambient_mob_detected":
                    e["visual_threats"] = []
                if audio_only:
                    # Fabricは視認敵の音を除く。別の未視認個体の音を同時に受ける。
                    e["event"]["source_kind"] = "auditory"
                    e["auditory_threats"] = [{"label": "zombie", "source_id": "unseen-zombie"}]
                return request(base, "/api/v1/game-events", e, sid=sid)

            def warnings(sid):
                return [r for r in snapshot(base)["utterances"]
                        if r["session_id"] == sid and r["category"] == "callout"]

            def played():
                return (folder / "played").read_text().splitlines() if (folder / "played").exists() else []

            def close(sid):
                request(base, "/api/v1/adapter-sessions/" + sid, method="DELETE")

            def reject_chat(sid):
                got = request(base, "/api/v1/player-input", {"session_id": sid, "source": "voice", "text": "こんにちは"})
                assert not got["accepted"] and got["reason"] == "fresh_safe_snapshot_required", got

            # cueの開始だけではなく、本文playerまで到達してから部分通知を割り込ませる。
            for label, kw in [("single_body", {}), ("group_fragment", {"count": 2}),
                              ("fuse_body", {"kind": "creeper", "fuse": True})]:
                sid = register(base, preview=False)
                before = len(played())
                send(sid, **kw)
                wt = wait_for(lambda: warnings(sid))[-1]["turn_id"]
                wait_for(lambda: any("/panic/" not in p for p in played()[before:]))
                send(sid, name="hostile_audio_detected")
                current = row(base, wt, {"started", "cancelled"})
                assert current and current["playback_status"] == "started", current
                send(sid, name="ambient_mob_detected")
                send(sid, name="threat_detected", audio_only=True)
                send(sid, name="hostile_audio_detected", stale=True)
                send(sid, **kw)
                wait_for(lambda: row(base, wt, {"completed"}))
                assert len(warnings(sid)) == 1, warnings(sid)
                # 完了後にも部分通知で導火edgeや増加済みIDを解除しない。
                send(sid, name="hostile_audio_detected")
                send(sid, **kw)
                assert len(warnings(sid)) == 1, warnings(sid)
                passed.append(label + "_survives_partial_notifications_without_replay")
                close(sid)

            sid = register(base, preview=False)
            (folder / "delay").write_text("30")
            send(sid)
            wt = wait_for(lambda: warnings(sid))[-1]["turn_id"]
            wait_for(lambda: row(base, wt, {"started"}))
            send(sid, name="hostile_audio_detected")
            send(sid, empty=True)
            assert wait_for(lambda: row(base, wt, {"cancelled"}))["cancel_reason"] == "target_changed_or_gone"
            passed.append("complete_empty_snapshot_still_cancels")
            close(sid)

            # 新しい部分通知だけが来続けても、視認情報の10秒期限は延長しない。
            sid = register(base, preview=False)
            send(sid)
            wt = wait_for(lambda: warnings(sid))[-1]["turn_id"]
            wait_for(lambda: row(base, wt, {"started"}))
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline and not row(base, wt, {"cancelled"}):
                send(sid, name="hostile_audio_detected")
                time.sleep(.15)
            assert row(base, wt, {"cancelled"})["cancel_reason"] == "stale_observation"
            passed.append("partial_notifications_do_not_renew_visual_lifetime")
            close(sid)

            sid = register(base, preview=False)
            send(sid, name="ambient_mob_detected")
            reject_chat(sid)
            send(sid, empty=True)
            turn = submit(base, sid)
            wait_for(lambda: row(base, turn, {"started"}))
            send(sid, name="hostile_audio_detected")
            assert wait_for(lambda: row(base, turn, {"cancelled"}))["cancel_reason"] == "auditory_hostile"
            send(sid, name="ambient_mob_detected")
            reject_chat(sid)
            send(sid, empty=True)
            (folder / "delay").write_text(".05")
            turn = submit(base, sid, "もう大丈夫かな")
            wait_for(lambda: row(base, turn, {"completed"}))
            passed.append("partial_danger_blocks_chat_until_complete_safe_observation")
            close(sid)
        assert "dialogue_stopped" in log.read_text()
    report = ROOT / "reports/warning-observation-check.json"
    report.parent.mkdir(exist_ok=True)
    report.write_text(json.dumps({"passed": passed, "llm": "mock", "tts": "mock", "playback": "mock",
                                 "all_owned_processes_stopped": True}, ensure_ascii=False, indent=2) + "\n")
    print(f"PASS {len(passed)} observation-scope checks; all owned processes stopped")


if __name__ == "__main__":
    main()
