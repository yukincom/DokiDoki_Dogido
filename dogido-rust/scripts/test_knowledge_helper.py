"""知識のreaderは固定の正本だけを読む。workshop振り分けは後続段階。"""
import pytest
import dialogue_helper as helper
from dogido_server.knowledge_query import LocalKnowledgeProvider


def fail(*args, **kwargs):
    raise AssertionError("knowledge must not construct a model or advance a state machine")


def test_knowledge_reads_once_and_returns_rust_text_without_rewriting(monkeypatch):
    emitted, requests = [], []
    monkeypatch.setattr(helper, "BridgeLLM", fail)
    monkeypatch.setattr(helper, "DogidoStateMachine", fail)
    monkeypatch.setattr(helper, "emit", emitted.append)
    monkeypatch.setattr(helper, "exchange", lambda value: requests.append(value) or {"text":"Rustで確定した本文。"})
    helper.run_turn({"model":"unused", "max_tokens":72, "text":"枕詞って何？", "reading_engine":"off"})
    assert len(requests) == 1 and requests[0]["op"] == "knowledge"
    assert requests[0]["request_text"] == "枕詞って何？"
    assert requests[0]["lookup"]["facts"][0]["record_id"] == "knowledge.rhetoric.makurakotoba"
    assert emitted == [{"op":"result", "text":"Rustで確定した本文。", "spoken_text":"Rustで確定した本文。"}]


def test_workshop_fallback_does_not_read_db_or_generate(monkeypatch):
    emitted = []
    monkeypatch.setattr(helper, "BridgeLLM", fail)
    monkeypatch.setattr(helper, "exchange", fail)
    monkeypatch.setattr(LocalKnowledgeProvider, "lookup", fail)
    monkeypatch.setattr(helper, "emit", emitted.append)
    helper.run_turn({"model":"unused", "max_tokens":72, "text":"枕詞って何？", "workshop_fallback":True})
    assert emitted[0]["unsupported"] == "句の相談中の知識検索はまだ接続していません。"


def test_reader_cannot_bind_facts_to_another_question(monkeypatch):
    from dataclasses import replace
    from dogido_server.knowledge_query import extract_explicit_knowledge_query
    wrong_query = extract_explicit_knowledge_query("ソネットとは？")
    record = LocalKnowledgeProvider().lookup(wrong_query)
    monkeypatch.setattr(LocalKnowledgeProvider, "lookup", lambda *args, **kwargs: replace(record, query=wrong_query))
    monkeypatch.setattr(helper, "exchange", fail)
    with pytest.raises(ValueError, match="another query"):
        helper.run_turn({"model":"unused", "max_tokens":72, "text":"枕詞って何？"})
