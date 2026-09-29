"""補助は質問だけを渡す。検索・根拠検証・回答はRust所有。"""
import io
import dialogue_helper as helper
from dogido_server.knowledge_query import LocalKnowledgeProvider
from test_input_helper import frame


def fail(*args, **kwargs):
    raise AssertionError("knowledge must not construct a model or advance a state machine")


def test_knowledge_requests_rust_lookup_without_reading_or_rewriting(monkeypatch):
    emitted, requests = [], []
    monkeypatch.setattr(LocalKnowledgeProvider, "lookup", fail)
    monkeypatch.setattr(helper, "BridgeLLM", fail)
    monkeypatch.setattr(helper, "DogidoStateMachine", fail)
    monkeypatch.setattr(helper, "emit", emitted.append)
    monkeypatch.setattr(helper, "exchange", lambda value: requests.append(value) or {"text":"Rustで確定した本文。"})
    monkeypatch.setattr(helper.sys, "stdin", io.StringIO(""))
    helper.run_turn(frame("枕詞って何？",model="unused",max_tokens=72,reading_engine="off"))
    assert len(requests) == 1 and requests[0]["op"] == "knowledge"
    assert requests[0]["request_text"] == "枕詞って何？"
    assert requests[0]["query"]["subject"] == "枕詞"
    assert "lookup" not in requests[0]
    assert emitted == [{"op":"result", "text":"Rustで確定した本文。"}]


def test_workshop_fallback_does_not_read_db_or_generate(monkeypatch):
    emitted = []
    monkeypatch.setattr(helper, "BridgeLLM", fail)
    monkeypatch.setattr(helper, "exchange", fail)
    monkeypatch.setattr(LocalKnowledgeProvider, "lookup", fail)
    monkeypatch.setattr(helper, "emit", emitted.append)
    helper.run_turn(frame("枕詞って何？",model="unused",max_tokens=72,workshop_fallback=True))
    assert emitted[0]["unsupported"] == "句の相談中の知識検索はまだ接続していません。"
