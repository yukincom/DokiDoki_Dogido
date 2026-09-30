#!/usr/bin/env python3
"""Model decisions and wording, retained target, validation recovery and saved revisions."""
import json
from check_haiku_runtime import fixture, LINES
from check_workshop_runtime import install, ready, session, step
from check_workshop_edits import edit, finish, revisions, adoption


def main():
    passed = []
    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        def model(text, prompt, n):
            if text in {"いい句だね", "終了でいいよ"}:
                return step(text, "ask", "この句の響き、まだ一言話してもええ？")
            if text == "下五の意味を教えて":
                return step(text, speech="『あさのいろ』の明るさに、気持ちもほどける感じがするな。")
            if "段階: after_validation" in prompt:
                assert '"status": "rejected"' in prompt
                return step(text, "respond", "『さくら』だけやと三音になるな。今の上五からもう少し一緒に考えよか。")
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
        for text in ["いい句だね", "終了でいいよ"]:
            r = finish(base, send, sid, text)
            assert r["workshop_action"] == "ask" and hud(sid)["state"] == "open", r
        passed.append("submission_does_not_close_before_model_selects_action")
        r = finish(base, send, sid, "下五の意味を教えて")
        assert r["text"].startswith("『あさのいろ』")
        for text in ["なるほど", "まだ話したい", "そういう感じか", "もう少し考えて", "おわらんわ。"]:
            r = finish(base, send, sid, text)
            assert r["text"] == "うん、朝の色の話を続けよか。" and hud(sid)["state"] == "open", r
            assert session(base, sid)["workshop_discussion_target"]["line_index"] == 2
        passed.append("target_survives_more_than_four_turns_without_forced_ending")
        r = finish(base, send, sid, "それを『あさひかる』にして")
        assert r["workshop_outcome"] == "player_edit_saved", r
        assert hud(sid)["canonical_lines"] == [*LINES[:2], "あさひかる"]
        assert r["text"].startswith("その響き、ええな。") and len(revisions(folder, sid)) == 1
        passed.append("model_edit_with_omitted_target_uses_retained_line_and_own_reply")
        r = finish(base, send, sid, "冒頭の五音を『さくらいろ』にして")
        assert r["workshop_outcome"] == "player_edit_saved", r
        assert hud(sid)["canonical_lines"] == ["さくらいろ", LINES[1], "あさひかる"]
        assert session(base, sid)["workshop_discussion_target"]["line_index"] == 0
        passed.append("model_resolves_new_line_reference_outside_fixed_aliases")
        r = finish(base, send, sid, "それを『さくら』にして")
        assert r["workshop_action"] == "respond" and len(r["llm_reports"]) == 2, r
        assert r["text"].startswith("『さくら』だけやと三音") and len(revisions(folder, sid)) == 2
        passed.append("failed_edit_returns_observation_to_model_and_keeps_canonical")
        r = finish(base, send, sid, "きょうはここらで区切っとこか")
        assert r["workshop_action"] == "close_workshop" and hud(sid)["state"] == "closed", r
        assert r["text"] == "うん、今日はここまでや。またこの句を話そな。"
        passed.append("natural_close_uses_model_selected_action_and_reply")

    # A discussed candidate keeps its own target through a detour. A failed
    # candidate must get a fresh response, never reuse a pre-validation success claim.
    for replacement, succeeds in [("さくらいろ", True), ("さくら", False)]:
        with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
            def model(text, prompt, n):
                if "段階: after_validation" in prompt:
                    return step(text, "respond", "その案は音が足りんかったな。元の上五を残して、続き考えよか。")
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
            if succeeds:
                assert r["workshop_outcome"] == "player_edit_saved", r
                assert hud(sid)["canonical_lines"] == [replacement, *LINES[1:]]
                assert session(base, sid)["workshop_discussion_target"]["line_index"] == 0
            else:
                assert r["workshop_action"] == "respond" and len(r["llm_reports"]) == 2, r
                assert "直したで" not in r["text"] and hud(sid)["canonical_lines"] == LINES
                assert not revisions(folder, sid)
            passed.append("candidate_selection_after_detour_" + ("saved" if succeeds else "replanned_on_failure"))

    from check_workshop_revision import wire
    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        proposal_text = 'あさのいろをくささむしに変更しよう！どう？'
        def confirmed_edit(text, prompt, n):
            if '前の一手は実行していない' in prompt:
                return step(text, 'ask', '下五をその言葉に変えるんやな？')
            p = edit(text, index=2, replacement='くささむし', reference='', fragment=LINES[2])
            p['line_reference']['found'] = False
            p['line_reference']['concept_id'] = 'unknown'
            p['line_proposal']['evidence'] = proposal_text
            p['speech'] = 'うん、相談していた言葉に変えたで。'
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
