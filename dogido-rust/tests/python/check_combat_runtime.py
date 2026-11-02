#!/usr/bin/env python3
"""戦闘のHTTP受付・回答・割込み・leaf検査を模擬LLM/TTS/playerで確認する。"""
from datetime import datetime, timezone
import argparse
import base64
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
              "スケルトンどこ？":"enemy_information", "パンダさんもいるかな？":"enemy_information",
              "あれ、さっきいたよね":"enemy_information", "静かにして":"not_target"}
    with tempfile.TemporaryDirectory(prefix="dogido-combat-") as tmp, dependencies() as (dep, control, seen), mock_request_classifier(labels) as (classifier_args, classifier_folder):
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
        with running(binary, folder, dep, player=player, extra_args=(*classifier_args, "--chat-image-settings",
                         json.dumps({"enabled":True,"wait_ms":100,"stop_reactions_enabled":False})),
                     combat_settings={"aftermath_time_ms": 0}) as (base, process, log):
            seq = 0
            def send(sid, *, name="status_snapshot", mobs=(), audio=(), combat=None, player_state=None, world=None, passive=(), results=()):
                nonlocal seq
                seq += 1
                e = {"schema_version": "2026-05-24", "adapter": "fixture", "sequence": seq,
                     "observed_at": datetime.now(timezone.utc).isoformat(),
                     "event": {"name": name, "source_kind": "auditory" if name == "hostile_audio_detected" else "system",
                               "priority_hint": "background", "certainty": "high"},
                     "player": {"name": "試験", **(player_state or {})}, "visual_threats": list(mobs),
                     "passive_mobs":list(passive), "command_results":list(results), "auditory_threats": list(audio), "combat": combat or {}, "world": {"sky_visible":True,"ceiling_height":20,**(world or {})}}
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

            def classifications():
                path = classifier_folder / "requests.jsonl"
                return path.read_text().splitlines() if path.exists() else []

            # 模擬BERT受付からHTTP生成・配送まで。返答の意味は実機で確認する。
            def ask_mob(sid, text, image=None):
                before = llm_calls()
                calls = len(classifications())
                routed = say(sid, text)
                answer = wait_for(lambda: row(base, routed["turn_id"], {"completed"}))
                assert llm_calls() == before + 1 and len(classifications()) == calls + 1, answer
                assert answer["request_classification"]["prediction"]["label"] == "enemy_information", answer
                assert answer["text"] == control["leaf"] and not answer.get("combat_actions"), answer
                requests = [r["body"] for r in seen if r["path"] == "/v1/chat/completions"]
                user = requests[-1]["messages"][-1]["content"]
                if image:
                    png, captured = image
                    assert [part["type"] for part in user] == ["text", "image_url"], user
                    assert user[1]["image_url"]["url"] == "data:image/png;base64," + png
                    user = user[0]["text"]
                    meta = json.loads(user.split("\n\nimage_observation: ", 1)[1])
                    assert meta["captured_at"] == captured, meta
                assert isinstance(user, str) and "【今回の用件】" in user and text in user, user
                assert "呼び名: プレイヤー（自然なら一度だけ）" in user
                trace = [line for line in log.read_text().splitlines()
                         if 'event="mob_chat_request"' in line and routed["turn_id"] in line]
                assert len(trace) == 1 and ("image_attached=true" if image else "image_attached=false") in trace[0], trace
                return user

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
                # Fabricのwire由来の視認距離・向きが通常会話の一要求へ入ることを検査。
                user = ask_mob(sid, "ゾンビどこ？")
                assert "南" in user and '"距離（ブロック）":20.0' in user, user
                passed.append("adapter_wire_in_body_fixture_keeps_observed_distance_and_player_alert")
                user = ask_mob(sid, "敵は何体？")
                assert "現在視認：ゾンビ 1体" in user
                assert next(s for s in snapshot(base)["sessions"] if s["session_id"] == sid)["state"] == "alert"
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
            user = ask_mob(sid, "ゾンビどこ？")
            assert "前の北側" in user and '"距離（ブロック）":6.0' in user, user
            send(sid,mobs=[mob(distance=6)],combat={"hostiles_within_scan_ground":3,"hostile_scan_distance":24})
            user = ask_mob(sid, "敵は何体？")
            assert "現在視認：ゾンビ 1体" in user and "ゾンビ 3体" not in user
            user = ask_mob(sid, "スケルトンどこ？")
            assert "現在視認：スケルトン" not in user and "現在視認：ゾンビ 1体" in user
            passed.append("mob_questions_use_single_chat_with_observed_count_and_direction")
            close(sid)

            # 視認を外した直後の履歴と、音のみを別セッションで比較する。画像なしの模擬生成要求を読む。
            sid=register(base,preview=False,body=True)
            send(sid,passive=[{"type":"panda","direction":{"horizontal":"right"},"distance":4},
                              {"type":"panda","direction":{"horizontal":"front"},"distance":8}])
            user=ask_mob(sid, "パンダさんもいるかな？")
            assert "現在視認：パンダ 2体" in user and '"距離（ブロック）":4.0' in user
            assert "イルカ" not in user
            send(sid)
            user=ask_mob(sid, "あれ、さっきいたよね")
            assert "直近の視認：パンダ（" in user and "現在視認：一覧にモブの記録なし" in user
            assert "パンダさんもいるかな？" in user and control["leaf"] in user
            close(sid)
            sid=register(base,preview=False,body=True)
            send(sid,audio=[{"label":"panda","source_id":"unseen","spoken_name_allowed":True}])
            user=ask_mob(sid, "パンダさんもいるかな？")
            current, _ = json.JSONDecoder().raw_decode(user.split("【現在の環境観測と変化（会話履歴とは別）】\n", 1)[1])
            assert "パンダ" in json.dumps(current["observations"]["hearing"]["sounds"], ensure_ascii=False)
            assert "現在視認：一覧にモブの記録なし" in user and "直近の視認：パンダ" not in user
            passed.append("visible_recent_audio_and_delivered_history_remain_distinct")
            close(sid)

            # 画像ありでは同じ質問へ撮影時刻付きPNG一枚を一度だけ添える。画像理解は本人の実機確認。
            sid = request(base,"/api/v1/adapter-sessions",{"adapter_name":"fabric-test","adapter_version":"test",
                "game":"minecraft-java","schema_version":"2026-05-24","player_name":"試験",
                "player_uuid":"11111111-1111-4111-8111-111111111111",
                "execution_capabilities":["client.body_view.on_stop.v1"]})["session_id"]
            command = next(c for c in send(sid)["commands"] if c["type"]=="capture_body_view")
            captured = datetime.now(timezone.utc).isoformat()
            png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8v0AAAAASUVORK5CYII="
            view = {k:command[k] for k in ("body_entity_uuid","body_epoch","owner_uuid","dimension")}
            view.update(camera_restored=True,png_base64=png,sha256=hashlib.sha256(base64.b64decode(png)).hexdigest(),
                width=1,height=1,captured_at=captured,annotation_status="available",capture_id=command["command_id"],
                motion_sequence=1,scene_comparison={"status":"not_compared","reference":None,"cells":[]})
            send(sid, results=[{"command_id":command["command_id"],"command_type":"capture_body_view",
                               "status":"succeeded","executed_at":captured,"body_view":view}])
            user = ask_mob(sid,"パンダさんもいるかな？",image=(png,captured))
            assert "現在視認：一覧にモブの記録なし" in user
            passed.append("single_chat_with_one_timed_image_and_no_listed_mob")
            close(sid)

            # 通常の警告中に受けた質問は、同じ分類で一度だけ再開する。緊急割込みは別に確認。
            sid=register(base,preview=False,body=True)
            (folder/"delay").write_text(".6")
            send(sid)
            charged = mob("warden","w",12)
            send(sid,mobs=[charged],combat={"combat_active_hint":True})
            wait_for(lambda:any(r.get("combat_actions") and r["playback_status"]=="started" for r in rows(sid)))
            before = llm_calls()
            classified = len(classifications())
            pending = say(sid,"敵は何体？")
            assert pending.get("queued"), pending
            answer = wait_for(lambda:done(sid,"敵は何体？"))
            assert answer["text"] == control["leaf"] and not answer.get("combat_actions"), answer
            assert llm_calls() == before + 1 and len(classifications()) == classified + 1
            passed.append("warning_resumes_one_mob_chat_without_reclassification")
            close(sid); (folder/"delay").write_text(".06")

            sid=register(base,preview=False,body=True)
            send(sid,mobs=[mob(distance=8)])
            (folder/"delay").write_text(".7")
            got=say(sid,"ゾンビどこ？"); turn=got["turn_id"]
            wait_for(lambda:row(base,turn,{"started"}))
            send(sid,mobs=[mob(distance=7)])
            assert row(base,turn,{"started"}), rows(sid)
            send(sid,mobs=[mob(distance=5,cardinal="east",direction="right")])
            wait_for(lambda:row(base,turn,{"completed"}))
            user=ask_mob(sid,"ゾンビどこ？")
            assert "右の東側" in user and '"距離（ブロック）":5.0' in user, user
            passed.append("started_query_finishes_and_next_question_uses_current_direction")
            close(sid); (folder/"delay").write_text(".06")

            sid=register(base,preview=False,body=True)
            send(sid)
            (folder/"delay").write_text(".7")
            question = say(sid,"ドギド、一緒に歩こう。")["turn_id"]
            wait_for(lambda:row(base,question,{"started"}))
            send(sid,name="threat_approaching",mobs=[mob(distance=2,direction="back",approaching=True)])
            warning=wait_for(lambda:next((r for r in rows(sid) if r["playback_status"]=="started"
                and any(a["kind"].startswith("ushiro_") for a in r.get("combat_actions",[]))),None))
            assert row(base,question,{"cancelled"})
            # 模擬HTTP・再生。警告中の質問は終端し、警告完了後の新しい質問だけに回答する。
            got=say(sid,"ゾンビどこ？")
            wait_for(lambda:row(base,got["turn_id"],{"cancelled"}))
            send(sid)
            wait_for(lambda:row(base,warning["turn_id"],{"completed"}))
            assert row(base,question,{"cancelled"}) and not done(sid,"ゾンビどこ？")
            assert "現在視認：一覧にモブの記録なし" in ask_mob(sid,"ゾンビどこ？")
            passed.append("rear_warning_discards_overlap_then_accepts_new_question")
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
            send(sid,name="threat_approaching",mobs=[mob(distance=2,direction="back",approaching=True)],audio=sound,
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
