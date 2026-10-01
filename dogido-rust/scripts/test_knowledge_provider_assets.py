"""資料から切り離さず、移植の定数・回答比較fixtureを検証する。"""
import json
from dataclasses import asdict
from generate_knowledge_provider_fixtures import ROOT, policy
from dogido_server.knowledge_query import ExplicitKnowledgeQuery, LocalKnowledgeProvider


def test_policy_matches_canonical_provider():
    assert json.loads((ROOT/'dogido-rust/src/knowledge/provider-policy.json').read_text()) == policy()


def test_expected_facts_still_match_current_python_provider():
    provider=LocalKnowledgeProvider()
    for case in json.loads((ROOT/'dogido-rust/fixtures/knowledge-provider.json').read_text()):
        actual=asdict(provider.lookup(ExplicitKnowledgeQuery(**case['query'])))
        assert json.loads(json.dumps(actual)) == case['expected'], case['query']
