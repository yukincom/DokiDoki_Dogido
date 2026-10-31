#!/usr/bin/env python3
"""対話圧縮からSIGTERM後の旅の記録まで、模擬モデルと実HTTPで検証する。"""
import json
import signal

from check_dialogue import register, request, row, snapshot, submit, wait_for
from check_haiku_runtime import fixture


def synopsis_model(control):
    calls = []
    def complete(incoming):
        system = incoming["messages"][0]["content"]
        if not system.startswith("ドギドやで。"):
            return None
        assert not incoming.get("tools"), incoming
        calls.append(incoming)
        assert "あらすじ6件" not in system
        text = f"記録{len(calls)}：川沿いを歩き、村のそばに拠点を作る相談をした。"
        return {"id": "journey-mock", "object": "chat.completion", "created": 0,
                "model": "mock-model", "choices": [{"index": 0, "message": {
                    "role": "assistant", "content": text}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130}}
    control["completion_override"] = complete
    return calls


def main():
    passed = []
    with fixture(enabled=False) as f:
        base, process, log, control, seen, _, _, _, send, _, _, _, folder = f
        calls = synopsis_model(control)
        sid = register(base, preview=False)
        control["leaf"] = "川沿いの冒険も楽しみやな。"
        long = "川沿いの道を歩きながら村に行ってみたい。" * 8 + "最後に橋も見たい。"
        for i in range(71):
            send(sid)
            turn = submit(base, sid, long if i == 0 else f"ドギド、冒険の話その{i}だよ")
            wait_for(lambda: row(base, turn, {"completed"}))
            history = next(s["history"] for s in snapshot(base)["sessions"] if s["session_id"] == sid)
            if i == 19:
                assert not calls and history[0]["text"] == long, history
            if i == 20:
                assert len(calls) == 1 and long in calls[0]["messages"][1]["content"]
                assert not any(r["text"] == long for r in history), history
                reply_calls = [r["body"] for r in seen if r["path"] == "/v1/chat/completions"]
                leaf = reply_calls[-1]["messages"][-1]["content"]
                assert "村のそばに拠点を作る相談" in leaf and long not in leaf, leaf
        assert len(calls) == 6, len(calls)
        reply_calls = [r["body"] for r in seen if r["path"] == "/v1/chat/completions"]
        leaf = reply_calls[-1]["messages"][-1]["content"]
        assert "記録1：" not in leaf and "記録2：" in leaf and "記録6：" in leaf, leaf
        synopses = [r for r in history if r.get("reaction") == "summary"]
        assert len(synopses) == 6 and all(p["turn_count"] == 10 for p in synopses), synopses
        passed.append("20_turn_boundary_one_summary_last_five_context_and_all_six_archived")
        # Minecraft側が先に切断しても、その冒険の残りを正常終了へ引き継ぐ。
        request(base, "/api/v1/adapter-sessions/" + sid, method="DELETE")
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=15)
        assert process.returncode == 0, log.read_text()
        records = [json.loads(p.read_text()) for p in (folder / "memory/long_term/journeys").glob("*.json")]
        assert len(records) == 1, records
        record = records[0]
        assert record["ended_at"] and record["date"] and not record["unfinished"], record
        assert sum(p["turns"] for p in record["synopses"]) == 71, record
        assert long not in json.dumps(record, ensure_ascii=False)
        assert len(calls) == 7
        assert len(record["synopses"]) == 7 and not record["raw_tail"]
        passed.append("disconnect_then_SIGTERM_saves_remaining_11_turns_without_raw_dialogue")

    with fixture(enabled=False) as f:
        base, process, log, control, _, _, _, _, send, _, _, _, folder = f
        calls = synopsis_model(control)
        sid = register(base, preview=False)
        control["leaf"] = "短い冒険も覚えておこか。"
        for i in range(10):
            send(sid)
            turn = submit(base, sid, f"未要約の目印{i}だよ")
            wait_for(lambda: row(base, turn, {"completed"}))
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=15)
        assert process.returncode == 0, log.read_text()
        records = [json.loads(p.read_text()) for p in (folder / "memory/long_term/journeys").glob("*.json")]
        assert len(records) == 1 and not calls, records
        record = records[0]
        assert not record["synopses"] and len(record["raw_tail"]) == 20, record
        assert record["raw_tail"][0]["text"] == "未要約の目印0だよ"
        assert record["raw_tail"][1]["role"] == "assistant"
        passed.append("SIGTERM_keeps_ten_turns_raw_without_summary_model_call")

    with fixture() as f:
        base, process, log, control, _, _, _, _, send, _, rows, stored, folder = f
        synopsis_model(control)
        sid = register(base, preview=False, body=True)
        send(sid)
        wait_for(lambda: any(r["turn_id"].endswith(":poem") and r["playback_status"] == "completed" for r in rows(sid)))
        poem = wait_for(lambda: stored(sid))[0]
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=15)
        assert process.returncode == 0, log.read_text()
        records = [json.loads(p.read_text()) for p in (folder / "memory/long_term/journeys").glob("*.json")]
        assert len(records) == 1 and len(records[0]["poems"]) == 1, records
        assert records[0]["poems"][0]["id"] == f"session:{sid}:{poem['id']}", records
        passed.append("delivered_poem_links_to_existing_bookshelf_identity")
    from check_workshop_fixture import fixture as workshop_fixture
    from check_workshop_runtime import install, ready, step
    with workshop_fixture() as f:
        base, process, log, control, seen, _, _, _, send, hud, rows, _, folder = f
        summaries = synopsis_model(control)
        prompts = install(control, lambda text, prompt, n: step(text, "respond", f"相談の返答{n:04d}やで。"))
        sid = ready(base, send, rows)
        for i in range(31):
            send(sid)
            turn = submit(base, sid, f"川柳相談の目印{i:04d}だよ")
            wait_for(lambda: row(base, turn, {"completed"}))
            assert not summaries, "summary blocked workshop input"
        assert len(prompts) == 31, prompts
        last_prompt = prompts[-1][1]
        assert "川柳相談の目印0000" not in last_prompt and "川柳相談の目印0009" not in last_prompt
        assert "川柳相談の目印0010" in last_prompt and "川柳相談の目印0029" in last_prompt, last_prompt
        history = next(s["history"] for s in snapshot(base)["sessions"] if s["session_id"] == sid)
        assert any("川柳相談の目印0000" in r["text"] for r in history), history
        send(sid)
        turn = submit(base, sid, "終了でお願いします")
        wait_for(lambda: row(base, turn, {"completed"}))
        assert hud(sid)["state"] == "closed" and not summaries
        send(sid)
        turn = submit(base, sid, "冒険の続きを話そう")
        wait_for(lambda: row(base, turn, {"completed"}))
        assert len(summaries) == 1, summaries
        source = summaries[0]["messages"][1]["content"]
        assert "川柳相談の目印0000" in source and "川柳相談の目印0021" in source
        assert "川柳相談の目印0022" not in source, source
        history = next(s["history"] for s in snapshot(base)["sessions"] if s["session_id"] == sid)
        assert sum(r.get("dialogue_turn") is True for r in history) == 10, history
        passed.append("workshop_31_turns_no_summary_latest_20_read_and_post_close_batch_keeps_10")
    print(json.dumps({"checks": passed, "model": "mock", "tts": "mock", "real_service_touched": False,
                      "all_owned_processes_stopped": True}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
