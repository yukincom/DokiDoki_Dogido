"""相談用の短いJSONも、従来の終了意思・未実行断言の検査を必ず通す。"""
from copy import deepcopy
import pytest
from check_workshop_runtime import step
from workshop_helper import CONSULTATION_KEYS, details_for, handle


def test_whole_verse_uses_existing_dictionary_records_without_meter_rewriting():
    # The author's whole-poem archive is not a generated 5/7/5 candidate.
    result = handle({"op": "whole_verse", "text": "はる\nさくらのは\nあさ", "source": "formal"})
    assert [line["reading_text"] for line in result["lines"]] == ["はる", "さくらのは", "あさ"]
    assert all(line["provenance"] == "formal" and not line["source_atom_ids"] for line in result["lines"])
    assert handle({"op": "whole_verse", "text": "未分割のまま", "source": "formal"}) == {"lines": []}


def frame(text="この句はどういう意味？", action="explain", speech="明るい葉と黒い斧を並べたんやで。"):
    lines = [{"line_id": f"line_{i+1}", "line_index": i, "position": ["upper", "middle", "lower"][i],
        "canonical_name": ["上五", "中七", "下五"][i], "surface_text": reading, "reading_text": reading,
        "source_atom_ids": [], "source_atoms": [], "provenance": "generated"}
        for i, reading in enumerate(["さくらのは", "くろいおのへと", "あさのいろ"])]
    return {"op": "validate", "text": text, "phase": "decide", "observation": None,
        "turn_steps": [], "allowed_actions": ["explain", "respond", "ask", "close_workshop", "inspect", "unrelated", "show_current"],
        "workshop": {"materials": {}, "dialogue": [], "agent_steps": [], "emission": {
            "reading_text": "\n".join(l["reading_text"] for l in lines), "lines": lines,
            "created_at": "2026-09-26T10:00:00+00:00", "interpretation": "葉と斧の対比"}},
        "payload": {k: v for k, v in step(text, action, speech).items() if k in CONSULTATION_KEYS}}


def test_compact_and_full_contract_have_same_validated_step():
    short = frame()
    full = deepcopy(short)
    full["payload"] = step(short["text"], speech=short["payload"]["speech"])
    assert handle(short) == handle(full)
    assert handle(short)["step"]["action"] == "explain"


def test_asr_interpretation_is_only_consultation_context_and_original_is_preserved():
    f = frame("サクラノバって何？")
    f["interpreted_text"] = "さくらのはって何？"
    f["payload"]["evidence"] = f["interpreted_text"]
    details = details_for(f)
    assert details["player_text"] == f["interpreted_text"]
    assert details["original_player_text"] == f["text"]
    assert handle(f)["step"]["action"] == "explain"
    f["op"] = "prepare"
    prompt = handle(f)["messages"][1]["content"]
    assert "今回のプレイヤー発話（認識原文）: サクラノバって何？" in prompt
    assert "今回のプレイヤー発話（会話理解用）: さくらのはって何？" in prompt


@pytest.mark.parametrize("action,raw,semantic", [
    ("close_workshop", "シュウリョウにして", "終了にして"),
    ("close_workshop", "終了しないよ、サクラノバって何？", "終了しないよ、さくらのはって何？"),
])
def test_corrected_words_do_not_supply_close_authority(action, raw, semantic):
    f = frame(raw, action)
    f["interpreted_text"] = semantic
    f["payload"]["evidence"] = "終了"
    assert handle(f)["step"] is None


def test_corrected_replacement_is_not_permission_to_rewrite_original_words():
    from check_workshop_edits import edit
    f = frame("上五を『サクライロ』にして")
    f["interpreted_text"] = "上五を『さくらいろ』にして"
    f["allowed_actions"].append("stage_player_edit")
    f["payload"] = edit("にして", replacement="さくらいろ")
    assert handle(f)["step"] is None
    # 行名・置換語を原文から抜いた正しい案は、補正を無効にしてはいない。
    f["payload"] = edit("にして", replacement="サクライロ")
    assert handle(f)["step"] is not None


def test_corrected_current_verse_question_does_not_escape_to_dictionary():
    f = frame("サクラノバって何？")
    f["op"] = "knowledge_route"
    f["interpreted_text"] = "さくらのはって何？"
    assert handle(f) == {"query": None}


def test_original_fixed_fragment_edit_still_validates_after_asr_interpretation():
    f = frame("サクラノハをさくらいろに変えて")
    f["interpreted_text"] = "さくらのはをさくらいろに変えて"
    f["allowed_actions"].append("stage_player_edit")
    f["op"] = "prepare"
    fixed = handle(f)["fixed_payload"]
    assert fixed["evidence"] == f["text"]
    f["op"] = "validate"
    f["payload"] = fixed
    result = handle(f)
    assert result["step"]["action"] == "stage_player_edit", result
    assert result["step"]["analysis"]["line_proposal"]["replacement_text"] == "さくらいろ"


