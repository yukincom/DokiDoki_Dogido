"""移行用の純粋補助が、旧入口と原文evidence・出力上限を維持することを確認する。"""
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from language_helper import explicit_request, handle, parse
from dogido_server.language_dialogue.contracts import Interpretation
from dogido_server.service import DogidoService


def command():
    current = {"turn_id": "current", "text": "きょうの音数を教えて", "source": "voice"}
    interpretation = {
        "dialogue_act": "information_request", "question": current["text"], "target": "きょう",
        "facet": "mora_count", "topic": "language", "relation": "new", "target_status": "explicit",
        "alternatives": [], "evidence": [{"turn_id": "current", "quote": current["text"]}],
        "search_terms": [], "clarification": "",
    }
    return {
        "command": "interpretation", "details": {"current": current, "history": [],
            "focus": {"question": "", "target": "", "clarification": "", "alternatives": []}},
        "state": {"kanji_scope_confirmed": False},
        "generated": {"text": json.dumps(interpretation, ensure_ascii=False), "finish_reason": "stop"},
    }


@pytest.mark.parametrize("text", [
    "", "こんにちは", "漢字が書いてあった", "ゾンビどこ？", "いい川柳だね", "川柳の話をしよう",
    "枕詞ってどういう意味？", "3は何年生で習うの？", "音数\n を教えて", "ドギドってなんて読む？",
])
def test_entry_gate_matches_existing_service_without_a_model(text):
    assert explicit_request(text) == DogidoService._looks_like_main_language_request(text)


@pytest.mark.parametrize("reason", ["length", "max_tokens", "MAX_TOKENS"])
def test_even_parseable_json_is_rejected_when_output_was_truncated(reason):
    generated = command()["generated"]
    assert parse(generated, Interpretation) is not None
    generated["finish_reason"] = reason
    assert parse(generated, Interpretation) is None


@pytest.mark.parametrize("evidence", [
    [], [{"turn_id": "absent", "quote": "きょう"}],
    [{"turn_id": "current", "quote": "あした"}], [{"turn_id": "past", "quote": "きょう"}],
])
def test_unseen_or_only_past_evidence_cannot_support_current_interpretation(evidence):
    data = command()
    data["details"]["history"] = [{"turn_id": "past", "text": "きょう", "role": "user"}]
    payload = json.loads(data["generated"]["text"])
    payload["evidence"] = evidence
    data["generated"]["text"] = json.dumps(payload)
    assert handle(data)["payload"] is None


def test_contextual_target_and_nfkc_evidence_use_only_supplied_turns():
    data = command()
    data["details"]["current"]["text"] = "Ａの意味を教えて"
    payload = json.loads(data["generated"]["text"])
    payload.update(target="A", facet="meaning", target_status="contextual", relation="continue",
                   evidence=[{"turn_id": "current", "quote": "Aの意味を教えて"}])
    data["generated"]["text"] = json.dumps(payload)
    assert handle(data)["payload"]["target"] == "A"
    missing = copy.deepcopy(data)
    payload["target"] = "未出現の言葉"
    missing["generated"]["text"] = json.dumps(payload)
    assert handle(missing)["payload"] is None


def test_unknown_fields_cannot_add_state_change_commands():
    generated = command()["generated"]
    payload = json.loads(generated["text"])
    payload["close_workshop"] = True
    generated["text"] = json.dumps(payload)
    assert parse(generated, Interpretation) is None
