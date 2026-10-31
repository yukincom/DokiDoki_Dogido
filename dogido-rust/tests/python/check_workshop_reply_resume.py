#!/usr/bin/env python3
"""川柳の生成・保存・文途中を戦闘で保留し、同じworkerから一度だけ続行するHTTP検証。"""
import json
import fcntl
import re
import threading
import time

from check_dialogue import acknowledge, display_row, register, request, row, submit, wait_for
from check_workshop_fixture import fixture
from check_workshop_runtime import install, ready, session, step
from check_workshop_edits import edit, revisions
from check_workshop_combat import SETTINGS, MOB, actions, drive, end, enter, event, returned


def held(base, turn):
    result = row(base, turn, {"waiting_for_warning"})
    return result if result and result.get("reply_delivery") == "held_combat" else None


def start_enemy(send, sid, number=1):
    send(sid, visual_threats=[{**MOB, "entity_id": f"z{number}"}], combat={"combat_active_hint": True})


def finish_enemy(send, sid):
    send(sid, event=event("combat_ended"), combat={"hostile_outcomes": [], "hostiles_within_scan_ground": 0})


def audio_indices(text):
    return [int(n) for n in re.findall(r'event="audio_started" pid=Some\(\d+\) sentence=(\d+)', text)]


def check_generation_and_save(passed):
    with fixture(combat_settings=SETTINGS) as f:
        base, _, log, control, _, _, _, _, send, hud, rows, stored, folder = f
        entered, release = threading.Event(), threading.Event()
        def generate(text, prompt, count):
            entered.set()
            assert release.wait(timeout=15)
            return edit(text)
        calls = install(control, generate)
        sid = ready(base, send, rows)
        original = hud(sid)["canonical_lines"]
        turn = submit(base, sid, "上五を『さくらいろ』にして")
        try:
            wait_for(entered.is_set)
            enter(send, rows, sid)
            assert held(base, turn) and not revisions(folder, sid)
            release.set()
            # 結果を受け取っても、戦闘中には編集や保存へ進まない。
            for _ in range(4):
                start_enemy(send, sid)
                time.sleep(.06)
            assert held(base, turn) and not revisions(folder, sid)
            assert hud(sid)["canonical_lines"] == original and len(calls) == 1
            end(send, rows, sid)
            returned(send, rows, sid)
            result = drive(send, sid, lambda: row(base, turn, {"completed"}))
            assert result["workshop_outcome"] == "player_edit_saved", result
            assert len(calls) == 1 and len(revisions(folder, sid)) == 1
            assert hud(sid)["canonical_lines"][0] == "さくらいろ"
            assert len([r for r in session(base, sid)["workshop_history"] if r["turn_id"] == turn]) == 1
            passed.append("generation_result_waits_for_safety_and_saves_once_without_regeneration")
        finally:
            release.set()


