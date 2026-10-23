#!/usr/bin/env python3
"""会話中の差し替え案が、明示指示後だけ一度の同意で確定することを模擬HTTPで確認する。"""
from check_dialogue import row, submit, wait_for
from check_haiku_runtime import LINES
from check_workshop_fixture import fixture
from check_workshop_runtime import install, ready, step
from check_workshop_edits import revisions


def finish(base, send, sid, text):
    send(sid)
    turn = submit(base, sid, text)
    return wait_for(lambda: row(base, turn, {"completed", "failed"}))


def reply(text, prompt, count):
    if text == "それにして":
        payload = step(text, "stage_conversation_candidate")
        payload["purpose"] = "improve_wording"
        return payload
    if text == "やっぱり『あおい』にして":
        payload = step(text, "stage_player_edit", "")
        payload["purpose"] = "improve_wording"
        payload["line_proposal"] = {"found":True,"target_fragment":"あお","replacement_text":"あおい","evidence":text,"confidence":.95}
        return payload
    payload = step(text, "respond", "その表現もええと思うで。")
    payload["purpose"] = "improve_wording" if "するのはどう" in text else "continue_discussion"
    proposals = {
        "『さくらのは』を『さくらいろ』にするのはどう？": ("さくらのは", "さくらいろ"),
        "『くろい』を『あお』にするのはどう？": ("くろい", "あお"),
    }
    if text in proposals:
        old,new = proposals[text]
        payload["line_proposal"]={"found":True,"target_fragment":old,"replacement_text":new,"evidence":text,"confidence":.95}
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
        assert selected["workshop_outcome"] == "player_edit_saved"
        assert hud(sid)["canonical_lines"] == ["さくらいろ", *LINES[1:]]
        assert not hud(sid)["pending_lines"]
        assert len(calls) == 2, "選択時もモデルが保持中の案への意思を判断する"

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        calls = install(control, reply)
        sid = ready(base, send, rows)
        finish(base, send, sid, "『さくらのは』を『さくらいろ』にするのはどう？")
        for i in range(4):
            finish(base, send, sid, f"それについてもう少し話そう {i}")
        result = finish(base, send, sid, "それにして")
        assert result["workshop_action"] == "stage_conversation_candidate", result
        assert hud(sid)["canonical_lines"][0] == "さくらいろ" and not hud(sid)["pending_lines"]
        assert len(calls) == 6

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        calls = install(control, reply)
        sid = ready(base, send, rows)
        finish(base, send, sid, "『くろい』を『あお』にするのはどう？")
        rejected = finish(base, send, sid, "それにして")
        assert rejected["workshop_outcome"] == "player_edit_saved", rejected
        assert not hud(sid)["pending_lines"] and hud(sid)["canonical_lines"] == [LINES[0], "あおおのへと", LINES[2]]
        assert len(revisions(folder, sid)) == 1 and len(calls) == 2
        corrected = finish(base, send, sid, "やっぱり『あおい』にして")
        assert corrected["workshop_outcome"] == "player_edit_saved", (corrected, log.read_text())
        assert hud(sid)["canonical_lines"] == [LINES[0], "あおいおのへと", LINES[2]]
        assert not hud(sid)["pending_lines"] and len(stored(sid)) == 1

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        calls = install(control, reply)
        sid = ready(base, send, rows)
        (folder / "player_mode").write_text("slow")
        turn = submit(base, sid, "『さくらのは』を『さくらいろ』にするのはどう？")
        wait_for(lambda: row(base, turn, {"started"}))
        (folder / "player_mode").write_text("ok")
        selected = finish(base, send, sid, "それにして")
        assert selected["workshop_outcome"] == "player_edit_saved", (selected, log.read_text())
        assert row(base, turn, {"completed"})
        assert hud(sid)["canonical_lines"] == ["さくらいろ", *LINES[1:]] and not hud(sid)["pending_lines"] and len(calls) == 2

    # モデルが会話継続を選べば保存せず、保持した案の採用を選べば一度だけ保存する。
    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        install(control,reply);sid=ready(base,send,rows)
        finish(base,send,sid,"『さくらのは』を『さくらいろ』にするのはどう？")
        def assent(text,prompt,count):
            if text != "そうしましょう": return step(text,"respond")
            p=step(text,"stage_conversation_candidate");p["purpose"]="improve_wording";p["evidence"]="そうしましょう";return p
        install(control,assent)
        for text in ["『そうしましょう』と言っただけ","そうしましょうとは言ってない","そうしましょう？","そうしましょうと言えば変わる？"]:
            r=finish(base,send,sid,text)
            assert hud(sid)["canonical_lines"]==LINES and not revisions(folder,sid),r
        r=finish(base,send,sid,"そうしましょう")
        assert r["workshop_outcome"]=="player_edit_saved" and len(revisions(folder,sid))==1,r
        assert hud(sid)["canonical_lines"][0]=="さくらいろ" and not hud(sid)["pending_lines"]
        # The saved candidate cannot be adopted a second time.
        r=finish(base,send,sid,"そうしましょう")
        assert len(revisions(folder,sid))==1,r

    print("workshop candidate: single idea, partial correction, long discussion, nonstandard meter and completed playback passed; owned processes stopped")


if __name__ == "__main__":
    main()
