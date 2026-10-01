"""Pure canonical policy and narration-projection goldens, no model/services.

The narration suffix is extracted from the real function AST so its ordering,
role filter and exact-label behavior are not restated in the oracle.
"""
import ast
import copy
import hashlib
import itertools
import json
from pathlib import Path
from unittest.mock import patch
from dogido_server import entry_catalog as ec
from dogido_server.dialogue import chat_policy as policy

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'dogido-rust/src/chat_names'
OUT.mkdir(exist_ok=True)
source_path = ROOT / 'dogido_server/state_machine/mixins/narration.py'
tree = ast.parse(source_path.read_text())
render = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_render_player_chat_reply')
def assigns(node, name):
    return isinstance(node, ast.Assign) and any(isinstance(n, ast.Name) and n.id == name for n in node.targets)
start = next(i for i,n in enumerate(render.body) if assigns(n, 'reported_texts'))
end = next(i for i,n in enumerate(render.body[start:], start) if assigns(n, 'speech_whitelist_enforce'))
wrapper = ast.parse('''def finish(allowed_speech_labels, user_text, history_details, observed_entities, look_for_observation, recent_name_context_types):
    return None
''')
wrapper.body[0].body = copy.deepcopy(render.body[start:end+1]) + ast.parse('''return {"allowed_speech_labels": allowed_speech_labels, "speech_name_corrections": speech_name_corrections, "speech_whitelist_enforce": speech_whitelist_enforce}''').body
ast.fix_missing_locations(wrapper)
ns = {'catalog_labels_mentioned_in_text': policy.catalog_labels_mentioned_in_text,
      'build_observed_speech_name_corrections': policy.build_observed_speech_name_corrections}
exec(compile(wrapper, str(source_path), 'exec'), ns)
canonical_finish = ns['finish']
# Reuse the existing native matcher and require its vocabulary to be current.
assert json.loads((ROOT / 'dogido-rust/src/chat_validation/labels.json').read_text()) == list(policy.catalog_speech_labels())

pool = []
lookup = {}
def intern(value):
    key = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    if key not in lookup:
        lookup[key] = len(pool)
        pool.append(value)
    return lookup[key]
def canonical(input):
    base = policy.build_allowed_speech_labels(topic_hits=input.get('topics'), visual_types=input.get('visual_types'),
                passive_types=input.get('passive_types'), hearing_named_mobs=input.get('hearing_named_mobs'), recent_mob_types=input.get('recent_mob_types'))
    out = canonical_finish(base.copy(), input.get('user_text', ''), {'conversation_turns': input.get('history', [])},
                           [{'label': s} for s in input.get('current_entity_labels', [])], input.get('look_label',''),input.get('recent_mob_types', []))
    return base, out
def case(input):
    base, final = canonical(input)
    return [intern(input), intern(base), intern(final)]

cases = [case({})]
# Every entry through every real source, with canonical labels and legacy spelling.
for mid,entry in ec.all_mob_entries().items():
    label = str(entry.get('label') or '')
    for ids in [[mid], ['minecraft:' + mid], [' MINECRAFT:' + mid.upper() + ' '], [' minecraft:' + mid + ' '], [mid,mid]]:
        for field in ['visual_types','passive_types','recent_mob_types']:
            cases.append(case({field: ids}))
    for field in ['topics','hearing_named_mobs','current_entity_labels','user_text','history','look_label']:
        value = {'topics': [{'entry_id':mid,'label_ja':'','kind':'mob'}], 'hearing_named_mobs':[label],
                 'current_entity_labels':[label], 'user_text': label, 'history':[{'role':'user','text':label}], 'look_label':label}[field]
        cases.append(case({field:value}))
    cases.append(case({'topics':[{'entry_id':mid,'label_ja':label,'kind':'mob'}], 'history':[{'role':'assistant','text':label}], 'user_text':'こんにちは'}))
for sid,entry in ec.structure_entries().items():
    label = str(entry.get('label') or '')
    for input in [{'topics':[{'entry_id':sid,'kind':'structure','label_ja':''}]}, {'user_text':label},
                  {'history':[{'role':'assistant','text':label}]}, {'look_label':label}]:
        cases.append(case(input))

special = ['zombie_villager','zombie','elder_guardian','guardian','trader_llama','llama','cave_spider','spider','unknown:mob','UNKNOWN','猫','','  ', 'minecraft:']
for left,right in itertools.product(special, repeat=2):
    cases.append(case({'visual_types':[left], 'passive_types':[right], 'recent_mob_types':[left,right],
                      'hearing_named_mobs':['ゾンビ', ' 村人ゾンビ ', '知らない生き物'],
                      'current_entity_labels':['猫',' ','','ゾンビ','猫']}))