def check_sentences(passed):
    with fixture(combat_settings=SETTINGS) as f:
        base, _, log, control, _, _, _, _, send, hud, rows, stored, folder = f
        sentences = ["ひとつめやで。", "ふたつめやで。", "みっつめやで。"]
        gates = {text: threading.Event() for text in sentences[1:]}
        control["tts_gates"].update(gates)
        calls = install(control, lambda text, prompt, n: step(text, speech="".join(sentences)))
        sid = ready(base, send, rows)
        before = stored(sid)
        offset = len(log.read_text())
        turn = submit(base, sid, "この句を説明して")
        wait_for(lambda: row(base, turn, {"started"}))
        wait_for(lambda: 'event="audio_stopped"' in log.read_text()[offset:])
        assert audio_indices(log.read_text()[offset:]) == [1]
        (folder / "player_mode").write_text("slow")
        gates[sentences[1]].set()
        wait_for(lambda: audio_indices(log.read_text()[offset:]) == [1, 2])
        (folder / "player_mode").write_text("ok")
        enter(send, rows, sid)
        assert held(base, turn)
        assert not any(r["turn_id"] == turn for r in session(base, sid)["workshop_history"])
        end(send, rows, sid)
        returned(send, rows, sid)
        wait_for(lambda: row(base, turn, {"started"}))
        # 二文目が完了し、まだ合成されていない三文目を待っている。
        wait_for(lambda: log.read_text().rfind('event="audio_stopped"') > log.read_text().rfind('event="audio_started"'))
        offset = len(log.read_text())
        (folder / "player_mode").write_text("slow")
        gates[sentences[2]].set()
        wait_for(lambda: audio_indices(log.read_text()[offset:]) == [3])
        (folder / "player_mode").write_text("ok")
        start_enemy(send, sid, 2)
        wait_for(lambda: held(base, turn))
        finish_enemy(send, sid)
        notice = returned(send, rows, sid, count=2)
        result = drive(send, sid, lambda: row(base, turn, {"completed"}))
        # 最終の復帰案内より後は三文目だけ。完了した一・二文目は読み直さない。
        final = log.read_text().split(f'turn_id="{notice["turn_id"]}" playback_status="completed"')[-1]
        assert audio_indices(final) == [3], final
        assert len(calls) == 1 and result["text"] == "".join(sentences)
        history = [r for r in session(base, sid)["workshop_history"] if r["turn_id"] == turn]
        assert len(history) == 1 and history[0]["dogido_text"] == result["text"], history
        assert len([r for r in rows(sid) if r["turn_id"] == turn]) == 1
        assert stored(sid) == before and not revisions(folder, sid)
        passed.append("two_audio_interrupts_resume_unfinished_sentence_with_one_reply_and_history")


def check_stop(passed):
    with fixture(combat_settings=SETTINGS) as f:
        base, _, _, control, _, _, _, _, send, hud, rows, _, folder = f
        calls = install(control, lambda text, prompt, n: step(text))
        sid = ready(base, send, rows)
        (folder / "player_mode").write_text("slow")
        turn = submit(base, sid, "この句を説明して")
        wait_for(lambda: row(base, turn, {"started"}))
        (folder / "player_mode").write_text("ok")
        enter(send, rows, sid)
        assert held(base, turn)
        request(base, "/api/v1/rust-dialogue/interrupt", {"session_id": sid})
        wait_for(lambda: row(base, turn, {"cancelled"}))
        finish_enemy(send, sid)
        returned(send, rows, sid)
        for _ in range(4):
            send(sid)
            time.sleep(.06)
        assert row(base, turn, {"cancelled"}) and len(calls) == 1
        assert not any(r["turn_id"] == turn for r in session(base, sid)["workshop_history"])
        passed.append("manual_stop_terminates_held_reply_without_resurrection")


def check_text(passed):
    with fixture(combat_settings=SETTINGS) as f:
        base, _, _, control, _, _, _, _, send, hud, rows, stored, folder = f
        calls = install(control, lambda text, prompt, n: step(text))
        sid = register(base, preview=False, voice_enabled=False, body=True)
        send(sid)
        acknowledged = set()
        def ack_except(turn=None):
            for r in rows(sid):
                ident = r.get("display_id")
                if ident and ident not in acknowledged and r["turn_id"] != turn:
                    acknowledge(base, sid, r)
                    acknowledged.add(ident)
        def poem_ready():
            ack_except()
            return any(r["turn_id"].endswith(":poem") and r["playback_status"] == "audio_disabled" for r in rows(sid))
        wait_for(poem_ready)
        turn = submit(base, sid, "この句を説明して", source="text")
        original = wait_for(lambda: display_row(base, turn))
        start_enemy(send, sid)
        wait_for(lambda: held(base, turn))
        assert submit(base, sid, "この句を説明して", source="text") == turn
        rejected = request(base, "/api/v1/rust-dialogue/displayed", {"session_id":sid, "display_ids":[original["display_id"]]})
        assert rejected["rejected"] == [original["display_id"]] and not rejected["displayed"], rejected
        deadline = time.monotonic() + 5.5
        while time.monotonic() < deadline:
            start_enemy(send, sid)
            ack_except(turn)
            time.sleep(.1)
        assert held(base, turn) and not session(base, sid)["workshop_history"]
        finish_enemy(send, sid)
        def resumed_display():
            ack_except(turn)
            r = display_row(base, turn)
            return r if r and r["display_id"] != original["display_id"] else None
        resumed = drive(send, sid, resumed_display)
        assert submit(base, sid, "この句を説明して", source="text") == turn
        acknowledge(base, sid, resumed)
        acknowledge(base, sid, resumed)
        result = wait_for(lambda: row(base, turn, {"audio_disabled"}))
        assert result["delivery_confirmed"] and len(calls) == 1, result
        history = [r for r in session(base, sid)["workshop_history"] if r["turn_id"] == turn]
        assert len(history) == 1 and history[0]["dogido_text"] == step("")["speech"], history
        passed.append("text_resume_renews_ack_and_timeout_after_long_combat_without_duplicate_input")


