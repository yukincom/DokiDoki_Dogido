#!/usr/bin/env python3
"""宛先保留のHTTP・生成回数・再生完了境界。実モデル/マイクは使わない。"""
import json
import threading
import time
from check_dialogue import register, request, row, snapshot, submit as direct_submit, wait_for
from check_haiku_runtime import fixture
from check_language_runtime import interpretation
from dogido_server.tts_reading import prepare_text_for_tts


def main():
    passed = []
    with fixture(enabled=False, combat_settings={"conversation_pending_address_ttl_ms":4000}) as f:
        base, _, log, control, seen, _, _, _, send, _, rows, _, folder = f
        def model(incoming):
            if incoming["max_tokens"] == 640 and "持ち物を教えて" in incoming["messages"][-1]["content"]:
                return {"action":"answer_observation","focus":"現在の所持品","entity_query":"",
                        "evidence":[{"turn_id":"current","quote":"持ち物を教えて"}],"confidence":.95}
            if incoming["max_tokens"] != 850: return None
            current = json.loads(incoming["messages"][-1]["content"].split("\n以上は背景。今回応答する最新の発話はこちら：\n")[1])
            if "音数" in current["text"]: return interpretation(current)
            return interpretation(current, dialogue_act="casual", target="", facet="other", topic="minecraft", relation="switch")
        control["structured_handler"] = model
        def submit(base, sid, text):
            turn = direct_submit(base, sid, text)
            while True:
                status = wait_for(lambda: row(base, turn, {"routing", "generating", "queued", "started", "completed", "not_selected", "failed", "cancelled"}))
                if status.get("category") != "routing": return turn
                routed = wait_for(lambda: row(base, turn, {"not_selected"}) and row(base, turn, {"not_selected"}).get("forwarded_input"))
                assert routed["accepted"], routed
                turn = routed["turn_id"]
        def calls(): return len([r for r in seen if r["path"] == "/v1/chat/completions"])
        def session(sid): return next(s for s in snapshot(base)["sessions"] if s["session_id"] == sid)
        def say(sid, text, status="completed"):
            send(sid)
            turn = submit(base, sid, text)
            return wait_for(lambda: row(base, turn, {status}))
        def fresh():
            sid = register(base, preview=False)
            say(sid, "音数を教えて。きょう")
            return sid
        def hold(sid, text="家を建てたいけどどこがよさそう？"):
            n = calls(); held = say(sid, text, "not_selected")
            assert calls() == n + 1 and held["routing_status"] == "awaiting_address", held
            assert all(r["text"] != text for r in session(sid)["history"])
            return held
        def close(sid): request(base, "/api/v1/adapter-sessions/" + sid, method="DELETE")

        sid = fresh(); held = hold(sid); n = calls()
        repair = say(sid, "ドギド")
        assert "家を建てたい" in repair["text"] and calls() == n, repair
        resumed = say(sid, "うん")
        assert resumed["turn_id"] == held["turn_id"] and resumed["source"] == "voice", resumed
        assert resumed["player_input_text"] == held["player_input_text"] and calls() == n + 2, resumed
        assert resumed["category"] == "speech" and session(sid)["foreground"]["route"] == "casual"
        assert len([r for r in rows(sid) if r["turn_id"] == held["turn_id"]]) == 1
        assert len([r for r in session(sid)["history"] if r["turn_id"] == held["turn_id"] + ":reply"]) == 1
        say(sid, "うん")
        assert len([r for r in rows(sid) if r["turn_id"] == held["turn_id"]]) == 1
        close(sid); passed.append("held_input_replayed_once_with_original_id_text_source_and_two_calls")

        sid = fresh(); held = hold(sid, "明日は家を作りたい")
        gate = threading.Event()
        # 初めての引用部分を止める。cache済みの冒頭二文を再利用しても全文completedにはならない。
        tail = prepare_text_for_tts("さっきの『明日は家を作りたい』のこと、今から聞いてええ？", engine="auto")
        control["tts_gates"][tail] = gate
        repair_id = submit(base, sid, "ドギド")
        wait_for(lambda: any(r["path"].startswith("/synthesis") and r["body"]["test_text"] == tail for r in seen))
        n = calls(); early = submit(base, sid, "うん")
        assert row(base, early, {"not_selected"})["resolution"] == "confirmation_before_repair_completed"
        assert calls() == n and row(base, held["turn_id"], {"not_selected"})
        gate.set()
        wait_for(lambda: row(base, repair_id, {"completed"}))
        assert say(sid, "はい")["turn_id"] == held["turn_id"]
        close(sid); passed.append("early_confirmation_does_not_interrupt_or_release_before_full_playback")

        sid = fresh(); held = hold(sid)
        (folder / "player_mode").write_text("fail")
        say(sid, "ドギド", "failed")
        (folder / "player_mode").write_text("ok")
        again = say(sid, "聞いてる？")
        assert "今から聞いてええ" in again["text"], again
        n = calls(); ambiguous = submit(base, sid, "はいと言ったら？")
        assert row(base, ambiguous, {"not_selected"})["resolution"] == "ambiguous_confirmation"
        declined = submit(base, sid, "もういい")
        assert row(base, declined, {"not_selected"})["resolution"] == "address_declined"
        assert calls() == n and row(base, held["turn_id"], {"not_selected"})["resolution"] == "declined_unaddressed"
        close(sid); passed.append("failed_repair_retries_and_ambiguous_or_declined_confirmation_has_no_generation")

        sid = fresh(); first = hold(sid)
        second = hold(sid, "あとで畑も作りたい")
        assert row(base, first["turn_id"], {"not_selected"})["resolution"] == "replaced_unaddressed"
        repair = say(sid, "ドギド")
        assert "畑" in repair["text"] and "家" not in repair["text"]
        assert say(sid, "お願い")["turn_id"] == second["turn_id"]
        close(sid); passed.append("replacement_keeps_only_latest_unaddressed_input")

        sid = fresh(); held = hold(sid)
        say(sid, "ドギド")
        time.sleep(4)
        send(sid)
        expired = row(base, held["turn_id"], {"not_selected"})
        assert expired["routing_status"] == "expired" and expired["resolution"] == "expired_unaddressed", expired
        n = calls(); later = say(sid, "うん", "not_selected")
        assert later["turn_id"] != held["turn_id"] and calls() == n + 1
        close(sid); passed.append("confirmation_does_not_extend_original_expiry_or_restore_expired_history")

        for text in ["ドギド、家を作りたい", "ところで家を作りたい"]:
            sid = fresh(); n = calls()
            reply = say(sid, text)
            assert reply["language_status"] == "host_chat" and calls() == n + 3
            close(sid)
        passed.append("explicit_address_or_topic_shift_bypasses_hold")

        sid = fresh(); held = hold(sid)
        say(sid, "ドギド")
        n = calls()
        fact = say(sid, "枕詞って何？")
        assert fact["knowledge_status"] == "found" and calls() == n, fact
        inventory = say(sid, "持ち物を教えて")
        assert inventory["player_input_text"] == "持ち物を教えて" and calls() == n + 2, inventory
        assert row(base, held["turn_id"], {"not_selected"})
        close(sid); passed.append("pending_confirmation_does_not_swallow_knowledge_or_inventory_questions")

        sid = fresh(); held = hold(sid)
        send(sid, visual_threats=[{"type":"zombie", "entity_id":"z", "distance":5}])
        interrupted = row(base, held["turn_id"], {"not_selected"})
        assert interrupted["resolution"] == "attention_interrupted", interrupted
        assert all(r["text"] != held["player_input_text"] for r in session(sid)["history"])
        close(sid); passed.append("combat_discards_pending_without_history_or_late_replay")
    print(f"PASS {len(passed)} address checks; all owned processes stopped")
    for name in passed: print(name)


if __name__ == "__main__": main()
