#!/usr/bin/env python3
"""一般知識の本文・根拠・未収録判定を既存Pythonから採取。外部通信なし。"""
import argparse
import ast
from dataclasses import asdict
import inspect
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dogido_server import knowledge_query as k
from dogido_server.reference_catalog import load_reference_index


def policy():
    value = {name: getattr(k, name) for name in (
        '_OFFICIAL_KANJI_TABLE_RECORDS', '_ENTITY_KIND_LABELS', '_EXPRESSION_MODE_LABELS',
        '_FORMAL_CONSTRAINT_LABELS', '_RECIPE_TYPE_LABELS')}
    value.update(ambiguous=sorted(k._AMBIGUOUS_CURATED_SUBJECT_KEYS), grammar_exact=sorted(k._GRAMMAR_PATTERN_EXACT_SUBJECT_KEYS))
    tree = ast.parse(inspect.getsource(k._explicit_minecraft_versions))
    value['version_patterns'] = list(ast.literal_eval(next(n.value for n in ast.walk(tree)
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'patterns' for t in n.targets))))
    return value


def cases():
    provider = k.LocalKnowledgeProvider()
    result = []
    seen = set()
    def add(domain, subject, intent='definition', evidence=None):
        query = k.ExplicitKnowledgeQuery(domain=domain, subject=subject, intent=intent, evidence=evidence or f'{subject}について教えて')
        key = tuple(asdict(query).values())
        if key in seen: return
        seen.add(key)
        result.append({'query': asdict(query), 'expected': asdict(provider.lookup(query))})
    index = load_reference_index()
    for entry in index['entries'].values():
        if entry['dataset_id'] in ('japanese_grammar', 'historical_kana_and_scripts', 'makurakotoba', 'japanese_poetry_forms', 'world_poetry'):
            domain = 'poetry' if entry['dataset_id'] in ('japanese_poetry_forms', 'world_poetry') else 'japanese_language'
            for intent in ('definition', 'rules', 'classification', 'reading'):
                add(domain, entry['title_ja'], intent)
            for alias in entry.get('aliases', [])[:2]: add(domain, alias)
    for subject in ['常用漢字表', '学年別漢字配当表', '3', '３', '三', '森', '匠', '鬱', '神', '鿿']:
        for intent in ['definition', 'grade', 'reading']: add('japanese_language', subject, intent)
    for subject in ['～そうだ', '～ば', '～ている', '～ようだ', '～だけ', '～に違いない', '～存在しない文型', '〜そうだ', '~そうだ']:
        for intent in ['definition', 'classification', 'reading']: add('japanese_language', subject, intent)
    for subject in ['たくみ', 'こそ', 'ない', '幻の枕詞', '\x1c枕詞\x1f', ' 俳句 ', 'ダイヤモンドの剣']:
        add('japanese_language', subject)
    for evidence in ['Minecraft 1.20.1の剣の耐久値は？', 'Java Edition 1.22の変更は？', 'マイクラ１.２０でどう変わる？',
                     '1.20.1版の変更は？', 'Minecraft 1.21.11と1.20.1版でどう変わった？']:
        add('minecraft', 'ダイヤモンドの剣', 'properties', evidence)
    return result


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--check',action='store_true'); args=parser.parse_args()
    artifacts={ROOT/'dogido-rust/src/knowledge/provider-policy.json':policy(), ROOT/'dogido-rust/fixtures/knowledge-provider.json':cases()}
    for path,value in artifacts.items():
        rendered=json.dumps(value,ensure_ascii=False,indent=2)+'\n'
        if args.check: assert path.read_text()==rendered, f'stale {path.name}'
        else: path.write_text(rendered)
        print(path.name, len(value))
if __name__ == '__main__': main()
