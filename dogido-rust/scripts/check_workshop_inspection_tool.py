#!/usr/bin/env python3
"""inspect_verse一件の往復だけを、本体の既存workshop経路で確認する。観測・TTSは模擬。"""
import argparse
import json
import urllib.request

from check_dialogue import submit
from check_haiku_runtime import fixture, LINES
from check_workshop_runtime import ready, step, wait_with_observations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", help="省略時はモデルも模擬。指定時も推論サーバーは起動・停止しない")
    args = parser.parse_args()
    calls = []
    results = []
    with fixture() as (base, _, log, control, _, _, _, _, send, hud, rows, stored, _):
        def completion(incoming):
            if incoming["max_tokens"] != 420:
                return None
            # モデルが旧JSONのinspectを返しても本体は実行しない。APIの実結果を必須にする。
            if incoming.get("tools"):
                assert {t["function"]["name"] for t in incoming["tools"]} == {"inspect_verse", "stage_player_edit"}
            else:
                assert args.base_url, incoming
            calls.append(incoming)
            tool_results = [m for m in incoming["messages"] if m["role"] == "tool"]
            if tool_results:
                assert incoming["tool_choice"] == "none"
                assert len(tool_results) == 1
                result = json.loads(tool_results[0]["content"])
                assert set(result["checks"]) == {"reading", "meter", "source"}, result
                assert [r["reading_text"] for r in result["lines"]] == LINES, result
                assert [r["mora_count"] for r in result["lines"]] == [5, 7, 5], result
                assert all(r["source_status"] == "recorded" for r in result["lines"]), result
                assistant = next(m for m in incoming["messages"] if m.get("tool_calls"))
                assert assistant["tool_calls"][0]["id"] == tool_results[0]["tool_call_id"]
                results.append(result)
            else:
                assert incoming.get("tool_choice") in ({None, "auto"} if args.base_url else {"auto"})
            if args.base_url:
                request = urllib.request.Request(args.base_url.rstrip("/") + "/chat/completions",
                    json.dumps(incoming).encode(), {"Content-Type": "application/json"})
                with urllib.request.urlopen(request, timeout=45) as response:
                    return json.load(response)
            if tool_results:
                message = {"role":"assistant","content":json.dumps(step("読みと音数と出典を確認して",
                    speech="読みを確認したで。五・七・五で、三行とも出典の記録があるわ。"), ensure_ascii=False)}
            else:
                message = {"role":"assistant","content":None,"tool_calls":[{
                    "id":"inspection-1","type":"function","function":{"name":"inspect_verse",
                        "arguments":json.dumps({"checks":["reading","meter","source"]})}}]}
            return {"id":"inspection-smoke","model":"fixture","object":"chat.completion","created":0,
                    "choices":[{"index":0,"message":message,
                        "finish_reason":"tool_calls" if not tool_results else "stop"}]}
        control["completion_override"] = completion
        sid = ready(base, send, rows)
        before = stored(sid)
        turn = submit(base, sid, "この句の読みと音数と出典を確認して")
        reply = wait_with_observations(base, send, sid, turn)
        assert reply["playback_status"] == "completed", (reply, log.read_text()[-4000:])
        assert results and reply["workshop_steps"][0]["action"] == "inspect", reply
        assert reply["workshop_steps"][0]["transport"] == "native_tool", reply
        assert reply["workshop_steps"][0]["tool_call"]["name"] == "inspect_verse", reply
        assert reply["text"] and hud(sid)["canonical_lines"] == LINES and stored(sid) == before
        if not args.base_url:
            assert len(calls) == 2
        print(json.dumps({"passed":True,"model":"live" if args.base_url else "mock",
            "model_calls":len(calls),"checks":results[0]["checks"],"mora_counts":[5,7,5],
            "reply":reply["text"],"verse_unchanged":True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
