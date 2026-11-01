#!/usr/bin/env python3
"""戦闘中の会話と即時警告で中断した入力を、隔離HTTP・模擬LLM/TTS/playerで検証する。"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import threading
import time

from check_dialogue import register, request, row, snapshot, wait_for
from check_haiku_runtime import fixture as haiku_fixture
from request_classifier_fixture import mock_request_classifier, resolve_input_response


@contextmanager
def fixture(**settings):
    labels = {
        "ドギド、冒険楽しい？": "not_target", "ドギド、ここは何バイオーム？": "not_target",
        "ドギド、この先も一緒に行こか？": "not_target", "ドギド、空がきれいやね": "not_target",
        "さっき乗った馬をここに呼んで": "recall_mount",
    }
    with mock_request_classifier(labels) as (args, _), haiku_fixture(extra_args=args, **settings) as state:
        yield state


def horses():
    """一回の個体選択と実行前照合に使う、本人所有の最新候補。"""
    return {"schema_version": 2, "status": "available", "revision": 1,
        "world_id": "10000000-0000-0000-0000-000000000001", "observed_at": datetime.now(timezone.utc).isoformat(),
        "player_uuid": "11111111-1111-4111-8111-111111111111", "dimension": "minecraft:overworld",
        "candidates": [{"entity_id": "30000000-0000-0000-0000-000000000001", "entity_type": "horse",
            "custom_name": "ぶち", "owner_uuid": "11111111-1111-4111-8111-111111111111", "tamed": True,
            "adult": True, "equipped": True, "alive": True, "dimension": "minecraft:overworld",
            "has_passengers": False, "has_vehicle": False, "leashed": False}]}


def say(base, sid, text):
    response = request(base, "/api/v1/player-input", {
        "session_id": sid, "source": "text", "text": text})
    assert response["accepted"], response
    return resolve_input_response(base, response)


def session(base, sid):
    return next(s for s in snapshot(base)["sessions"] if s["session_id"] == sid)


def mob(kind, ident, *, fuse=False):
    return {"type": kind, "entity_id": ident, "distance": 3 if fuse else 5,
            "direction": {"horizontal": "front"}, "fuse_active": fuse}


def horse_capable(base):
    return request(base, "/api/v1/adapter-sessions", {
        "adapter_name":"fabric-test", "adapter_version":"test", "game":"minecraft-java",
        "schema_version":"2026-05-24", "player_name":"試験",
        "player_uuid":"11111111-1111-4111-8111-111111111111",
        "execution_capabilities":["integrated_server.horse.recall.v2"]})["session_id"]


def main():
    passed = []
    with fixture(enabled=False) as f:
        base, _, log, control, seen, _, _, _, send, _, rows, _, folder = f
        sid = register(base, preview=False, body=True)
        # 普通の敵の初見、増加、別個体の声が続いても生成・再生中の同じ返答を一度届ける。
        # 実HTTP・模擬モデルとplayer。次の質問でも敵の観測がモデルに届き、固定警告は一件もない。
        send(sid)
        gate = threading.Event()
        started = threading.Event()
        prompts = []
        original = control["structured_handler"]

        def hold_first_reply(incoming):
            if incoming.get("temperature") != .65:
                return original(incoming)
            prompts.append(incoming)
            if len(prompts) == 1:
                started.set()
                gate.wait(timeout=12)
            return {"action": "speak", "speech": "一緒に冒険できてうれしいで。"}

        control["structured_handler"] = hold_first_reply
        passive = {"combat_active_hint": True, "player_engaged": False}
        zombies = [mob("zombie", "z1"), mob("zombie", "z2")]
        heard = [{"label": "zombie", "source_id": "unseen", "spoken_name_allowed": True}]
        try:
            turn = say(base, sid, "ドギド、冒険楽しい？")["turn_id"]
            wait_for(started.is_set)
            epoch = row(base, turn, {"generating"})["epoch"]
            send(sid, visual_threats=zombies[:1], combat=passive)
            assert row(base, turn, {"generating"})["epoch"] == epoch
            (folder / "player_mode").write_text("slow")
            gate.set()
            wait_for(lambda: row(base, turn, {"started"}))
            send(sid, visual_threats=zombies, combat=passive)
            send(sid, event={"name": "hostile_audio_detected", "source_kind": "auditory",
                             "priority_hint": "background", "certainty": "high"},
                 auditory_threats=heard, combat=passive)
            assert row(base, turn, {"started"})["epoch"] == epoch
            (folder / "player_mode").write_text("ok")
            result = wait_for(lambda: row(base, turn, {"completed"}))
            assert result["epoch"] == epoch and result["delivery_confirmed"], result
            assert result.get("resolution") != "reply_resumed" and len(prompts) == 1, result
            assert sum(h.get("turn_id") == turn + ":reply" for h in session(base, sid)["history"]) == 1
            send(sid, visual_threats=zombies, auditory_threats=heard, combat=passive)
            response = say(base, sid, "ドギド、この先も一緒に行こか？")
            assert not response.get("queued"), response
            wait_for(lambda: row(base, response["turn_id"], {"completed"}))
            assert len(prompts) == 2 and session(base, sid)["foreground"]["route"] == "casual"
            prompt = "\n".join(m["content"] for m in prompts[1]["messages"])
            assert "ゾンビ" in prompt and not any(r.get("combat_actions") for r in rows(sid)), rows(sid)
            passed.append("passive_hostiles_preserve_generating_and_playing_reply_and_reach_next_prompt")
        finally:
            gate.set()

    with fixture(enabled=False) as f:
        base, _, log, control, seen, _, _, _, send, _, rows, _, folder = f
        sid = register(base, preview=False, body=True)
        send(sid)
        gate = threading.Event()
        started = threading.Event()
        prompts = []
        original = control["structured_handler"]

        def delayed_reply(incoming):
            if incoming.get("temperature") != .65:
                return original(incoming)
            prompts.append(incoming)
            number = len(prompts)
            if number == 1:
                started.set()
                gate.wait(timeout=12)
            return {"action": "speak", "speech": f"質問への返事その{number}やで。"}

        control["structured_handler"] = delayed_reply
        try:
            first_text = "ドギド、ここは何バイオーム？"
            first = say(base, sid, first_text)["turn_id"]
            wait_for(started.is_set)
            first_epoch = row(base, first, {"generating"})["epoch"]
            (folder / "player_mode").write_text("slow")
            creeper = mob("creeper", "fuse", fuse=True)
            send(sid, visual_threats=[creeper], combat={"combat_active_hint": True})
            warning = wait_for(lambda: next((r for r in rows(sid)
                if r["playback_status"] == "started" and any(
                    a["kind"] == "creeper_fuse" for a in r.get("combat_actions", []))), None))
            assert row(base, first, {"cancelled"}), rows(sid)
            second_text = "ドギド、この先も一緒に行こか？"
            second = say(base, sid, second_text)
            assert second["queued"] and second["reason"] == "reply_input_routing", second
            # 模擬LLMの生成中に点火。割り込まれた質問と警告中の新しい発言は、解除後にも履歴・再生成に戻らない。
            dropped = wait_for(lambda: row(base, second["turn_id"], {"cancelled"}))
            assert dropped["resolution"] == "urgent_combat_warning" and not dropped.get("forwarded_input"), dropped
            # 背後へ回った同じ個体で、配送中の導火警告を「うしろ」で止めない。模擬再生で元の警告だけが完了する。
            send(sid, visual_threats=[{**creeper, "direction": {"horizontal": "back"}, "approaching": True}],
                 combat={"combat_active_hint": True})
            (folder / "player_mode").write_text("ok")
            gate.set()
            wait_for(lambda: row(base, warning["turn_id"], {"completed"}))
            assert not any(a["kind"].startswith("ushiro_") for r in rows(sid) for a in r.get("combat_actions", []))
            for _ in range(3):
                send(sid)
            assert row(base, first, {"cancelled"})["epoch"] == first_epoch
            assert len(prompts) == 1, prompts
            history = session(base, sid)["history"]
            assert not any(r.get("turn_id") in {first + ":reply", second["turn_id"] + ":reply"} for r in history)
            fresh = say(base, sid, second_text)["turn_id"]
            assert fresh != second["turn_id"] and wait_for(lambda:row(base, fresh, {"completed"}))
            assert len(prompts) == 2
            passed.append("fuse_drops_interrupted_generation_and_overlap_then_accepts_a_fresh_question")
        finally:
            gate.set()

    for operation in [False, True]:
        with fixture(enabled=False) as f:
            base, _, log, control, seen, _, _, _, send, _, rows, _, folder = f
            sid = horse_capable(base)
            send(sid, owned_horses=horses())
            gate = threading.Event()
            started = threading.Event()
            calls = []
            original = control["structured_handler"]
            text = "さっき乗った馬をここに呼んで" if operation else "ドギド、空がきれいやね"

            def pending_operation(incoming):
                if incoming.get("temperature") != .65:
                    return original(incoming)
                calls.append(incoming)
                if len(calls) == 1:
                    started.set()
                    gate.wait(timeout=12)
                if not operation:
                    assert not incoming.get("tools"), incoming
                    return {"action":"speak", "speech":"せやな、空がきれいや。"}
                assert [t["function"]["name"] for t in incoming.get("tools", [])] == ["select_horse"], incoming
                return {"role":"assistant", "content":None, "tool_calls":[{
                    "id":"selection", "type":"function", "function":{"name":"select_horse",
                    "arguments":json.dumps({"candidate_id":"h1","evidence":text,"confidence":.99},ensure_ascii=False)}}]}

            control["structured_handler"] = pending_operation
            try:
                turn = say(base, sid, text)["turn_id"]
                wait_for(started.is_set)
                threat = mob("creeper", "operation-fuse", fuse=True)
                send(sid, visual_threats=[threat], combat={"combat_active_hint":True}, owned_horses=horses())
                if operation:
                    # BERTで受理した逃走操作は警告の音声枠と独立し、同じ一回の選択を続ける。
                    wait_for(lambda: any(a["kind"] == "creeper_fuse" for r in rows(sid)
                                        for a in r.get("combat_actions", [])))
                    assert row(base, turn, {"generating"}), rows(sid)
                    gate.set()
                    commands = wait_for(lambda: send(sid, visual_threats=[threat],
                        combat={"combat_active_hint":True}, owned_horses=horses()).get("commands"))
                    assert len(commands) == 1 and commands[0]["type"] == "recall_horse", commands
                    result = wait_for(lambda: row(base, turn, {"quiet"}))
                    assert result["horse_recall_status"] == "issued", result
                    assert len(calls) == 1 and result["horse_command_id"] == commands[0]["command_id"]
                else:
                    wait_for(lambda: row(base, turn, {"cancelled"}))
                    gate.set()
                    wait_for(lambda: any(r["playback_status"] == "completed" and any(
                        a["kind"] == "creeper_fuse" for a in r.get("combat_actions", [])) for r in rows(sid)))
                    send(sid)
                    assert row(base, turn, {"cancelled"})
                    assert len(calls) == 1
                    assert not any(c.get("tools") for c in calls), calls
                    assert not any(r.get("horse_command_id") for r in rows(sid))
                passed.append("bert_recall_keeps_single_selection_through_urgent_warning" if operation
                              else "ordinary_question_with_horse_capability_is_discarded_on_fuse")
            finally:
                gate.set()
    print(json.dumps({"passed": len(passed), "cases": passed,
                      "model": "mock", "tts": "mock", "playback": "mock",
                      "all_owned_processes_stopped": True}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