texts = ['', 'こんにちは', '村人ゾンビとゾンビ、村人ゾンビ。', 'エルダーガーディアンとガーディアン', '森の洋館、洋館、森の洋館',
         'ゾンビは昨日の話。今は知らん', 'ラバとアレイ', '\u001cゾンビ\u001f', 'ゾンビ' * 4, 'minecraft:zombie', '😀ゾンビ🐈村人ゾンビ']
histories = [[], [{'role':'assistant','text':'ゾンビとネコ'}], [{'role':'user','text':'ゾンビとネコ'}],
             [{'role':'system','text':'ゾンビ'}, {'role':'USER','text':'ネコ'}, {'role':' user ','text':'ヤギ'}],
             [{'role':'user','text':'エルダーガーディアン'}, {'role':'assistant','text':'ブレイズ'}, {'role':'user','text':'村人ゾンビとゾンビ'}]]
for text,history,look in itertools.product(texts,histories,['', '村人ゾンビ', '前哨基地の旗']):
    cases.append(case({'user_text':text, 'history':history, 'look_label':look,
                      'topics':[{'entry_id':'zombie_villager','label_ja':'村人ゾンビ','kind':'mob'}],
                      'current_entity_labels':['一','  未登録名  ','','村人ゾンビ']}))
# Match order is longest-first even when the text order differs; preserve repeats.
mentions = [[t, policy.catalog_labels_mentioned_in_text(t)] for t in texts + [' '.join(policy.catalog_speech_labels())]]

sections = [{'items': {
    'base': {'label':'一般種'}, 'also_base': {'label':'一般種'},
    'first': {'label':'正式名A','observed_speech_aliases':['一般種','略称','略称','  別名  ','一',''], 'observed_speech_rewrite_from_ids':['base','unknown','base']},
    'second': {'label':'正式名B','observed_speech_aliases':['一般種'], 'observed_speech_rewrite_from_ids':['base']},
    'same_label': {'label':'正式名A','observed_speech_rewrite_from_ids':['base']},
    'same_source': {'label':'一般種','observed_speech_rewrite_from_ids':['base']},
    'single': {'label':'一','observed_speech_rewrite_from_ids':['base']},
    'empty_label': {'label':'  ','observed_speech_rewrite_from_ids':['base']},
    'invalid_source_list': {'label':'別種','observed_speech_rewrite_from_ids':'base'},
    'empty': {}, 'primitive':'昔の名前', 'name_scalars': {'label':True, 'observed_speech_aliases':[None,True,False,12]},
    'alias_match': {'label':' 正式名A ','observed_speech_aliases':['第三略称']},
}}, {'items':{}}, {'items':{}}]
structures = {'groups': {'g': {'label':'構造群','structures': {'place': {'japanese':'構造物名'}, 'short':{'japanese':'村'}}}}}
synthetic = []
with patch.object(ec, '_mob_catalog_sections', return_value=dict(zip(['hostile','neutral','passive'],sections))), \
     patch.object(ec, 'load_entry_catalog', return_value=structures):
    # Text matching is separately tested against the shipped shared guard. These
    # rows exercise dictionary permissions and corrections only, no new vocabulary.
    ids = list(sections[0]['items']) + ['minecraft:first',' MINECRAFT:BASE ', ' minecraft:base ', 'unknown:id','', 'minecraft:']
    for left,right in itertools.product(ids, repeat=2):
        input = {'topics':[{'entry_id':left,'kind':'mob','label_ja':'候補だけ'}], 'visual_types':[right],
                 'hearing_named_mobs':['正式名A'], 'recent_mob_types':[left,right]}
        synthetic.append(case(input))
    for topic in [{'entry_id':'first','label_ja':'','kind':'mob'}, {'entry_id':'place','label_ja':'','kind':'structure'},
                  {'entry_id':'short','label_ja':'村','kind':'structure'}, {'entry_id':'first','label_ja':'候補','kind':' mob '},
                  {'entry_id':'first','label_ja':' 候補 ','kind':''}]:
        synthetic.append(case({'topics':[topic]}))

paths = ['dogido_server/dialogue/chat_policy.py','dogido_server/state_machine/mixins/narration.py',
         'dogido_server/entry_catalog.py','dogido-rust/src/chat_validation/labels.json']
output = {'source_sha256': {p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths},
          'pool':pool,'cases':cases,'mentions':mentions,'synthetic':{'documents':{'mobs':sections,'structures':structures},'cases':synthetic}}
(OUT/'fixtures.json').write_text(json.dumps(output,ensure_ascii=False,separators=(',',':'))+'\n')
print(f'{len(cases)} real and {len(synthetic)} synthetic permission/final projections; {len(mentions)} mention cases')
