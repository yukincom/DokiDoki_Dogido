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
