"""残る国語補助は入口とローカル検索だけに限定する。"""
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from language_helper import explicit_request, handle
from dogido_server.service import DogidoService


@pytest.mark.parametrize("text", [
    "", "こんにちは", "漢字が書いてあった", "ゾンビどこ？", "いい川柳だね", "川柳の話をしよう",
    "枕詞ってどういう意味？", "3は何年生で習うの？", "音数\n を教えて", "ドギドってなんて読む？",
])
def test_entry_gate_matches_existing_service_without_a_model(text):
    assert explicit_request(text) == DogidoService._looks_like_main_language_request(text)


@pytest.mark.parametrize("command", ["interpretation", "reply", "prompt"])
def test_helper_no_longer_owns_model_generation_or_validation(command):
    with pytest.raises(ValueError, match="unsupported language helper command"):
        handle({"command":command})


def test_lookup_returns_records_without_composing_a_reply_or_comparison():
    i={"dialogue_act":"information_request","question":"三と三は同じ？","target":"三",
       "facet":"comparison","topic":"language","relation":"new","target_status":"explicit",
       "evidence":[{"turn_id":"t1","quote":"三"}],"alternatives":[],"search_terms":["三"],"clarification":""}
    result=handle({"command":"lookup","interpretation":i})
    assert set(result) == {"stage", "lookup"}
    assert any(f.get("allocation",{}).get("character")=="三" for f in result["lookup"]["facts"])
    assert all(f.get("claim_status")!="input_character_comparison" for f in result["lookup"]["facts"])