def check_confirmed_text(passed):
    with fixture(combat_settings=SETTINGS) as f:
        base, _, _, control, _, _, _, _, send, hud, rows, stored, folder = f
        calls = install(control, lambda text, prompt, n: step(text))
        sid = register(base, preview=False, voice_enabled=True, body=True)
        send(sid)
        wait_for(lambda: any(r["turn_id"].endswith(":poem") and r["playback_status"] == "completed" for r in rows(sid)))
        (folder / "player_mode").write_text("slow")
        turn = submit(base, sid, "この句を説明して", source="text")
        wait_for(lambda: row(base, turn, {"started"}))
        original = display_row(base, turn)
        acknowledge(base, sid, original)
        assert len(session(base, sid)["workshop_history"]) == 1
        (folder / "player_mode").write_text("ok")
        enter(send, rows, sid)
        wait_for(lambda: row(base, turn, {"cancelled"}))
        end(send, rows, sid)
        returned(send, rows, sid)
        assert row(base, turn, {"cancelled"}) and len(calls) == 1
        history = [r for r in session(base, sid)["workshop_history"] if r["turn_id"] == turn]
        assert len(history) == 1 and history[0]["dogido_text"] == step("")["speech"], history
        passed.append("already_confirmed_text_is_not_replayed_or_recorded_twice")


def check_save_waits(passed):
    for kind, text, filename, log_event in [
        ("edit", "上五を『さくらいろ』にして", "revisions", "workshop_revision_save_started"),
        ("whole", "直し: はる/さくら/あさ", "revisions", "poem_input_save_started"),
        ("lessons", "前の注意やめて", "lessons", "haiku_lessons_clear_started"),
    ]:
        with fixture(combat_settings=SETTINGS) as f:
            base, _, log, control, _, _, _, _, send, hud, rows, stored, folder = f
            calls = install(control, lambda text, prompt, n: step(text, "show_current") if text == "今の句" else
                            {"action":"clear_lessons"} if kind == "lessons" else edit(text))
            sid = ready(base, send, rows)
            path = folder / "memory" / ("long_term/haiku_lessons.jsonl" if kind == "lessons" else f"sessions/{sid}/long_term/haiku_revisions.jsonl")
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a+") as locked:
                fcntl.flock(locked, fcntl.LOCK_EX)
                try:
                    turn = submit(base, sid, text)
                    wait_for(lambda: log_event in log.read_text())
                    enter(send, rows, sid)
                    assert held(base, turn) and not path.read_text()
                    assert submit(base, sid, text) == turn
                finally:
                    fcntl.flock(locked, fcntl.LOCK_UN)
            wait_for(lambda: path.read_text())
            assert len(path.read_text().splitlines()) == 1
            # 保存済みの操作も、返答の再開で再実行しない。
            end(send, rows, sid)
            if kind != "whole":
                returned(send, rows, sid)
            result = drive(send, sid, lambda: row(base, turn, {"completed"}))
            assert len(path.read_text().splitlines()) == 1 and len(calls) == (0 if kind == "whole" else 1), result
            assert result.get("workshop_outcome", result.get("memory_outcome")) in {"player_edit_saved", "saved"}, result
            if kind == "whole":
                assert hud(sid)["state"] == "closed" and revisions(folder, sid)[0]["revised_text"] == "はる\nさくら\nあさ", hud(sid)
            passed.append(kind + "_save_wait_yields_to_combat_and_ack_resumes_without_second_write")


