"""一般知識readerの欠損と出典の検査。モデル・音声・サーバーは起動しない。"""
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
from test_language_retrieval import reference, ROOT, BINARY
from dogido_server.knowledge_query import ExplicitKnowledgeQuery, LocalKnowledgeProvider


def compare(tmp_path, reference, subject, intent='definition'):
    query=ExplicitKnowledgeQuery('japanese_language', subject, intent, f'{subject}について教えて')
    expected=json.loads(json.dumps(asdict(LocalKnowledgeProvider(language_reference_dir=reference).lookup(query))))
    request=tmp_path/'query.json';request.write_text(json.dumps([asdict(query)],ensure_ascii=False))
    process=subprocess.run([str(BINARY),'lookup-knowledge',str(request),'--reference-dir',str(reference),
        '--minecraft-cache',str(tmp_path/'unused'),'--source-lock',str(ROOT/'reference/minecraft_technical/source_lock.json')],
        check=True,capture_output=True,text=True,timeout=30)
    actual=json.loads(process.stdout)[0]
    assert actual==expected, (actual, expected)
    return actual


def test_curated_question_works_without_optional_bulk_data(tmp_path,reference):
    assert compare(tmp_path,reference,'枕詞')['status']=='found'


def test_missing_grammar_data_is_unavailable_not_an_unrecorded_word(tmp_path,reference):
    result=compare(tmp_path,reference,'～そうだ')
    assert result['status']=='unavailable' and result['error_code']=='FileNotFoundError'


def test_missing_kanji_data_is_unavailable_not_unknown_grade(tmp_path,reference):
    assert compare(tmp_path,reference,'森','grade')['status']=='unavailable'


def test_data_hash_failure_does_not_emit_unverified_facts(tmp_path,reference):
    index=json.loads((reference/'index.json').read_text())
    path=reference/index['datasets'][0]['path'];path.write_bytes(path.read_bytes()+b' ')
    result=compare(tmp_path,reference,'枕詞')
    assert result['status']=='unavailable' and result['facts']==[]


def test_numeric_grade_ambiguity_needs_no_database(tmp_path):
    assert compare(tmp_path,tmp_path/'absent','3','grade')['error_code']=='ambiguous_kanji_numeric_notation'


def test_minecraft_facts_keep_version_sources_properties_and_recipe_limits(tmp_path):
    from unittest.mock import patch
    from generate_minecraft_reader_fixtures import base_files, build_fixture, seal, PREFIX, encode, DATASETS
    from dogido_server import minecraft_knowledge as mc
    files=base_files()
    for dataset in DATASETS:
        records=[json.loads(line) for line in files[PREFIX+dataset+'.jsonl'].splitlines()]
        data=''; entries=[]
        for record in records:
            record['minecraft_version']='1.21.11'
            record['sources']=[{'source_kind':'official_artifact','relative_path':'data/minecraft/item/test.json','json_pointer':'/components'}]
            if record['id']=='entry.a':
                record.update(entry_id='minecraft:diamond_sword', item_summary={'max_damage':1561,'max_stack_size':1})
            elif record['id']=='entry.b':
                record.update(entry_id='minecraft:test_sword',item_summary={'max_stack_size':64})
            elif record['id']=='change.synthetic':
                record.update(summary_ja='試験用設定の名称が変わりました。',coverage='representative_selection',
                              identifier_mappings=[{'from':'doMobSpawning','to':'minecraft:spawn_mobs','value_inverted':True}])
                record['search_terms']+=['minecraft:spawn_mobs']
                record['sources']=[{'source_kind':'official_web_page','url':'https://www.minecraft.net/ja-jp/article/synthetic-fixture','section':'test'}]
            elif record['id']=='recipe.synthetic':
                record.update(entry_id='minecraft:diamond_sword',document_summary={'declared_type':'minecraft:crafting_shaped',
                    'referenced_resource_ids':['minecraft:diamond_sword','minecraft:crafting_shaped','minecraft:diamond','minecraft:stick',
                                               'minecraft:diamond','minecraft:test_a','minecraft:test_b','minecraft:test_c']})
                record['sources'][0]['relative_path']='data/minecraft/recipe/diamond_sword.json'
            line=encode(record)+'\n'
            entries.append({**record,'dataset_id':dataset,'dataset_path':dataset+'.jsonl','byte_offset':len(data.encode()),'byte_length':len(line.encode())})
            data+=line
        files[PREFIX+dataset+'.jsonl']=data
        files[PREFIX+dataset+'.index.jsonl']=''.join(encode(row)+'\n' for row in entries)
    seal(files)
    cache,lock=build_fixture(tmp_path/'db',files)
    queries=[]
    for subject in ['ダイヤモンドの剣','minecraft:diamond_sword','剣','doMobSpawning','minecraft:spawn_mobs','知らない道具']:
        for intent in ['identifier','properties','change','definition','rules']:
            queries.append(ExplicitKnowledgeQuery('minecraft',subject,intent,f'{subject}の作り方を教えて' if intent=='rules' else f'{subject}の情報を教えて'))
    queries.append(ExplicitKnowledgeQuery('minecraft','ダイヤモンドの剣','properties','ダイヤモンドの剣の耐久値は？'))
    queries.append(ExplicitKnowledgeQuery('minecraft','ダイヤモンドの剣','identifier','Minecraft 1.20.1のダイヤモンドの剣のIDは？'))
    with patch.object(mc,'SOURCE_LOCK_PATH',lock):
        provider=LocalKnowledgeProvider(minecraft_cache_root=cache)
        expected=json.loads(json.dumps([asdict(provider.lookup(query)) for query in queries]))
    request=tmp_path/'requests.json';request.write_text(json.dumps([asdict(q) for q in queries],ensure_ascii=False))
    process=subprocess.run([str(BINARY),'lookup-knowledge',str(request),'--reference-dir',str(ROOT/'reference/language_education_and_poetry'),
        '--minecraft-cache',str(cache),'--source-lock',str(lock)],check=True,capture_output=True,text=True,timeout=30)
    actual=json.loads(process.stdout)
    assert actual==expected
    assert any('ほか1件' in fact['text_ja'] for result in actual for fact in result['facts'])
    assert any('最大耐久値は1561' in fact['text_ja'] for result in actual for fact in result['facts'])
