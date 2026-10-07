#!/usr/bin/env python3
"""戦闘前の合成会話と保持期限を、実HTTP・模擬音声の配送で確認する。"""
import json
from check_dialogue import register, request, row, snapshot, submit, wait_for
from check_haiku_runtime import fixture
from check_language_runtime import interpretation
from check_workshop_combat import SETTINGS, MOB, drive, end, enter, event

def session(base, sid):
    return next(s for s in snapshot(base)["sessions"] if s["session_id"] == sid)


def main():
    passed = []
    with fixture(enabled=False, combat_settings=SETTINGS) as f:
        base, _, log, control, seen, _, _, _, send, _, rows, _, folder = f
        sid = register(base, preview=False)
        completed_rows = []
        comparisons = 0

        def compare(expected, *, danger=False, retained=0, remaining=0):
            nonlocal comparisons
            actual = session(base, sid)
            assert actual["history"] == expected, (actual, expected)
            assert actual["history_retention"] == {
                "danger_active": danger, "retained_utterances": retained,
                "remaining_player_turns": remaining}, actual
            comparisons += 1

        def say(text, *, retain_seed=False, remaining=0):
            send(sid)
            before = len(seen)
            expected_history = session(base, sid)["history"]
            turn = submit(base, sid, text)
            reply = wait_for(lambda: row(base, turn, {"completed"}))
            # These short synthetic utterances need no normalization oracle.
            assert len(text) < 80 and len(reply["text"]) < 80
            completed_rows.extend([
                {"turn_id": turn, "role": "user", "text": text},
                {"turn_id": turn + ":reply", "role": "assistant", "text": reply["text"]},
            ])
            expected = completed_rows if retain_seed else completed_rows[-10:]
            compare(expected, retained=10 if retain_seed else 0, remaining=remaining)
            prompts = [r["body"] for r in seen[before:] if r["path"] == "/v1/chat/completions"]
            assert len(prompts) == 1, prompts
            prompt = json.dumps(prompts[0], ensure_ascii=False)
            for previous in expected_history:
                assert previous["text"] in prompt, (previous, prompt)
            return turn, prompts

        seed = [say(f"ドギド、今日の話その{i}だよ")[0] for i in range(5)]
        seed_rows = list(completed_rows)
        assert len(seed_rows) == 10
        enter(send, rows, sid)
        warning = next(r for r in rows(sid) if r["playback_status"] == "completed" and r.get("combat_actions"))
        completed_rows.append({"turn_id": warning["turn_id"] + ":reply",
                               "role": "assistant", "text": warning["text"]})
        compare(completed_rows, danger=True, retained=10)
        for _ in range(12):
            send(sid, visual_threats=[MOB], combat={"combat_active_hint": True})
        compare(completed_rows, danger=True, retained=10)
        aftermath = end(send, rows, sid)
        # The delivered aftermath is current conversation material, not a user/reply pair.
        assert aftermath["playback_status"] == "completed" and aftermath["text"]
        completed_rows.append({"turn_id": aftermath["turn_id"] + ":reply",
                               "role": "assistant", "text": aftermath["text"]})
        compare(completed_rows, retained=10, remaining=3)
        for i, text in enumerate(["ドギド、一段落したね", "ドギド、落ち着いたね", "さっきの話の続き"]):
            _, prompts = say(text, retain_seed=i < 2, remaining=2 - i)
            # 一回の本文生成にも戦闘前の退避分を渡す。
            leaf = prompts[0]
            assert "今日の話その0だよ" in json.dumps(leaf, ensure_ascii=False), leaf
            assert session(base, sid)["history_retention"]["remaining_player_turns"] == 2 - i
        _, prompts = say("ドギド、次の話をしよう")
        assert seed[0] not in json.dumps(prompts, ensure_ascii=False), prompts
        passed.append(f"synthetic_history_{comparisons}_snapshots_and_three_turn_prompt_lifetime")
        # Evaluation episodes are intentionally written separately from memory.
        # 通常雑談は保存せず、既存の戦闘の出来事だけは共有短期ログへ残る。
        persisted = {p.relative_to(folder / "memory").as_posix()
                     for p in (folder / "memory").rglob("*.jsonl")}
        short_path = f"sessions/{sid}/short_term/current_session.jsonl"
        assert persisted <= {"eval/episodes.jsonl", short_path}, persisted
        if short_path in persisted:
            short_rows = [json.loads(line) for line in (folder / "memory" / short_path).read_text().splitlines()]
            assert all(r["kind"] == "combat_fact" for r in short_rows), short_rows
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
