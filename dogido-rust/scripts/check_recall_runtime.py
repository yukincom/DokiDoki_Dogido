#!/usr/bin/env python3
"""一回の明示想起の条件検索・条件解除と、次の要求の再読込を模擬HTTPで確認する。"""
import json
from check_dialogue import register, row, submit, wait_for
from check_haiku_runtime import fixture
from request_classifier_fixture import mock_request_classifier


def main():
    passed = []
    with mock_request_classifier({}) as (args, _), fixture(enabled=False, extra_args=args) as state:
        base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder = state
        sid = register(base, preview=False, body=True)

        def recall(text):
            send(sid)
            turn = submit(base, sid, text, source="text")
            result = wait_for(lambda: row(base, turn, {"completed", "failed"}))
            assert result["playback_status"] == "completed", (result, log.read_text())
            assert result.get("memory_action") == "haiku_recall" and not result["llm_reports"], result
            return result

        empty = recall("覚えてる句を教えて")
        assert empty["memory_outcome"] == "read" and "まだ覚えとらへん" in empty["text"], empty
        passed.append("empty_memory_returns_existing_reply_without_generation")
        records = folder / "memory/long_term/haiku_entries.jsonl"
        records.parent.mkdir(parents=True, exist_ok=True)
        entries = [
            {"id":"old","text":"砂漠の句","created_at":"2026-10-01T00:00:00Z","world":{"biome":"desert"}},
            {"id":"new","text":"草原の句","created_at":"2026-10-02T00:00:00Z","world":{"biome":"plains"}},
        ]
        records.write_text("".join(json.dumps(r, ensure_ascii=False)+"\n" for r in entries))
        before = records.read_bytes()
        matched = recall("砂漠の句を思い出して")
        assert "砂漠の句" in matched["text"] and "草原の句" not in matched["text"], matched
        assert "ぴったりは無いけど" not in matched["text"], matched
        passed.append("place_filter_keeps_only_matching_saved_poem")
        fallback = recall("雪原の句を思い出して")
        assert "ぴったりは無いけど" in fallback["text"], fallback
        assert "砂漠の句" in fallback["text"] and "草原の句" in fallback["text"], fallback
        assert records.read_bytes() == before
        passed.append("missing_place_falls_back_without_writing_memory")
        entries.append({"id":"later","text":"追加の句","created_at":"2026-10-27T00:00:00Z","world":{"biome":"plains"}})
        records.write_text("".join(json.dumps(r, ensure_ascii=False)+"\n" for r in entries))
        latest = recall("雪原の句を思い出して")
        assert "追加の句" in latest["text"] and "草原の句" in latest["text"] and "砂漠の句" not in latest["text"], latest
        assert not any(r["path"] == "/v1/chat/completions" for r in seen)
        passed.append("next_request_reads_new_records_and_keeps_two_reply_limit")
    print(json.dumps({"passed":passed,"live_model":False,"real_minecraft":False,"owned_processes_stopped":True}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
