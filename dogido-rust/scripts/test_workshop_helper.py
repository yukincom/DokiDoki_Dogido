"""相談用の短いJSONも、従来の終了意思・未実行断言の検査を必ず通す。"""
from copy import deepcopy
import pytest
from check_workshop_runtime import step
from workshop_helper import CONSULTATION_KEYS, handle


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


def test_generated_repair_does_not_follow_model_to_a_different_named_line():
    from check_workshop_revision import proposal
    f=frame("上五を直して");f["allowed_actions"].append("propose_revision");f["payload"]=proposal(f["text"])
    f["payload"]["findings"][0]["fragment"]="くろいおのへと"
    result=handle(f)
    assert result["step"] is None and result["reason"]=="repair_target_conflict"