def check_saved_reply_and_overlapping_input(passed):
    with fixture(combat_settings=SETTINGS) as f:
        base, _, _, control, _, _, _, _, send, hud, rows, stored, folder = f
        calls = install(control, lambda text, prompt, n: step(text, "show_current") if text == "今の句" else edit(text))
        sid = ready(base, send, rows)
        (folder / "player_mode").write_text("slow")
        turn = submit(base, sid, "上五を『さくらいろ』にして")
        wait_for(lambda: row(base, turn, {"started"}))
        assert len(revisions(folder, sid)) == 1
        # 見込み: 編集結果の音声中に次の相談を生成し、保存済みの編集は再実行しない。
        # 合格: 元音声startedのまま次がqueued。その後の戦闘も両返答を再開し保存1件。BERT・モデル・音声は模擬。
        queued_turn = submit(base, sid, "今の句")
        wait_for(lambda: row(base, queued_turn, {"queued"}), timeout=3)
        assert row(base, turn, {"started"}) and len(calls) == 2
        (folder / "player_mode").write_text("ok")
        enter(send, rows, sid)
        assert held(base, turn)
        end(send, rows, sid)
        returned(send, rows, sid)
        drive(send, sid, lambda: row(base, turn, {"completed"}))
        newer = drive(send, sid, lambda: row(base, queued_turn, {"completed"}))
        assert len(revisions(folder, sid)) == 1 and len(calls) == 2, (newer,calls)
        assert newer["text"].startswith("さくらいろ"), newer
        assert len([h for h in session(base,sid)["workshop_history"] if h["turn_id"] == turn]) == 1
        passed.append("saved_edit_output_and_next_input_overlap_then_resume_without_second_save")


def check_lifetime(passed):
    for cause in ["death", "dimension", "voice_off", "disconnect"]:
        with fixture(combat_settings=SETTINGS) as f:
            base, _, _, control, _, _, _, _, send, hud, rows, stored, folder = f
            calls = install(control, lambda text, prompt, n: step(text))
            sid = register(base, preview=False, voice_enabled=True, body=True)
            send(sid)
            wait_for(lambda: any(r["turn_id"].endswith(":poem") and r["playback_status"] == "completed" for r in rows(sid)))
            (folder / "player_mode").write_text("slow")
            turn = submit(base, sid, "この句を説明して", source="text")
            wait_for(lambda: row(base, turn, {"started"}))
            (folder / "player_mode").write_text("ok")
            enter(send, rows, sid)
            assert held(base, turn)
            if cause == "death":
                send(sid, event=event("player_died"), player={"name":"試験","dimension":"minecraft:overworld","health":0})
            elif cause == "dimension":
                send(sid, player={"name":"試験","dimension":"minecraft:the_nether"})
            elif cause == "voice_off":
                request(base, "/api/v1/rust-dialogue/voice", {"session_id":sid,"enabled":False})
            else:
                request(base, "/api/v1/adapter-sessions/" + sid, method="DELETE")
            wait_for(lambda: row(base, turn, {"cancelled"}))
            if cause != "disconnect":
                for _ in range(5):
                    send(sid)
                    time.sleep(.08)
                assert not any(h["turn_id"] == turn for h in session(base,sid)["workshop_history"])
            assert row(base, turn, {"cancelled"}) and len(calls) == 1
            passed.append(cause + "_terminates_held_reply_without_resurrection")


def main():
    passed = []
    check_generation_and_save(passed)
    check_sentences(passed)
    check_stop(passed)
    check_text(passed)
    check_confirmed_text(passed)
    check_save_waits(passed)
    check_saved_reply_and_overlapping_input(passed)
    check_lifetime(passed)
    print(json.dumps({"passed": passed, "all_owned_processes_stopped": True}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
