"""残る国語補助は入口・prompt・ローカル検索・読みの変換だけに限定する。"""
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


@pytest.mark.parametrize("command", ["interpretation", "reply"])
def test_helper_no_longer_owns_model_output_validation(command):
    with pytest.raises(ValueError, match="unsupported language helper command"):
        handle({"command":command})


def test_lookup_formats_only_the_computed_fact_received_from_rust():
    i={"dialogue_act":"information_request","question":"音数を教えて。きょう","target":"きょう",
       "facet":"mora_count","topic":"language","relation":"new","target_status":"explicit",
       "evidence":[{"turn_id":"t1","quote":"きょう"}],"alternatives":[],"search_terms":[],"clarification":""}
    fact={"id":"calculation:test","sources":[],"calculation":{"operation":"mora_count","surface":"きょう","reading":"きょう","value":2}}
    result=handle({"command":"lookup","interpretation":i,"computed_fact":fact,"text":"音数を教えて。きょう"})
    assert result["lookup"]["facts"] == [fact]
    assert result["fixed_reply"]["text"] == "「きょう」は2音やで。"
    assert result["fixed_reply"]["fact_ids"] == ["calculation:test"]
