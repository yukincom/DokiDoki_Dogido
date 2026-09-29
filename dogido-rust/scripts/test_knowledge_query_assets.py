"""移植用の固定構文・語彙と比較データをPython正本に照合する。"""
from dataclasses import asdict
import json
from generate_knowledge_query_fixtures import ROOT, grammar
from dogido_server.knowledge_query import extract_explicit_knowledge_query


def test_query_grammar_is_the_current_closed_python_vocabulary_and_patterns():
    saved=json.loads((ROOT / "dogido-rust/src/knowledge/query-grammar.json").read_text())
    assert saved == json.loads(json.dumps(grammar(), ensure_ascii=False))


def test_query_examples_still_match_canonical_python_without_search():
    rows=json.loads((ROOT / "dogido-rust/fixtures/knowledge-query.json").read_text())
    for row in rows:
        query=extract_explicit_knowledge_query(row["text"])
        assert row["query"] == (asdict(query) if query else None), row["text"]
