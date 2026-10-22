#!/usr/bin/env python3
"""戦闘中の会話と緊急警告後の回答を、隔離HTTP・模擬LLM/TTS/playerで検証する。"""
import json
import threading
import time

from check_dialogue import register, request, row, snapshot, wait_for
from check_haiku_runtime import fixture


def say(base, sid, text):
    response = request(base, "/api/v1/player-input", {
        "session_id": sid, "source": "text", "text": text})
    assert response["accepted"], response
    return response


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
        zombie = mob("zombie", "stable")
        send(sid, visual_threats=[zombie], combat={"combat_active_hint": True})
        wait_for(lambda: rows(sid) and all(r["playback_status"] in {
            "completed", "cancelled", "failed"} for r in rows(sid)))
        control["delay"] = .7
        response = say(base, sid, "ドギド、冒険楽しい？")
        turn = response["turn_id"]
        assert not response.get("queued"), response
        for _ in range(4):
            send(sid, visual_threats=[zombie], combat={"combat_active_hint": True})
            time.sleep(.1)
        result = wait_for(lambda: row(base, turn, {"completed"}))
        assert result["delivery_confirmed"] and result.get("resolution") != "reply_resumed", result
        assert session(base, sid)["foreground"]["combat_active"]
        assert session(base, sid)["foreground"]["route"] == "casual"
        prompt = "\n".join(m["content"] for r in seen if r["path"] == "/v1/chat/completions"
                           for m in r["body"]["messages"])
        approved = "敵がおるときも、プレイヤーとの会話に応じてな。注意が必要な状況なら、回答に短く注意を自然に含めてな。変化がない注意は繰り返さんでええで。"
        assert approved in prompt
        passed.append("combat_presence_does_not_cancel_player_reply_and_approved_prompt_is_used")

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
            duplicate = say(base, sid, first_text)
            assert duplicate["deduplicated"] and duplicate["turn_id"] == first, duplicate
            (folder / "player_mode").write_text("ok")
            send(sid, visual_threats=[creeper], combat={"combat_active_hint": True},
                 world={"biome": "minecraft:desert", "sky_visible": True,
                        "ceiling_height": 20, "weather": "clear", "time_phase": "morning"})
            gate.set()
            resumed = wait_for(lambda: row(base, first, {"completed"}), timeout=10)
            following = wait_for(lambda: row(base, second["turn_id"], {"completed"}), timeout=10)
            assert resumed["epoch"] > first_epoch and resumed["resolution"] == "reply_resumed", resumed
            assert resumed["completed_at"] < following["completed_at"]
            assert row(base, warning["turn_id"], {"completed"})
            assert session(base, sid)["foreground"]["combat_active"]
            assert len(prompts) == 3, prompts
            current_prompt = "\n".join(m["content"] for m in prompts[1]["messages"])
            assert "desert" in current_prompt or "砂漠" in current_prompt, current_prompt
            assert f"プレイヤー: {first_text}" not in current_prompt, current_prompt
            history = session(base, sid)["history"]
            for turn in [first, second["turn_id"]]:
                assert sum(r["turn_id"] == turn + ":reply" for r in history) == 1, history
            assert not any(r.get("text") == "質問への返事その1やで。" for r in history)
            passed.append("urgent_warning_retains_original_question_and_resumes_fifo_during_combat")
            passed.append("resumption_refreshes_observation_and_discards_late_cancelled_generation")
        finally:
            gate.set()

    for operation in [False, True]:
        with fixture(enabled=False) as f:
            base, _, log, control, seen, _, _, _, send, _, rows, _, folder = f
            sid = horse_capable(base)
            send(sid)
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
                    return {"role":"assistant", "content":"せやな、空がきれいや。", "tool_calls":[]}
                return {"role":"assistant", "content":None, "tool_calls":[{
                    "id":"request", "type":"function", "function":{"name":"list_horses",
                    "arguments":json.dumps({"evidence":text,"confidence":.99},ensure_ascii=False)}}]}

            control["structured_handler"] = pending_operation
            try:
                turn = say(base, sid, text)["turn_id"]
                wait_for(started.is_set)
                send(sid, visual_threats=[mob("creeper", "operation-fuse", fuse=True)],
                     combat={"combat_active_hint":True})
                wait_for(lambda: row(base, turn, {"cancelled"}))
                gate.set()
                result = wait_for(lambda: row(base, turn, {"unsupported" if operation else "completed"}))
                if operation:
                    assert result["error"] == "interrupted_operation", result
                else:
                    assert result["delivery_confirmed"] and result["resolution"] == "reply_resumed", result
                assert len(calls) == 2
                assert not any(t["function"]["name"] == "select_horse" for c in calls for t in c.get("tools", []))
                assert not any(r.get("horse_command_id") for r in rows(sid))
                passed.append("interrupted_unclassified_operation_never_replays_selection_or_execution" if operation
                              else "unclassified_conversation_with_horse_capability_resumes_after_warning")
            finally:
                gate.set()
    print(json.dumps({"passed": len(passed), "cases": passed,
                      "model": "mock", "tts": "mock", "playback": "mock",
                      "all_owned_processes_stopped": True}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
