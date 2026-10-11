#!/usr/bin/env python3
"""本文JSONの初稿・再生成・失敗上限を実HTTPと模擬LLM/TTSで確認する。"""
import json
from check_dialogue import register, wait_for
from check_haiku_runtime import fixture, LINES


def run_case(mode):
    with fixture() as (base, _, _, control, _, _, _, _, send, hud, rows, stored, _):
        original = control["structured_handler"]
        calls = []

        def model(incoming):
            temperature = incoming.get("temperature")
            if temperature in [.6, .3]:
                assert not incoming.get("tools") and "tool_choice" not in incoming
                assert all(m["role"] in ["system", "user"] for m in incoming["messages"])
                prompt = "\n".join(m["content"] for m in incoming["messages"])
                assert "JSON" in prompt and "submit_haiku_candidate" not in prompt
            if temperature == .6:
                original(incoming)
                calls.append(incoming)
                if mode == "native_only":
                    return {"role": "assistant", "content": None, "tool_calls": [{
                        "id": "unexpected-native", "type": "function", "function": {
                            "name": "submit_haiku_candidate", "arguments": json.dumps({
                                "lines": [{"line_index": i, "text": t} for i, t in enumerate(LINES)]})}}]}
                return {"lines": ["ひ", *LINES[1:]]}
            if temperature == .3:
                calls.append(incoming)
                assert LINES[1] in prompt and LINES[2] in prompt
                assert "音数が短い" in prompt
                if len(calls) > 2:
                    assert "以前と実質同じ候補" in prompt
                return {"lines": [{"line_index": 0, "text": LINES[0] if mode == "repair" else "ひ"}]}
            return original(incoming)

        control["structured_handler"] = model
        sid = register(base, preview=False)
        send(sid)
        ending = ":poem" if mode == "repair" else ":failure"
        wait_for(lambda: any(r["turn_id"].endswith(ending) and r["playback_status"] == "completed" for r in rows(sid)))
        if mode == "repair":
            assert len(calls) == 2 and len(stored(sid)) == 1
            assert hud(sid)["canonical_lines"] == LINES
            assert stored(sid)[0]["materials_snapshot"]["regeneration_rounds"] == 1
        else:
            assert not stored(sid) and hud(sid)["state"] == "closed"
            assert len(calls) == (1 if mode == "native_only" else 7)
        return {"case": mode, "passed": True, "candidate_calls": len(calls), "saved_entries": len(stored(sid))}


def main():
    print(json.dumps({"passed": True, "cases": [run_case(mode) for mode in ["repair", "native_only", "bounded_repetition"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