def test_model_edit_and_close_validate_original_evidence_not_corrected_permission():
    from check_workshop_edits import edit
    f = frame("上五を『ハルノクサ』にして")
    f["interpreted_text"] = "上五を『はるのくさ』にして"
    f["allowed_actions"].append("stage_player_edit")
    f["payload"] = edit(f["text"], replacement="ハルノクサ")
    result = handle(f)
    assert result["step"]["action"] == "stage_player_edit", result
    f["payload"] = edit(f["interpreted_text"], replacement="はるのくさ")
    assert handle(f)["step"] is None
    f = frame("サクラノバの話はここでおしまいにしよう", "close_workshop")
    f["interpreted_text"] = "さくらのはの話はここでおしまいにしよう"
    assert handle(f)["step"]["action"] == "close_workshop"


@pytest.mark.parametrize("key", sorted(CONSULTATION_KEYS))
def test_six_active_fields_are_not_filled_by_helper(key):
    f = frame(); del f["payload"][key]
    assert handle(f)["contract_errors"]


@pytest.mark.parametrize("text", ["終了しない", "『終了』って言った", "終了したらどうなる？", "終了かな？", "終了という言葉が好き"])
def test_compact_close_still_validates_original_intent(text):
    f = frame(text, "close_workshop")
    f["payload"]["evidence"] = "終了"
    result = handle(f)
    assert result["step"] is None, result


@pytest.mark.parametrize("speech,reason", [("保存したで。", "false_persistence_claim"),
    ("修正したで。", "false_revision_claim"), ("音数は五・七・五やで。", "unverified_meter_claim"),
    ("出典の記録があるで。", "unverified_source_claim")])
def test_compact_reply_still_checks_unsupported_claims(speech, reason):
    result = handle(frame(speech=speech))
    assert result["step"] is None and result["reason"] == reason


def test_prompt_keeps_source_and_history_but_does_not_request_unused_edit_fields():
    f = frame(); f["op"] = "prepare"
    f["workshop"]["dialogue"] = [{"turn_id": "t1", "player_text": "何を並べた？", "dogido_text": "葉と斧やで。"}]
    prompt = str(handle(f))
    assert "葉と斧やで。" in prompt and "葉と斧の対比" in prompt
    assert "source_atoms" in prompt and "saved_line_sources" in prompt
    assert "line_proposal" not in prompt and "close_after_action" not in prompt


@pytest.mark.parametrize("text", [
    "上五を『さくらいろ』にして", "上五を『さくらいろ』に変えて",
    "上五は『さくらいろ』の方がいい", "上五は『さくらいろ』でいい",
])
def test_explicit_edit_allows_quoted_replacement(text):
    from check_workshop_edits import edit
    f=frame(text); f["allowed_actions"].append("stage_player_edit"); f["payload"]=edit(text)
    r=handle(f)
    assert r["step"] and r["step"]["analysis"]["line_proposal"]["replacement_text"]=="さくらいろ",r


@pytest.mark.parametrize("text", [
    "上五を『さくらいろ』にしないで", "上五を『さくらいろ』に変えないで",
    "上五を『さくらいろ』にするな", "上五を『さくらいろ』にして？",
    "上五を『さくらいろ』にしてと言われた", "『上五をさくらいろにして』と聞いた",
    "上五を『さくらいろ』にしたらどうなる", "上五を『さくらいろ』にするなら考える",
    "上五は『さくらいろ』がいいとは思わない", "上五を『さくらいろ』にしてって言っただけ",
    "上五は『さくらいろ』という言葉が好き", "上五を直して",
])
def test_edit_needs_unnegated_unquoted_current_act(text):
    from check_workshop_edits import edit
    f=frame(text); f["allowed_actions"].append("stage_player_edit"); f["payload"]=edit(text)
    assert handle(f)["step"] is None


def test_player_edit_dictionary_surface_and_reading_are_bound_together():
    from check_workshop_edits import edit
    f=frame("上五を『桜色』にして"); f["allowed_actions"].append("stage_player_edit"); f["payload"]=edit(f["text"],replacement="桜色")
    step=handle(f)["step"]
    result=handle({"op":"player_edit","workshop":f["workshop"],"proposal":step["analysis"]["line_proposal"]})
    assert result["lines"][0]["surface_text"]=="桜色" and result["lines"][0]["reading_text"]=="さくらいろ"
    assert result["lines"][0]["source_atom_ids"]==() and result["lines"][0]["provenance"]=="player_explicit"


