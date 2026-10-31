#!/usr/bin/env python3
"""実ログ由来の相談とモデル選択の辞書確認を、模擬HTTP・辞書・音声で通す。"""
import json

from check_haiku_runtime import LINES
from check_workshop_fixture import fixture
from check_workshop_runtime import install, ready, step
from check_workshop_edits import finish, revisions, token_fixture


def main():
    passed = []
    with token_fixture("ok") as (args, token_dir), fixture(extra_args=(*args, "--reading-engine", "off")) as context:
        base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder = context
        idea = "コップンゆらいならシンプルに白い骨の方がいいんじゃないでしょうか"

        def model(text, prompt, n):
            if text == "白い塩?":
                return {"action": "explain", "purpose": "understand_meaning", "evidence": "しろいしお", "speech": "骨粉の白さを塩に見立てた読みやな。"}
            if text == idea:
                return {"action": "respond", "speech": "白い骨なら、直接伝わる感じやな。下五をその言葉に変えるんやな？",
                        "line_proposal": {"found": True, "target_fragment": LINES[2],
                            "replacement_text": "白い骨", "evidence": text, "confidence": .95}}
            if text == "ん":
                return {"action": "ask", "confidence": .1, "evidence": idea,
                        "speech": "この言葉の響き、一緒に考えよか。"}
            if text == "この子はもう完成でいいんじゃないかな":
                return {**step(text, "close_workshop"), "speech": "この句の響き、一緒に考えたな。"}
            if text == "白い骨の読みを確認して":
                if "段階: decide" in prompt:
                    return {"action": "inspect", "checks": ["reading"], "inspect_text": "白い骨"}
                assert '"reading_text": "しろいほね"' in prompt, prompt
                return {"action": "respond", "speech": "しろいほね、という読みやで。"}
            if text == "うん！変えて！":
                assert "白い骨" in prompt and "conversation_candidate" in prompt
                result = step(text, "stage_conversation_candidate")
                result["purpose"] = "improve_wording"
                return result
            return {"action": "show_current"}

        install(control, model)
        sid = ready(base, send, rows)
        original = stored(sid)
        for text, action in [("白い塩?", "explain"), (idea, "respond"), ("ん", "ask")]:
            result = finish(base, send, sid, text)
            assert result["workshop_action"] == action and result["workshop_reason"] == "accepted", result
            assert len(result["llm_reports"]) == 1 and hud(sid)["canonical_lines"] == LINES, result
        assert not revisions(folder, sid) and stored(sid) == original
        for name in ("haiku_critiques.jsonl", "haiku_lessons.jsonl"):
            assert not (folder / "memory/sessions" / sid / "long_term" / name).exists(), name
        assert not (token_dir / "requests.jsonl").exists(), "ordinary conversation requested a dictionary"
        passed.append("live_wording_and_missing_metadata_keep_conversation_without_inspection_or_save")

        result = finish(base, send, sid, "白い骨の読みを確認して")
        assert [s["action"] for s in result["workshop_steps"]] == ["inspect", "respond"], result
        requests = [json.loads(line) for line in (token_dir / "requests.jsonl").read_text().splitlines()]
        assert [request["text"] for request in requests] == ["白い骨"], requests
        assert not revisions(folder, sid) and hud(sid)["canonical_lines"] == LINES
        passed.append("model_can_request_dictionary_reading_then_discuss_without_editing")

        result = finish(base, send, sid, "うん！変えて！")
        assert result["workshop_action"] == "stage_conversation_candidate", result
        assert hud(sid)["canonical_lines"] == [*LINES[:2], "白い骨"] and len(revisions(folder, sid)) == 1, result
        assert revisions(folder, sid)[0]["lines"][2]["reading_text"] == "しろいほね"
        passed.append("retained_kanji_idea_applies_and_saves_once_after_player_selection")

        result = finish(base, send, sid, "この子はもう完成でいいんじゃないかな")
        assert result["workshop_action"] == "close_workshop" and hud(sid)["state"] == "closed", result
        assert not result["llm_reports"] and len(revisions(folder, sid)) == 1
        assert result["text"] == "ほな、この句はここまでにしよか。"
        passed.append("bert_completion_closes_without_generation_or_extra_save")

    with token_fixture("unavailable") as (args, token_dir), fixture(extra_args=(*args, "--reading-engine", "off")) as context:
        base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder = context
        def unavailable(text, prompt, n):
            if "段階: decide" in prompt:
                return {"action": "inspect", "checks": ["reading"], "inspect_text": "白い骨"}
            assert '"reading_status": "unavailable"' in prompt
            return {"action": "ask", "speech": "読みは確認できんかったわ。しろいほね、のつもりでええ？"}
        install(control, unavailable)
        sid = ready(base, send, rows)
        result = finish(base, send, sid, "この言葉、どう読む？")
        assert result["workshop_action"] == "ask" and not revisions(folder, sid), result
        passed.append("dictionary_unavailability_is_returned_to_the_model_without_an_invented_reading")
    print(json.dumps({"passed": passed}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
