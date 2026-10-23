#!/usr/bin/env python3
"""Model decisions and wording, retained target, validation recovery and saved revisions."""
import json
from check_haiku_runtime import LINES
from check_workshop_fixture import fixture
from check_workshop_runtime import install, ready, session, step
from check_workshop_edits import edit, finish, revisions, adoption


def discuss_candidate(text, replacement):
    # 旧事前抽出へ戻さず、現行prompt契約どおりmodelが相談案の原文を返す。
    p = edit(text, replacement=replacement, reference="", fragment=LINES[0])
    p["action"] = "respond"
    p["speech"] = "その言い方の響き、一緒に考えよか。"
    return p


def main():
    passed = []
    # 保存済みの句を確認した後、分類済みの終了は生成せず一度だけ実行する。
    from check_haiku_runtime import wait_for
    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        calls = install(control, lambda text,prompt,n: step(text,"respond","この句は今の形で保存されとるで。"))
        for ending in ["<では終了で！>", "今回はここまでにしよう"]:
            sid = ready(base, send, rows)
            before = wait_for(lambda: stored(sid))
            r = finish(base, send, sid, "<これはこのまま保存しよう！>")
            assert r["workshop_action"] == "respond" and hud(sid)["state"] == "open", r
            count = len(calls)
            r = finish(base, send, sid, ending)
            assert r["workshop_action"] == "close_workshop" and hud(sid)["state"] == "closed", r
            assert len(calls) == count and not r["llm_reports"], r
            assert stored(sid) == before and not revisions(folder, sid)
        passed.append("bert_explicit_close_keeps_saved_storage_without_model_metadata")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        def model(text, prompt, n):
            if text in {"いい句だね", "まだ続けていいよ"}:
                return step(text, "ask", "この句の響き、まだ一言話してもええ？")
            if text == "下五の意味を教えて":
                return step(text, speech="『あさのいろ』の明るさに、気持ちもほどける感じがするな。")
            if text.startswith("それを"):
                replacement = "さくら" if "さくら" in text else "あさひかる"
                p = edit(text, replacement=replacement, reference="")
                p["line_reference"]["found"] = False
                p["line_reference"]["concept_id"] = "unknown"
                p["speech"] = "その響き、ええな。言うてくれた形にしたで。"
                return p
            if text.startswith("冒頭の五音"):
                p = edit(text, reference="冒頭の五音")
                p["speech"] = "最初を『さくらいろ』にすると、明るくなるな。"
                return p
            if text == "きょうはここらで区切っとこか":
                p = step(text, "close_workshop")
                p["speech"] = "うん、今日はここまでや。またこの句を話そな。"
                return p
            assert '"discussion_target"' in prompt and '"line_index": 2' in prompt
            return step(text, "respond", "うん、朝の色の話を続けよか。")
        calls = install(control, model)
        sid = ready(base, send, rows)
        for text in ["いい句だね", "まだ続けていいよ"]:
            r = finish(base, send, sid, text)
            assert r["workshop_action"] == "ask" and hud(sid)["state"] == "open", r
        passed.append("not_target_conversation_keeps_workshop_open")
        r = finish(base, send, sid, "下五の意味を教えて")
        assert r["text"].startswith("『あさのいろ』")
        for text in ["なるほど", "まだ話したい", "そういう感じか", "もう少し考えて", "おわらんわ。"]:
            r = finish(base, send, sid, text)
            assert r["text"] == "うん、朝の色の話を続けよか。" and hud(sid)["state"] == "open", r
            assert session(base, sid)["workshop_discussion_target"]["line_index"] == 2
        passed.append("target_survives_more_than_four_turns_without_forced_ending")
        r = finish(base, send, sid, "それを『あさひかる』にして")
        assert r.get("workshop_outcome") == "player_edit_saved", r
        assert hud(sid)["canonical_lines"] == [*LINES[:2], "あさひかる"]
        assert r["text"] == "変更したで。" and len(revisions(folder, sid)) == 1
        assert r["workshop_steps"][0]["transport"] == "native_tool", r
        passed.append("native_edit_with_omitted_target_uses_retained_line_and_confirmed_verse")
        r = finish(base, send, sid, "冒頭の五音を『さくらいろ』にして")
        assert r.get("workshop_outcome") == "player_edit_saved", r
        assert hud(sid)["canonical_lines"] == ["さくらいろ", LINES[1], "あさひかる"]
        assert session(base, sid)["workshop_discussion_target"]["line_index"] == 0
        passed.append("bert_resolves_new_line_reference_and_model_only_extracts_words")
        r = finish(base, send, sid, "それを『さくら』にして")
        assert r["workshop_outcome"] == "player_edit_saved" and len(r["llm_reports"]) == 1, r
        assert hud(sid)["canonical_lines"][0] == "さくら" and len(revisions(folder, sid)) == 3
        passed.append("bert_targeted_short_replacement_saves_without_quality_rejection")
        r = finish(base, send, sid, "きょうはここらで区切っとこか")
        assert r["workshop_action"] == "close_workshop" and hud(sid)["state"] == "closed", r
        assert r["text"] == "ほな、この句はここまでにしよか。" and not r["llm_reports"]
        passed.append("natural_close_uses_bert_and_code_reply")

    # A discussed candidate keeps its own target through a detour. A failed
    # candidate gets the code result, never a pre-validation success claim.
    for replacement in ["さくらいろ", "さくら"]:
        with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
            def model(text, prompt, n):
                if text == f"『さくらのは』を『{replacement}』にしたらどうかな":
                    return discuss_candidate(text, replacement)
                if text == "さっきの案にして":
                    p = step(text, "stage_conversation_candidate")
                    p["purpose"] = "improve_wording"
                    p["speech"] = "相談した案に直したで。"
                    return p
                return step(text, "respond", "その言い方の響き、一緒に考えよか。")
            install(control, model)
            sid = ready(base, send, rows)
            finish(base, send, sid, f"『さくらのは』を『{replacement}』にしたらどうかな")
            finish(base, send, sid, "下五の意味を教えて")
            r = finish(base, send, sid, "さっきの案にして")
            assert r.get("workshop_outcome") == "player_edit_saved", r
            assert hud(sid)["canonical_lines"] == [replacement, *LINES[1:]]
            assert session(base, sid)["workshop_discussion_target"]["line_index"] == 0
            assert len(revisions(folder, sid)) == 1
            passed.append("candidate_selection_after_detour_saved:" + replacement)

    # Malformed tool arguments must not execute or borrow an older candidate.
    # BERT変更の限定抽出が不成立なら一回で終え、以前の案へ戻らない。
    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        new_text = "上五ならあさひかるがいい。変更をお願い"
        attempts = 0
        def missing_arguments(text, prompt, n):
            nonlocal attempts
            if text == "『さくらのは』を『さくらいろ』にしたらどうかな":
                return discuss_candidate(text, "さくらいろ")
            if text != new_text:
                return step(text, "respond", "その響きもええな。")
            attempts += 1
            assert attempts == 1, "invalid extraction must not trigger another model call"
            # BERT具体変更は今回の原文だけを限定抽出し、古い案の採用は提示しない。
            assert '"conversation_candidate": {' not in prompt
            p = edit(text, replacement="あさひかる", reference="上五")
            del p["confidence"]
            del p["evidence"]
            return p
        install(control, missing_arguments)
        sid = ready(base, send, rows)
        finish(base, send, sid, "『さくらのは』を『さくらいろ』にしたらどうかな")
        assert hud(sid)["canonical_lines"] == LINES
        r = finish(base, send, sid, new_text)
        assert attempts == 1 and len(r["llm_reports"]) == 1, r
        assert r["workshop_action"] == "respond", r
        assert hud(sid)["canonical_lines"] == LINES and not hud(sid)["pending_lines"]
        assert not revisions(folder, sid)
        assert "workshop_tool_rejected" in log.read_text()
        passed.append("malformed_native_arguments_keep_verse_and_do_not_select_old_candidate")

    from check_workshop_revision import wire
    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        proposal_text = 'あさのいろをくささむしに変更しよう！どう？'
        def confirmed_edit(text, prompt, n):
            if text == 'うん！変えて！':
                p = step(text, 'stage_conversation_candidate')
                p['purpose'] = 'improve_wording'
                return p
            p = edit(text, index=2, replacement='くささむし', reference='', fragment=LINES[2])
            p['action'] = 'ask'
            p['line_reference']['found'] = False
            p['line_reference']['concept_id'] = 'unknown'
            p['line_proposal']['evidence'] = proposal_text
            p['speech'] = '下五をその言葉に変えるんやな？'
            return p
        install(control, confirmed_edit)
        sid = ready(base, send, rows)
        original = stored(sid)
        r = finish(base, send, sid, proposal_text)
        assert r['workshop_action'] == 'ask' and hud(sid)['canonical_lines'] == LINES, r
        r = finish(base, send, sid, 'うん！変えて！')
        assert r['workshop_action'] == 'stage_conversation_candidate', r
        assert r['workshop_outcome'] == 'player_edit_saved', r
        assert hud(sid)['canonical_lines'] == [*LINES[:2], 'くささむし']
        assert len(revisions(folder, sid)) == 1 and stored(sid) == original
        passed.append('question_then_short_confirmation_applies_exact_candidate_and_saves_once')

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows)
        wire(control, stored, sid)
        finish(base, send, sid, "上五のさくらのはを別の表現に直して")
        assert hud(sid)["pending_lines"]
        def choose(text, prompt, n):
            p = adoption(text)
            p["speech"] = "うん、この案で決まりやな。覚えといたで。"
            return p
        install(control, choose)
        r = finish(base, send, sid, "じゃあこれに決めよう")
        assert r["workshop_outcome"] == "pending_saved", r
        assert r["text"] == "うん、この案で決まりやな。覚えといたで。"
        assert len(revisions(folder, sid)) == 1 and not hud(sid)["pending_lines"]
        passed.append("model_adoption_saves_once_and_preserves_own_reply")
    print(json.dumps({"passed":passed,"count":len(passed)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