@pytest.mark.parametrize("text", [
    "さくらのはをさくらいろに変えて", "桜の葉を桜色に変えて",
    "さくらのはよりさくらいろの方がいい",
])
def test_original_line_spoken_without_expert_name_uses_legacy_code_path(text):
    f = frame(text); f["allowed_actions"].append("stage_player_edit"); f["op"] = "prepare"
    prepared = handle(f)
    assert prepared["fixed_payload"]["line_proposal"]["target_fragment"] == "さくらのは"
    f["op"] = "validate"; f["payload"] = prepared["fixed_payload"]
    step = handle(f)["step"]
    assert step and step["action"] == "stage_player_edit"
    result = handle({"op": "player_edit", "workshop": f["workshop"], "proposal": step["analysis"]["line_proposal"]})
    assert result["target_line_index"] == 0 and result["lines"][0]["reading_text"] == "さくらいろ"


@pytest.mark.parametrize("text,old_fragment,expected_surface", [
    ("くろいめをひかるめに変えて", "くろいめ", "ひかるめが"),
    ("くろいめよりひかるめの方がいい", "くろいめ", "ひかるめが"),
    ("黒い目を光る目に変えて", "黒い目", "光る目が"),
    ("上五の『黒い目』を『光る目』にして", "黒い目", "光る目が"),
])
def test_spoken_word_pair_stages_only_that_span_without_a_model_call(text, old_fragment, expected_surface):
    f = frame(text)
    f["allowed_actions"].append("stage_player_edit")
    f["workshop"]["emission"]["lines"][0]["reading_text"] = "くろいめが"
    f["workshop"]["emission"]["lines"][0]["surface_text"] = "黒い目が"
    f["workshop"]["emission"]["reading_text"] = "くろいめが\nくろいおのへと\nあさのいろ"
    f["op"] = "prepare"
    prepared = handle(f)
    assert prepared["fixed_payload"]["line_proposal"]["target_fragment"] == old_fragment
    f["op"] = "validate"; f["payload"] = prepared["fixed_payload"]
    proposal = handle(f)["step"]["analysis"]["line_proposal"]
    revised = handle({"op": "player_edit", "workshop": f["workshop"], "proposal": proposal})
    assert revised["text"].splitlines()[0] == "ひかるめが"
    assert revised["lines"][0]["surface_text"] == expected_surface
    assert revised["lines"][0]["reading_text"] == "ひかるめが"


@pytest.mark.parametrize("text", [
    "『黒い目』を『光る目』にしないで",
    "『黒い目』を『光る目』にして？",
    "『黒い目』を『光る目』にしてと言われた",
    "『黒い目』を『光る目』にしたらどうなる？",
    "上五と下五の『黒い目』を『光る目』にして",
])
def test_quoted_word_pair_needs_one_current_unnegated_edit(text):
    f = frame(text)
    f["allowed_actions"].append("stage_player_edit")
    f["op"] = "prepare"
    assert "fixed_payload" not in handle(f)


@pytest.mark.parametrize("text", [
    "さくらのはをさくらいろに変えないで", "『さくらのはをさくらいろに変えて』と聞いた",
    "さくらのはをさくらいろに変えて？", "さくらのはをさくらいろにしたらどうなる",
    "下五のさくらのはをさくらいろに変えて", "さくらのはとあさのいろをさくらいろに変えて",
    "さくらのはをさくらに変えて", "さくらのはを", "じゃあそれで変更しましょう",
])
def test_fragment_recovery_does_not_invent_missing_words_or_bypass_checks(text):
    f = frame(text); f["allowed_actions"].append("stage_player_edit"); f["op"] = "prepare"
    assert "fixed_payload" not in handle(f)


def test_edit_prompt_keeps_character_and_nontechnical_line_references():
    f = frame(); f["op"] = "prepare"; f["allowed_actions"].append("stage_player_edit")
    messages = handle(f)["messages"]
    assert "一人称はオレ" in messages[0]["content"] and "素直な共同編集者" in messages[0]["content"]
    assert "専門的な行名を要求しない" in messages[1]["content"] and "句本文を読む指定" in messages[1]["content"]
    assert "その連続部分だけをtarget_fragment" in messages[1]["content"]


def test_discussed_idea_is_checked_without_becoming_pending():
    f = frame("『さくらのは』を『さくらいろ』にするのはどう？")
    f["op"] = "discussion_candidate"
    candidate = handle(f)["candidate"]
    assert candidate["proposal"]["line_index"] == 0
    assert candidate["proposal"]["replacement_text"] == "さくらいろ"
    assert candidate["validation_codes"] == []
    assert f["workshop"].get("pending") is None


