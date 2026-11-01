#!/usr/bin/env python3
"""戦闘のHTTP受付・回答・割込み・leaf検査を模擬LLM/TTS/playerで確認する。"""
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from check_dialogue import dependencies, running, register, request, snapshot, wait_for, row
from body_senses_fixture import attach_body
from request_classifier_fixture import mock_request_classifier, resolve_input_response

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter-wire-fixture", type=Path,
                        help="Fabric EventJsonBuilderTestが出力したhostile-margin-wire.json")
    args = parser.parse_args()
    wire_bytes = args.adapter_wire_fixture.read_bytes() if args.adapter_wire_fixture else None
    wire = json.loads(wire_bytes) if wire_bytes is not None else None
    passed = []
    labels = {"ゾンビどこ？":"enemy_information", "敵は何体？":"enemy_information",
              "スケルトンどこ？":"enemy_information", "静かにして":"not_target"}
    with tempfile.TemporaryDirectory(prefix="dogido-combat-") as tmp, dependencies() as (dep, control, seen), mock_request_classifier(labels) as (classifier_args, _):
        folder = Path(tmp)
        player = folder / "player"
        player.write_text(f'''#!{sys.executable}
from pathlib import Path
import sys,time
root=Path(__file__).parent
with (root/'played').open('a') as f:f.write(sys.argv[1]+'\\n')
time.sleep(float((root/'delay').read_text()))
''')
        player.chmod(0o700)
        (folder / "delay").write_text(".06")
        binary = Path(os.environ.get("DOGIDO_RUST_BINARY", ROOT / "target/debug/dogido-rust"))
        with running(binary, folder, dep, player=player, extra_args=classifier_args,
                     combat_settings={"aftermath_time_ms": 0}) as (base, process, log):
            seq = 0
            def send(sid, *, name="status_snapshot", mobs=(), audio=(), combat=None, player_state=None, world=None):
                nonlocal seq
                seq += 1
                e = {"schema_version": "2026-05-24", "adapter": "fixture", "sequence": seq,
                     "observed_at": datetime.now(timezone.utc).isoformat(),
                     "event": {"name": name, "source_kind": "auditory" if name == "hostile_audio_detected" else "system",
                               "priority_hint": "background", "certainty": "high"},
                     "player": {"name": "試験", **(player_state or {})}, "visual_threats": list(mobs),
                     "auditory_threats": list(audio), "combat": combat or {}, "world": {"sky_visible":True,"ceiling_height":20,**(world or {})}}
                return request(base, "/api/v1/game-events", attach_body(e), sid=sid)
            def mob(kind="zombie", ident="z", distance=8, direction="front", cardinal="north", **extra):
                return {"type": kind, "entity_id": ident, "distance": distance,
                        "direction": {"horizontal": direction, "cardinal": cardinal}, "environment":{"on_ground":True}, **extra}
            def say(sid, text):
                got=request(base,"/api/v1/player-input",{"session_id":sid,"source":"voice","text":text})
                assert got["accepted"], got
                return resolve_input_response(base, got)
            def rows(sid):
                return [r for r in snapshot(base)["utterances"] if r["session_id"] == sid]
            def done(sid, text=None, kind=None):
                return next((r for r in reversed(rows(sid)) if r["playback_status"]=="completed"
                             and (text is None or r.get("player_input_text")==text)
                             and (kind is None or any(a["kind"]==kind for a in r.get("combat_actions",[])))), None)
            def close(sid): request(base,"/api/v1/adapter-sessions/"+sid,method="DELETE")
            def llm_calls(): return len([r for r in seen if r["path"]=="/v1/chat/completions"])

            if wire is not None:
                def send_wire(sid, frame):
                    nonlocal seq
                    seq += 1
                    # Javaの実フィルタ・serializer出力はプレイヤー安全側へ保ち、
                    # このHTTP試験では同じ位置・視認を持つ身体fixtureを明示的に添える。
                    # 実adapterの身体scanは、このfixtureでは検証しない。
                    event = {**wire[frame], "sequence": seq,
                             "observed_at": datetime.now(timezone.utc).isoformat()}
                    return request(base, "/api/v1/game-events", attach_body(event), sid=sid)

                sid = register(base, preview=False, body=True)
                send_wire(sid, "inner")
                assert not any(r.get("combat_actions") for r in rows(sid))
                send_wire(sid, "outer")
                session = next(s for s in snapshot(base)["sessions"] if s["session_id"] == sid)
                assert session["state"] == "alert", session
                assert not any(any(a["kind"] == "aftermath" for a in r.get("combat_actions", [])) for r in rows(sid))
                before = llm_calls()
                say(sid, "ゾンビどこ？")
                answer = wait_for(lambda: done(sid, "ゾンビどこ？"))
                assert "南" in answer["text"] and "20ブロック" in answer["text"], answer
                assert any(a["kind"] == "mob_information" and a.get("scope", {}).get("kind") == "event"
                           for a in answer["combat_actions"]), answer
                assert llm_calls() == before
                passed.append("adapter_wire_in_body_fixture_keeps_observed_distance_and_player_alert")
                say(sid, "敵は何体？")
                answer = wait_for(lambda: done(sid, "敵は何体？"))
                assert "1体" in answer["text"] and "今見える範囲" in answer["text"], answer
                assert "16ブロック" not in answer["text"], answer
                assert next(s for s in snapshot(base)["sessions"] if s["session_id"] == sid)["state"] == "alert"
                assert llm_calls() == before
                passed.append("body_observed_count_does_not_use_player_ground_scan_count")
                previous_leaf = control["leaf"]
                control["leaf"] = "ふう、ひと息つけたな。"
                send_wire(sid, "ended")
                assert not any(a["kind"] in {"aftermath", "hostile_defeated"}
                               for r in rows(sid) for a in r.get("combat_actions", [])), rows(sid)
                passed.append("adapter_observation_only_exit_creates_no_combat_speech")
                close(sid)

                sid = register(base, preview=False, body=True)
                send_wire(sid, "inner")
                assert not any(r.get("combat_actions") for r in rows(sid))
                # 模擬Fabricで交戦終了後に導火。固定の安堵を挟まず、緊急警告は単独で配送する。
                send(sid, mobs=[mob(distance=6)], combat={"player_engaged":True})
                wait_for(lambda: done(sid))
                send_wire(sid, "ended")
                send(sid, name="threat_approaching", mobs=[mob("creeper", "new-creeper", 3, fuse_active=True)],
                     combat={"combat_active_hint": True})
                wait_for(lambda: done(sid, kind="creeper_fuse"))
                assert not any(a["kind"] == "aftermath" for r in rows(sid) for a in r.get("combat_actions", []))
                passed.append("new_creeper_after_quiet_combat_end_still_warns")
                close(sid)
                control["leaf"] = previous_leaf

            sid=register(base,preview=False,body=True)
            send(sid,mobs=[mob(distance=6)])
            before=llm_calls()
            say(sid,"ゾンビどこ？")
            answer=wait_for(lambda:done(sid,"ゾンビどこ？"))
            assert "北" in answer["text"] and "6ブロック" in answer["text"],answer
            assert llm_calls()==before
            passed.append("panic_direction_answer_without_llm_or_input_rejection")
            send(sid,mobs=[mob(distance=6)],combat={"hostiles_within_scan_ground":3,"hostile_scan_distance":24})
            say(sid,"敵は何体？")
            answer=wait_for(lambda:done(sid,"敵は何体？"))
            assert "1体" in answer["text"] and "24ブロック" not in answer["text"] and "3体" not in answer["text"],answer
            assert llm_calls()==before
            passed.append("bert_mob_count_uses_body_individuals_without_llm_or_player_count")
            say(sid,"スケルトンどこ？")
            answer=wait_for(lambda:done(sid,"スケルトンどこ？"))
            assert "見当たらへん" in answer["text"],answer
            passed.append("missing_named_enemy_is_not_replaced_by_zombie")
            close(sid)

            sid=register(base,preview=False,body=True)
            send(sid,mobs=[mob(distance=8)])
            (folder/"delay").write_text(".7")
            got=say(sid,"ゾンビどこ？"); turn=got["turn_id"]
            wait_for(lambda:row(base,turn,{"started"}))
            duplicate=say(sid,"ゾンビどこ？")
            assert duplicate.get("deduplicated") and duplicate["turn_id"]==turn,duplicate
            send(sid,mobs=[mob(distance=7)])
            assert row(base,turn,{"started"}), rows(sid)
            passed.append("small_distance_change_does_not_restart_started_answer")
            send(sid,mobs=[mob(distance=5,cardinal="east",direction="right")])
            old=wait_for(lambda:row(base,turn,{"completed"}))
            assert "東" not in old["text"]
            say(sid,"ゾンビどこ？")
            answer=wait_for(lambda:done(sid,"ゾンビどこ？"))
            if answer["turn_id"]==turn:
                answer=wait_for(lambda:next((r for r in rows(sid) if r.get("player_input_text")=="ゾンビどこ？" and r["turn_id"]!=turn and r["playback_status"]=="completed"),None))
            assert "東" in answer["text"] and "5ブロック" in answer["text"],answer
            passed.append("started_query_finishes_and_next_question_uses_current_direction")
            close(sid); (folder/"delay").write_text(".06")

            sid=register(base,preview=False,body=True)
            send(sid)
            (folder/"delay").write_text(".7")
            send(sid,name="threat_approaching",mobs=[mob(distance=2,direction="back")])
            warning=wait_for(lambda:next((r for r in rows(sid) if r["playback_status"]=="started"),None))
            got=say(sid,"ゾンビどこ？")
            assert got["queued"],got
            send(sid)
            answer=wait_for(lambda:done(sid,"ゾンビどこ？"))
            assert "見当たらへん" in answer["text"],answer
            assert row(base,warning["turn_id"],{"completed"})
            passed.append("queued_question_survives_prior_warning_target_disappearance")
            close(sid);(folder/"delay").write_text(".06")

            sid=register(base,preview=False,body=True)
            send(sid)
            before=llm_calls()
            # 鳴き声だけでは固定反応も追加生成も始めない。観測は模擬HTTPから本体に入力する。
            send(sid,name="hostile_audio_detected",audio=[{"label":"zombie","source_id":"sound-z", "spoken_name_allowed":True}],
                 combat={"combat_active_hint":True,"player_engaged":False})
            assert not rows(sid), rows(sid)
            assert llm_calls()==before
            passed.append("audio_only_enemy_stays_a_conversation_observation")
            close(sid)

            sid=register(base,preview=False,body=True)
            send(sid,mobs=[mob("warden","w",12)],combat={"combat_active_hint":True})
            boss=wait_for(lambda:done(sid))
            assert "ウォーデン" in boss["text"],boss
            close(sid)
            passed.append("boss_cue_and_body_share_completion")

            sid=register(base,preview=False,body=True)
            send(sid,mobs=[mob(distance=6)],combat={"combat_active_hint":True,"player_engaged":True})
            wait_for(lambda:done(sid))
            for _ in range(3):
                say(sid,"静かにして")
                time.sleep(.1)
            session=next(s for s in snapshot(base)["sessions"] if s["session_id"]==sid)
            assert session["state"]=="suppressed_panic",session
            send(sid,mobs=[mob("creeper","c",3,fuse_active=True)],combat={"combat_active_hint":True})
            answer=wait_for(lambda:done(sid,kind="creeper_fuse"))
            assert "爆発" in answer["text"],answer
            passed.append("quiet_mode_preserves_new_fuse_priority")
            close(sid)

            sid=register(base,preview=False,body=True)
            send(sid,mobs=[mob(distance=6)],combat={"player_engaged":True})
            wait_for(lambda:done(sid))
            before=llm_calls()
            outcome={"entity_id":"z","type":"zombie","outcome":"player_kill","evidence":"server_death_event"}
            # 撃破の重複通知と終了時の再掲。実HTTPで固定発話ゼロ、次の会話入力で撃破・不在が各1件。
            for _ in range(2):
                send(sid,name="hostile_defeated",combat={"hostile_outcomes":[outcome]})
            send(sid,name="combat_ended",combat={"hostile_outcomes":[outcome],"hostiles_within_scan_ground":0})
            send(sid)
            assert llm_calls()==before
            assert not any(a["kind"] in {"aftermath","hostile_defeated"}
                           for r in rows(sid) for a in r.get("combat_actions",[])), rows(sid)
            say(sid, "さっきはどうだった？")
            wait_for(lambda:done(sid, "さっきはどうだった？"))
            chat = [r["body"] for r in seen if r["path"] == "/v1/chat/completions"][-1]
            prompt = "\n".join(m["content"] for m in chat["messages"])
            current, _ = json.JSONDecoder().raw_decode(prompt.split("【現在の環境観測と変化（会話履歴とは別）】\n", 1)[1])
            changes = current["changes"]
            defeated = [c["now"] for c in changes if c["kind"] == "player_hostile_defeated"]
            assert len(defeated) == 1 and "ゾンビ" in json.dumps(defeated, ensure_ascii=False), changes
            assert sum(c["kind"] == "player_hostiles_cleared" for c in changes) == 1, changes
            assert not any(c["kind"] == "workshop_resume" for c in changes)
            passed.append("ordinary_defeat_and_clear_are_once_only_changes_for_next_chat")
            close(sid)

            sid=register(base,preview=False,body=True)
            control["delay"]=2
            before_leaf=llm_calls()
            sound=[{"label":"zombie","source_id":"unseen","spoken_name_allowed":True}]
            send(sid,audio=sound,combat={"player_engaged":True},world={"sky_visible":False,"ceiling_height":2,"enclosure_score":.8})
            wait_for(lambda:llm_calls()>before_leaf)
            send(sid,name="threat_approaching",mobs=[mob(distance=2,direction="back")],audio=sound,
                 world={"sky_visible":False,"ceiling_height":2,"enclosure_score":.8})
            answer=wait_for(lambda:done(sid))
            assert "うしろ" in answer["text"],answer
            assert any(r["playback_status"]=="cancelled" for r in rows(sid)),rows(sid)
            passed.append("new_rear_threat_interrupts_slow_leaf_without_losing_body")
            close(sid)

            sid=register(base,preview=False,body=True)
            control["delay"]=2
            send(sid,name="player_died",player_state={"health":0})
            wait_for(lambda:llm_calls()>before+1)
            send(sid,mobs=[mob("creeper","c",3,fuse_active=True)],combat={"recent_damage_ms":0})
            wait_for(lambda:done(sid,kind="creeper_fuse"))
            assert any(r["playback_status"]=="cancelled" for r in rows(sid)),rows(sid)
            passed.append("urgent_fuse_cancels_slow_combat_leaf_and_reaps_helper")
            control["delay"]=0; close(sid)
        assert "dialogue_stopped" in log.read_text()
    report=ROOT/"reports/combat-runtime-check.json"
    report.parent.mkdir(exist_ok=True)
    report.write_text(json.dumps({"passed":passed,"llm":"mock","tts":"mock","playback":"mock",
                                 "adapter_wire_sha256": hashlib.sha256(wire_bytes).hexdigest() if wire_bytes else None,
                                 "all_owned_processes_stopped":True},ensure_ascii=False,indent=2)+"\n")
    print(f"PASS {len(passed)} combat HTTP/lifecycle checks; all owned processes stopped")


if __name__=="__main__": main()
