#!/usr/bin/env python3
"""戦闘のHTTP受付・回答・割込み・leaf検査を模擬LLM/TTS/playerで確認する。"""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import time
from check_dialogue import dependencies, running, register, request, snapshot, wait_for, row

ROOT = Path(__file__).resolve().parents[1]


def main():
    passed = []
    with tempfile.TemporaryDirectory(prefix="dogido-combat-") as tmp, dependencies() as (dep, control, seen):
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
        with running(ROOT / "target/debug/dogido-rust", folder, dep, player=player,
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
                return request(base, "/api/v1/game-events", e, sid=sid)
            def mob(kind="zombie", ident="z", distance=8, direction="front", cardinal="north", **extra):
                return {"type": kind, "entity_id": ident, "distance": distance,
                        "direction": {"horizontal": direction, "cardinal": cardinal}, **extra}
            def say(sid, text):
                got=request(base,"/api/v1/player-input",{"session_id":sid,"source":"voice","text":text})
                assert got["accepted"], got
                return got
            def rows(sid):
                return [r for r in snapshot(base)["utterances"] if r["session_id"] == sid]
            def done(sid, text=None, kind=None):
                return next((r for r in reversed(rows(sid)) if r["playback_status"]=="completed"
                             and (text is None or r.get("player_input_text")==text)
                             and (kind is None or any(a["kind"]==kind for a in r.get("combat_actions",[])))), None)
            def close(sid): request(base,"/api/v1/adapter-sessions/"+sid,method="DELETE")
            def llm_calls(): return len([r for r in seen if r["path"]=="/v1/chat/completions"])

            sid=register(base,preview=False)
            send(sid,mobs=[mob(distance=6)])
            wait_for(lambda:done(sid))
            before=llm_calls()
            say(sid,"ゾンビどこ？")
            answer=wait_for(lambda:done(sid,"ゾンビどこ？"))
            assert "北" in answer["text"] and "6ブロック" in answer["text"],answer
            assert llm_calls()==before
            passed.append("panic_direction_answer_without_llm_or_input_rejection")
            send(sid,mobs=[mob(distance=6)],combat={"hostiles_within_scan_ground":3,"hostile_scan_distance":24})
            say(sid,"敵は何体？")
            answer=wait_for(lambda:done(sid,"敵は何体？"))
            assert "24ブロック" in answer["text"] and "3体" in answer["text"],answer
            assert llm_calls()==before
            passed.append("authoritative_scan_count_without_llm")
            say(sid,"スケルトンどこ？")
            answer=wait_for(lambda:done(sid,"スケルトンどこ？"))
            assert "確かめられへん" in answer["text"],answer
            passed.append("missing_named_enemy_is_not_replaced_by_zombie")
            close(sid)

            sid=register(base,preview=False)
            send(sid,mobs=[mob(distance=8)])
            wait_for(lambda:done(sid))
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

            sid=register(base,preview=False)
            send(sid)
            (folder/"delay").write_text(".7")
            send(sid,name="threat_approaching",mobs=[mob(distance=2,direction="back")])
            warning=wait_for(lambda:next((r for r in rows(sid) if r["playback_status"]=="started"),None))
            got=say(sid,"ゾンビどこ？")
            assert got["queued"],got
            send(sid)
            answer=wait_for(lambda:done(sid,"ゾンビどこ？"))
            assert "確かめられへん" in answer["text"],answer
            assert row(base,warning["turn_id"],{"completed"})
            passed.append("queued_question_survives_prior_warning_target_disappearance")
            close(sid);(folder/"delay").write_text(".06")

            sid=register(base,preview=False)
            send(sid)
            before=llm_calls()
            send(sid,name="hostile_audio_detected",audio=[{"label":"zombie","source_id":"sound-z", "spoken_name_allowed":True}])
            answer=wait_for(lambda:done(sid,kind="auditory_hostile"))
            assert "ゾンビ" in answer["text"],answer
            assert llm_calls()==before
            passed.append("audio_only_enemy_reaction_and_independent_lifetime")
            close(sid)

            sid=register(base,preview=False)
            send(sid,mobs=[mob("warden","w",12)],combat={"combat_active_hint":True})
            boss=wait_for(lambda:done(sid))
            assert "ウォーデン" in boss["text"],boss
            close(sid)
            passed.append("boss_cue_and_body_share_completion")

            sid=register(base,preview=False)
            send(sid,mobs=[mob(distance=6)],combat={"combat_active_hint":True})
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

            sid=register(base,preview=False)
            send(sid,mobs=[mob(distance=6)])
            wait_for(lambda:done(sid))
            before=llm_calls()
            outcome={"entity_id":"z","type":"zombie","outcome":"player_kill","evidence":"server_death_event"}
            send(sid,name="hostile_defeated",combat={"hostile_outcomes":[outcome]})
            answer=wait_for(lambda:done(sid,kind="hostile_defeated"))
            assert "倒した" in answer["text"],answer
            send(sid,name="hostile_defeated",combat={"hostile_outcomes":[outcome]})
            assert len([r for r in rows(sid) if any(a["kind"]=="hostile_defeated" for a in r.get("combat_actions",[]))])==1
            assert llm_calls()==before
            control["leaf"]="ゾンビを倒したで！ようやったな！"
            send(sid,name="combat_ended",combat={"hostile_outcomes":[],"hostiles_within_scan_ground":0})
            answer=wait_for(lambda:done(sid,kind="aftermath"))
            assert "倒した" not in answer["text"],answer
            assert llm_calls()>before
            assert answer.get("llm_reports"),answer
            passed.append("explicit_kill_once_and_aftermath_leaf_cannot_invent_another_kill")
            close(sid)

            sid=register(base,preview=False)
            control["delay"]=2
            before_leaf=llm_calls()
            sound=[{"label":"zombie","source_id":"unseen","spoken_name_allowed":True}]
            send(sid,audio=sound,world={"sky_visible":False,"ceiling_height":2,"enclosure_score":.8})
            wait_for(lambda:llm_calls()>before_leaf)
            send(sid,name="threat_approaching",mobs=[mob(distance=2,direction="back")],audio=sound,
                 world={"sky_visible":False,"ceiling_height":2,"enclosure_score":.8})
            answer=wait_for(lambda:done(sid))
            assert "うしろ" in answer["text"],answer
            assert any(r["playback_status"]=="cancelled" for r in rows(sid)),rows(sid)
            passed.append("new_rear_threat_interrupts_slow_leaf_without_losing_body")
            close(sid)

            sid=register(base,preview=False)
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
                                 "all_owned_processes_stopped":True},ensure_ascii=False,indent=2)+"\n")
    print(f"PASS {len(passed)} combat HTTP/lifecycle checks; all owned processes stopped")


if __name__=="__main__": main()