@pytest.mark.parametrize("text", [
    "『さくらのは』を『さくらいろ』にするのはどう？と言われた",
    "『さくらのは』を『さくらいろ』にするのはどう？と聞いた",
])
def test_discussed_idea_rejects_reported_speech(text):
    f = frame(text)
    f["op"] = "discussion_candidate"
    assert handle(f)["candidate"] is None


def test_discussed_idea_uses_grounded_model_target_and_replacement():
    f = frame("上の句のさくらのは、さくらいろはどうかな？")
    f["op"] = "discussion_candidate"
    f["proposal"] = {"target_fragment": "さくらのは", "replacement_text": "さくらいろ", "line_index": 0}
    candidate = handle(f)["candidate"]
    assert candidate["proposal"]["replacement_text"] == "さくらいろ"
    assert candidate["validation_codes"] == []
    f["proposal"]["replacement_text"] = "発話にない案"
    assert handle(f)["candidate"] is None


def test_discussed_idea_survives_respond_step_validation():
    text = "上の句のさくらのは、さくらいろはどうかな？"
    f = frame(text, "respond", "さくらいろも、やわらかい感じやな。")
    f["payload"]["purpose"] = "improve_wording"
    f["payload"]["line_proposal"] = {
        "found": True, "target_fragment": "さくらのは", "replacement_text": "さくらいろ",
        "evidence": text, "confidence": .95,
    }
    step_result = handle(f)["step"]
    assert step_result and step_result["action"] == "respond"
    f["op"] = "discussion_candidate"
    f["proposal"] = step_result["analysis"]["line_proposal"]
    assert handle(f)["candidate"]["proposal"]["replacement_text"] == "さくらいろ"


def test_discussed_idea_is_visible_to_planner_and_selection_needs_evidence():
    f = frame("その案でいこう")
    f["workshop"]["conversation_candidate"] = {
        "proposal": {"line_index": 0, "target_fragment": "さくらのは", "replacement_text": "さくらいろ"},
        "validation_codes": [],
    }
    f["allowed_actions"].append("stage_conversation_candidate")
    f["op"] = "prepare"
    prompt = handle(f)["messages"][1]["content"]
    assert '"replacement_text": "さくらいろ"' in prompt
    assert "まだ句にも未採用案にも反映していない" in prompt
    f["op"] = "validate"
    f["payload"] = {"action": "stage_conversation_candidate", "purpose": "improve_wording",
                    "confidence": .95, "evidence": f["text"], "speech": "", "checks": []}
    assert handle(f)["step"]["action"] == "stage_conversation_candidate"
    f["text"] = "その案でいこう？"
    f["payload"]["evidence"] = f["text"]
    assert handle(f)["step"] is None
    f["text"] = "その案でいこう"
    del f["workshop"]["conversation_candidate"]
    assert handle(f)["step"] is None

@pytest.mark.parametrize("text,ok", [
    ("上五のさくらのはを別の表現に直して", True),
    ("上五は直さないで", False), ("上五を直したらどうなる？", False),
    ("『上五を直して』と言っただけ", False), ("上五の意味を教えて", False),
    ("上五を変更しないで", False), ("上五を修正するなら後で", False),
])
def test_generated_repair_requires_explicit_current_intent(text,ok):
    from check_workshop_revision import proposal
    f=frame(text);f["allowed_actions"].append("propose_revision");f["payload"]=proposal(text)
    assert (handle(f)["step"] is not None)==ok


def test_repair_finding_cannot_target_an_invented_fragment():
    from check_workshop_revision import proposal
    f=frame("上五を直して");f["allowed_actions"].append("propose_revision");f["payload"]=proposal(f["text"])
    f["payload"]["findings"][0]["fragment"]="存在しない行"
    result=handle(f)
    # Legacy keeps the observation but cannot select an editable line from it.
    assert result["step"]["analysis"]["findings"][0]["line_index"] is None


@pytest.mark.parametrize("stage,text,expected", [
    ("meaning_explained", "なるほどね", "acknowledge_meaning"),
    ("meaning_explained", "『なるほど』と言った", None),
    ("discussion", "なるほどね", None),
    ("close_confirmation", "うん", "confirm_close"),
    ("close_confirmation", "うん？", None),
    ("close_confirmation", "まだ続けたい", "continue_workshop"),
])
def test_followup_patterns_are_only_for_current_stage(stage, text, expected):
    f = {"op": "fixed_followup", "stage": stage, "text": text, "pending": False}
    assert handle(f)["action"] == expected
    f["pending"] = True
    assert handle(f)["action"] is None


