#!/usr/bin/env python3
"""共有履歴を実HTTPと模擬モデル/音声で確認する。外部モデルは呼ばない。"""
import json
from check_dialogue import wait_for
from check_haiku_runtime import fixture
from check_workshop_runtime import ready, session, install, step
from check_workshop_edits import finish
from check_workshop_combat import SETTINGS, enter, end, returned, event


def short_records(folder, sid):
    p = folder / "memory/sessions" / sid / "short_term/current_session.jsonl"
    return [json.loads(line) for line in p.read_bytes().splitlines(keepends=True) if line.endswith(b"\n")] if p.exists() else []


def main():
    passed = []
    with fixture(combat_settings=SETTINGS) as context:
        base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder = context
        sid = ready(base, send, rows)
        install(control, lambda text, prompt, n: step(text, speech="葉の明るさと斧の黒さ、その余韻を一緒に考えたんやで。"))
        reply = finish(base, send, sid, "この句の余韻について話したい")
        saved = wait_for(lambda: next((r for r in short_records(folder, sid) if r.get("kind") == "workshop_dialogue"), None))
        assert saved["workshop_id"] == hud(sid)["workshop_id"], saved
        assert saved["details"]["dogido_text"] == reply["text"] and saved["details"]["player_text"] == "この句の余韻について話したい", saved
        install(control, lambda text, prompt, n: step(text, "unrelated"))
        offset = len(seen)
        ordinary = finish(base, send, sid, "今日はどんな気分？")
        assert ordinary["workshop_action"] == "unrelated", ordinary
        requests = [r["body"] for r in seen[offset:] if r["path"] == "/v1/chat/completions"]
        planner = next(r for r in requests if r["max_tokens"] == 640)
        leaf = next(r for r in requests if r["max_tokens"] not in {420, 640})
        for r in [planner, leaf]:
            text = json.dumps(r["messages"], ensure_ascii=False)
            assert "この句の余韻について話したい" in text and reply["text"] in text, text
        passed.append("delivered_workshop_dialogue_reaches_normal_planner_leaf_and_same_short_log")

    with fixture(combat_settings=SETTINGS) as context:
        base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder = context
        sid = ready(base, send, rows)
        enter(send, rows, sid)
        outcomes = [
            ("hostile_defeated", "zombie", "z1", "player_kill", "ゾンビを倒した。"),
            ("creeper_detonated", "creeper", "c2", "creeper_detonation", "クリーパーが爆発した。"),
            ("hostile_defeated", "skeleton", "s3", "explosion_death", "スケルトンが爆発で倒れた。"),
        ]
        for name, kind, identity, outcome, text in outcomes:
            send(sid, event=event(name), combat={"hostile_outcomes":[{"type":kind, "entity_id":identity,
                "outcome":outcome,"evidence":"explosion_packet" if outcome=="creeper_detonation" else "server_death_event"}]})
            wait_for(lambda: any(r.get("kind") == "combat_fact" and r["text"] == text.rstrip("。") for r in short_records(folder, sid)))
        end(send, rows, sid)
        returned(send, rows, sid)
        common = session(base, sid)["history"]
        assert sum(r["text"] == "句の話に戻る。" for r in common) == 1, common
        facts = wait_for(lambda: [r for r in short_records(folder, sid) if r.get("kind") == "combat_fact"])
        assert all(any(text.rstrip("。") == r["text"] for r in facts) for *_, text in outcomes), facts
        returned_record = wait_for(lambda: next((r for r in short_records(folder, sid) if r.get("kind") == "workshop_return"), None))
        assert returned_record["text"] == "句の話に戻る。" and returned_record["workshop_id"] == hud(sid)["workshop_id"], returned_record
        assert not any(r.get("kind") == "combat" for r in short_records(folder, sid))
        calls = install(control, lambda text, prompt, n: step(text, speech="さっきの句の続き、聞かせてな。"))
        finish(base, send, sid, "さっきの続きにしよう")
        prompt = calls[-1][1]
        assert all(text.rstrip("。") in prompt for *_, text in outcomes), prompt
        assert "会話段階: combat_resume_confirmation" in prompt, prompt
        assert "句の話に戻る。" not in prompt, "既存の復帰段階と別の復帰指示を二重注入している"
        assert len(calls) == 1
        passed.append("existing_combat_facts_and_resume_stage_reach_workshop_through_one_bridge")

    with fixture(combat_settings=SETTINGS, memory_enabled=False) as context:
        base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder = context
        sid = ready(base, send, rows)
        install(control, lambda text, prompt, n: step(text))
        result = finish(base, send, sid, "この句の話をしよう")
        assert any(result["text"] in r["text"] and r["reaction"] == "story" for r in session(base, sid)["history"])
        assert not list((folder / "memory").rglob("*.jsonl"))
        passed.append("disabled_persistence_keeps_only_in_memory_story")
    print(json.dumps({"passed":len(passed),"checks":passed}, ensure_ascii=False))


if __name__ == "__main__":
    main()
