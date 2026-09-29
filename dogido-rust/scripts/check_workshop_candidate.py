#!/usr/bin/env python3
"""会話中の差し替え案が、明示指示後だけ未採用案になることを模擬HTTPで確認する。"""
from check_dialogue import row, submit, wait_for
from check_haiku_runtime import LINES, fixture
from check_workshop_runtime import install, ready, step


def finish(base, send, sid, text):
    send(sid)
    turn = submit(base, sid, text)
    return wait_for(lambda: row(base, turn, {"completed", "failed"}))


def reply(text, prompt, count):
    if text == "やっぱり『あおい』にして":
        payload = step(text, "stage_player_edit", "")
        payload["purpose"] = "improve_wording"
        payload["line_proposal"] = {"found":True,"target_fragment":"","replacement_text":"あおい","evidence":text,"confidence":.95}
        return payload
    payload = step(text, "respond", "その表現もええと思うで。")
    payload["purpose"] = "improve_wording" if "するのはどう" in text else "continue_discussion"
    return payload


def main():
    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        calls = install(control, reply)
        sid = ready(base, send, rows)
        result = finish(base, send, sid, "『さくらのは』を『さくらいろ』にするのはどう？")
        assert result["workshop_action"] == "respond", (result, log.read_text())
        assert hud(sid)["canonical_lines"] == LINES and not hud(sid)["pending_lines"]
        assert len(calls) == 1
        selected = finish(base, send, sid, "それにして")
        assert selected["workshop_action"] == "stage_conversation_candidate", (selected, log.read_text())
        assert selected["workshop_outcome"] == "player_edit_staged"
        assert hud(sid)["canonical_lines"] == LINES
        assert hud(sid)["pending_lines"] == ["さくらいろ", *LINES[1:]]
        assert len(calls) == 1, "選択時は追加のモデル呼出しをしない"

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        calls = install(control, reply)
        sid = ready(base, send, rows)
        finish(base, send, sid, "『さくらのは』を『さくらいろ』にするのはどう？")
        for i in range(4):
            finish(base, send, sid, f"それについてもう少し話そう {i}")
        result = finish(base, send, sid, "それにして")
        assert result["workshop_action"] == "stage_conversation_candidate", result
        assert hud(sid)["canonical_lines"] == LINES and hud(sid)["pending_lines"][0] == "さくらいろ"
        assert len(calls) == 5

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        calls = install(control, reply)
        sid = ready(base, send, rows)
        finish(base, send, sid, "『くろい』を『あお』にするのはどう？")
        rejected = finish(base, send, sid, "それにして")
        assert rejected["workshop_outcome"] == "player_edit_rejected", rejected
        assert "meter_not_exact" in str(rejected["workshop_steps"])
        assert not hud(sid)["pending_lines"]
        corrected = finish(base, send, sid, "やっぱり『あおい』にして")
        assert corrected["workshop_outcome"] == "player_edit_staged", (corrected, log.read_text())
        assert hud(sid)["pending_lines"] == [LINES[0], "あおいおのへと", LINES[2]]
        assert hud(sid)["canonical_lines"] == LINES and len(stored(sid)) == 1

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        calls = install(control, reply)
        sid = ready(base, send, rows)
        (folder / "player_mode").write_text("slow")
        turn = submit(base, sid, "『さくらのは』を『さくらいろ』にするのはどう？")
        wait_for(lambda: row(base, turn, {"started"}))
        (folder / "player_mode").write_text("ok")
        selected = finish(base, send, sid, "それにして")
        assert selected["workshop_outcome"] == "player_edit_staged", (selected, log.read_text())
        assert row(base, turn, {"cancelled"})
        assert hud(sid)["pending_lines"] == ["さくらいろ", *LINES[1:]] and len(calls) == 1

    print("workshop candidate: single idea, partial correction, long discussion, rejected meter and interrupted playback passed; owned processes stopped")


if __name__ == "__main__":
    main()