def test_followup_context_and_actions_are_in_same_planner_prompt():
    f = frame(); f["op"] = "prepare"
    f["workshop"]["followup"] = "meaning_explained"
    f["allowed_actions"].append("acknowledge_meaning")
    prompt = str(handle(f))
    assert "会話段階: meaning_explained" in prompt and "acknowledge_meaning" in prompt


@pytest.mark.parametrize("text", ["いいよとは言ってない", "いいよとは思わない", "いいよ？", "いいよと返したらどうなる", "『いいよ』と聞いた", "いいよ、でももう少し話したい"])
def test_followup_assent_must_not_be_negated_or_hypothetical(text):
    from check_workshop_followup import followup
    f = frame(text); f["workshop"]["followup"] = "close_confirmation"
    f["allowed_actions"].append("confirm_close")
    f["payload"] = followup("いいよ", "confirm_close")
    assert handle(f)["step"] is None


@pytest.mark.parametrize("text,expected", [("うん", "resume_workshop"), ("続けよう", "resume_workshop"),
    ("もういい", "decline_resume"), ("続けない", "decline_resume"), ("続ける？", None), ("『続けよう』と言った", None)])
def test_combat_resume_is_a_different_question_even_with_pending(text, expected):
    f = {"op": "fixed_followup", "stage": "combat_resume_confirmation", "text": text, "pending": True}
    assert handle(f)["action"] == expected


@pytest.mark.parametrize("action,text", [("resume_workshop", "続けない"), ("decline_resume", "終わらない"),
    ("resume_workshop", "『続けよう』と言った"), ("decline_resume", "やめたらどうなる？")])
def test_semantic_resume_or_decline_needs_current_unnegated_intent(action, text):
    f = frame(text); f["workshop"]["followup"] = "combat_resume_confirmation"
    f["allowed_actions"].append(action)
    f["payload"] = {"action": action, "purpose": "finish_workshop" if action == "decline_resume" else "continue_discussion",
        "confidence": .95, "evidence": text, "speech": "", "checks": []}
    assert handle(f)["step"] is None


def test_generated_repair_does_not_follow_model_to_a_different_named_line():
    from check_workshop_revision import proposal
    f=frame("上五を直して");f["allowed_actions"].append("propose_revision");f["payload"]=proposal(f["text"])
    f["payload"]["findings"][0]["fragment"]="くろいおのへと"
    result=handle(f)
    assert result["step"] is None and result["reason"]=="repair_target_conflict"


def test_literal_question_keeps_idea_without_granting_an_edit_command():
    f=frame("『くろい』を『あお』にするのはどう？"); f["op"]="explicit_discussion"
    draft=handle(f)["candidate"]
    assert draft["proposal"]["target_fragment"]=="くろい"
    assert draft["proposal"]["line_index"]==1 and "meter_not_exact" in draft["validation_codes"]
    f["text"] += "下五は変えて"
    assert handle(f)["candidate"] is None


def test_replacement_only_correction_preserves_discussed_target_and_other_characters():
    f=frame("『くろい』を『あお』にするのはどう？"); f["op"]="explicit_discussion"
    f["workshop"]["conversation_candidate"]=handle(f)["candidate"]
    f["op"]="player_edit"; f["text"]="やっぱり『あおい』にして"
    f["proposal"]={"replacement_text":"あおい", "target_fragment":"", "line_index":None}
    result=handle(f)
    assert [l["reading_text"] for l in result["lines"]]==["さくらのは","あおいおのへと","あさのいろ"]
    f["proposal"]["line_index"]=0
    result=handle(f)
    assert result["text"] is None and result["target_line_index"]==0
    f["proposal"]={"replacement_text":"あお", "target_fragment":"", "line_index":None}
    assert "meter_not_exact" in handle(f)["failure_reasons"]


def test_new_target_in_original_input_overrides_omitted_model_target():
    f=frame("『くろい』を『あお』にするのはどう？"); f["op"]="explicit_discussion"
    f["workshop"]["conversation_candidate"]=handle(f)["candidate"]
    f["op"]="player_edit"; f["text"]="あさをよるにして"
    f["proposal"]={"replacement_text":"よる", "target_fragment":"", "line_index":None}
    result=handle(f)
    assert [l["reading_text"] for l in result["lines"]]==["さくらのは","くろいおのへと","よるのいろ"]
    f["text"]="上五をあおいにして"
    f["proposal"]["replacement_text"]="あおい"
    result=handle(f)
    assert result["text"] is None and result["target_line_index"]==0
