#!/usr/bin/env python3
"""戦闘前履歴をPython正本と照合し、実HTTP・模擬音声で生成への配送を確認する。"""
import json
import sys
from pathlib import Path

from check_dialogue import register, request, row, snapshot, submit, wait_for
from check_haiku_runtime import fixture
from check_language_runtime import interpretation
from check_workshop_combat import SETTINGS, MOB, drive, end, enter, event

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dogido_server.dialogue_context import DialogueContext


def session(base, sid):
    return next(s for s in snapshot(base)["sessions"] if s["session_id"] == sid)


def main():
    passed = []
    with fixture(enabled=False, combat_settings=SETTINGS) as f:
        base, _, log, control, seen, _, _, _, send, _, rows, _, folder = f
        sid = register(base, preview=False)
        oracle = DialogueContext()
        comparisons = 0

        def compare():
            nonlocal comparisons
            actual = session(base, sid)
            assert actual["history"] == oracle.prompt_turns(), (actual, oracle.prompt_turns())
            assert actual["history_retention"] == {
                "danger_active": oracle._danger_active,
                "retained_utterances": len(oracle._danger_retained),
                "remaining_player_turns": oracle._post_danger_player_turns_remaining}, actual
            comparisons += 1

        def say(text):
            send(sid)
            before = len(seen)
            expected_planner_history = oracle.prompt_turns()[-10:]
            turn = submit(base, sid, text)
            reply = wait_for(lambda: row(base, turn, {"completed"}))
            oracle.add_player(text, turn_id=turn)
            oracle.add_dogido(reply["text"], turn_id=turn)
            compare()
            prompts = [r["body"] for r in seen[before:] if r["path"] == "/v1/chat/completions"]
            assert len(prompts) == 2, prompts
            planner = next(p for p in prompts if p["max_tokens"] == 640)
            assert all(r["turn_id"] in json.dumps(planner) for r in expected_planner_history), planner
            return turn, prompts

        seed = [say(f"ドギド、今日の話その{i}だよ")[0] for i in range(5)]
        enter(send, rows, sid)
        oracle.begin_danger_retention()
        compare()
        for _ in range(12):
            send(sid, visual_threats=[MOB], combat={"combat_active_hint": True})
        compare()
        end(send, rows, sid)
        oracle.end_danger_retention(player_turns=3)
        compare()
        for i, text in enumerate(["ドギド、一段落したね", "ドギド、落ち着いたね", "さっきの話の続き"]):
            _, prompts = say(text)
            # 既存plannerは直近10件に限定。本文生成には退避分も渡す。
            leaf = next(p for p in prompts if p["max_tokens"] != 640)
            assert "今日の話その0だよ" in json.dumps(leaf, ensure_ascii=False), leaf
            assert session(base, sid)["history_retention"]["remaining_player_turns"] == 2 - i
        _, prompts = say("ドギド、次の話をしよう")
        assert seed[0] not in json.dumps(prompts, ensure_ascii=False), prompts
        passed.append(f"python_parity_{comparisons}_snapshots_and_three_turn_prompt_lifetime")
        assert not list((folder / "memory").rglob("*.jsonl"))
        passed.append("no_added_model_calls_or_conversation_persistence")

        # 実際に始まった音声を敵が中断しても、全文を会話済みにはしない。
        (folder / "player_mode").write_text("slow")
        send(sid)
        cancelled = submit(base, sid, "ドギド、途中で止まる返事のテスト")
        wait_for(lambda: row(base, cancelled, {"started"}))
        (folder / "player_mode").write_text("ok")
        send(sid, visual_threats=[MOB], combat={"combat_active_hint": True})
        wait_for(lambda: row(base, cancelled, {"cancelled"}))
        current = session(base, sid)
        assert current["history_retention"]["danger_active"]
        assert not any(r["turn_id"] == cancelled + ":reply" for r in current["history"])
        request(base, "/api/v1/adapter-sessions/" + sid, method="DELETE")
        new = register(base, preview=False)
        assert session(base, new)["history"] == []
        assert session(base, new)["history_retention"]["retained_utterances"] == 0
        passed.append("interrupted_audio_excluded_and_session_reconnect_clears_bookmark")

    with fixture(enabled=False, combat_settings=SETTINGS) as f:
        base, _, log, control, seen, _, _, _, send, _, rows, _, _ = f
        captures = []

        def language_model(incoming):
            if incoming["max_tokens"] != 850:
                return None
            background, latest = incoming["messages"][-1]["content"].split(
                "\n以上は背景。今回応答する最新の発話はこちら：\n")
            details, current = json.loads(background), json.loads(latest)
            captures.append(details)
            return interpretation(current, target="あさ", relation="continue")

        control["structured_handler"] = language_model
        sid = register(base, preview=False)
        seeds = []
        for i in range(5):
            send(sid)
            t = submit(base, sid, f"音数を教えて。あさ。その{i}")
            wait_for(lambda: row(base, t, {"completed"}))
            seeds.append(t)
        enter(send, rows, sid)
        end(send, rows, sid)
        for i in range(3):
            send(sid)
            t = submit(base, sid, "さっきの話の続きをお願い" if i == 0 else f"音数を教えて。あさ。その後{i}")
            wait_for(lambda: row(base, t, {"completed"}))
            assert any(r["turn_id"] == seeds[0] for r in captures[-1]["history"]), captures[-1]
        send(sid)
        t = submit(base, sid, "音数を教えて。あさ。その後3")
        wait_for(lambda: row(base, t, {"completed"}))
        assert not any(r["turn_id"] == seeds[0] for r in captures[-1]["history"])
        passed.append("language_interpretation_receives_retained_history_then_expires")

    with fixture(enabled=False, combat_settings={**SETTINGS, "conversation_post_danger_player_turns": 0}) as f:
        base, _, log, _, _, _, _, _, send, _, rows, _, _ = f
        sid = register(base, preview=False)
        send(sid)
        t = submit(base, sid, "ドギド、始めよう")
        wait_for(lambda: row(base, t, {"completed"}))
        enter(send, rows, sid)
        assert session(base, sid)["history_retention"]["retained_utterances"] == 2
        send(sid, event=event("player_died"))
        current = session(base, sid)
        assert not current["history_retention"]["danger_active"]
        assert current["history_retention"]["retained_utterances"] == 0
        # death / dimension release use the same transition as combat_ended.
        drive(send, sid, lambda: all(r["playback_status"] in {"completed", "cancelled", "failed"} for r in rows(sid)))
        send(sid, visual_threats=[MOB])
        assert session(base, sid)["history_retention"]["danger_active"]
        send(sid, player={"name": "試験", "dimension": "minecraft:the_nether"})
        assert session(base, sid)["history_retention"]["retained_utterances"] == 0
        passed.append("configured_zero_releases_on_death_and_dimension_change")

    print(f"PASS {len(passed)} history retention checks; all owned processes stopped")
    for name in passed:
        print(name)


if __name__ == "__main__":
    main()
