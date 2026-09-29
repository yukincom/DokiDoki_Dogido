"""Canonical curated topic lookup fixtures. Pure Python, no model or services.

Run with this checkout on PYTHONPATH. Raw entries, sorted terms, lookup results
and hint strings are all returned by the original functions, not restated rules.
"""
import hashlib
import json
from pathlib import Path
from unittest.mock import patch
from dogido_server import entry_catalog as canonical
from dogido_server.state_machine.mixins.narration import NarrationMixin

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'dogido-rust/src/chat_catalog'
OUT.mkdir(exist_ok=True)
(OUT / 'rules.json').write_text(json.dumps({
    'field_weights': canonical._TOPIC_FIELD_WEIGHTS,
    'min_term_len': canonical._TOPIC_MIN_TERM_LEN,
    'min_score': canonical._TOPIC_MIN_SCORE,
    'top_k': canonical._TOPIC_TOP_K,
}, ensure_ascii=False, indent=2) + '\n')

pool = []
lookup = {}
def intern(value):
    key = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    if key not in lookup:
        lookup[key] = len(pool)
        pool.append(value)
    return lookup[key]

def capture(queries, configs):
    term_rows = canonical._topic_term_index()
    cases = []
    for query in queries:
        for observed, top_k, minimum in configs:
            hits = canonical.find_catalog_topics(query, observed_ids=observed, top_k=top_k, min_score=minimum)
            chat = NarrationMixin._player_chat_topic_hits(None, query, observed)
            cases.append([intern(query), intern(observed), top_k, minimum,
                          intern(hits), intern(canonical.format_catalog_topic_hints(hits)),
                          intern(chat), intern(canonical.format_catalog_topic_hints(chat))])
    return {'mobs': canonical.all_mob_entries(), 'structures': canonical.structure_entries(),
            'term_rows': term_rows, 'cases': cases,
            'mob_reads': [[key, canonical.mob_entry(key)] for entry_id in canonical.all_mob_entries() for key in
                          [entry_id, 'minecraft:' + entry_id, '\u001c MINECRAFT:' + entry_id.upper() + ' \u001f', 'minecraft: ' + entry_id, 'minecraft:minecraft:' + entry_id]]}

queries = dict.fromkeys(['', ' ', '\u001c\u001f', 'いいんじゃないかな', 'イカ', 'いか', 'イヌ', 'ネコ', '猫',
                        '前哨基地はどこ？', '旗のある前哨基地', 'その商人いる？', 'とてもきれいだね',
                        'ウィッチ ウイッチ うぃっち', 'ゾンビとヤギとネコ', '０１', 'A a Ａ', '🐈\n🐐'])
for term, *_ in canonical._topic_term_index():
    for query in [term, canonical._fold_kana_for_match(term), '\u001cそれは「' + term + '」かな？\u3000']:
        queries.setdefault(query, None)
# Include every existing term and pair adjacent rows to exercise score accumulation.
terms = [r[0] for r in canonical._topic_term_index()]
for left, right in zip(terms[::2], terms[1::2]):
    queries.setdefault(left + 'と' + right, None)
real = capture(queries, [([], 3, 5.0)])
real_edges = capture(['前哨基地', '旗のある前哨基地', 'ゾンビとヤギとネコ', '声', '海底遺跡', '取引', 'イカ', 'いいんじゃないかな'], [
    (obs, k, minimum)
    for obs in [[], ['pillager'], ['minecraft:pillager'], [' minecraft:pillager '], ['PILLAGER'], ['Minecraft:pillager'], ['goat', 'cat', 'squid'], ['outpost']]
    for k in [-2, 0, 1, 3, 50]
    for minimum in [0.0, 5.0, 5.01, 1000.0]
])
# Synthetic source-shaped documents exercise all source-normalization branches.
sections = [
    {'schema': {}, 'label': 'ignored section', 'notes': ['ignored'], 'recommended_fields': [],
     'alias': {'label': '先', 'spoken_aliases': ['同じ', '同じ', 'Ａ', 'A', 'ヶ', 'ヿ', '', '\u001c旗\u001f'],
               'poetic': {'visual_tags': ['同じ', '紫', '同じ'], 'sound_tags': ['こん', 'からん', '', '😀😀', '😀😀😀'],
                          'role': '同じ', 'scene_tags': ['同じ'], 'reaction_tags': ['同じ'], 'comic_tags': ['同じ'], 'motion_tags': ['同じ']}},
     'same': {'label': '共通'}, 'primitive': '昔の文字列', 'scalar': True,
     'compound': {'label': '合成', 'spoken_aliases': [None, True, 12, ['猫', None], {'x': '猫'}]},
     ' Minecraft:Alias ': {'label': '大文字ID'}, 'missing': {'japanese': '日本語だけ'}, '': {'label': '空ID'}},
    {'items': {'same': {'label': '中間'}, 'neutral_only': {'label': '中立'}}, 'label': 'ignored'},
    {'items': {'same': {'label': '共通', 'spoken_aliases': ['同じ']}, 'empty': {'label': '', 'japanese': '代用'},
               'white': {'label': '  ', 'spoken_aliases': ['空白ラベル']}, 'false': False}, 'notes': 'ignored'}
]
structures = {'groups': {
    'first': {'label': '最初', 'description': '群の説明', 'biomes': ['taiga'], 'extra': {'retained': True}, 'structures': {
        'same': {'japanese': '共通', 'spoken_aliases': ['同じ'], 'note': '短い注記'},
        'overwrite': {'japanese': '最初の名前'},
        'missing_japanese': {'label': '消えるラベル', 'note': '名だけ'},
        'note_24': {'japanese': None, 'note': 'あ' * 24},
        'note_25': {'japanese': '', 'note': 'あ' * 25},
        'bad_entry': 'ignored'}},
    'second': {'label': '後', 'structures': {'overwrite': {'japanese': '後の名前', 'poetic': {'keep': 'all metadata'}}}},
    'bad_group': [], 'bad_structures': {'structures': []}}}
with patch.object(canonical, '_mob_catalog_sections', return_value=dict(zip(['hostile', 'neutral', 'passive'], sections))), \
     patch.object(canonical, 'load_entry_catalog', return_value=structures):
    canonical._topic_term_index.cache_clear()
    synthetic_terms = canonical._topic_term_index()
    synthetic_queries = list(dict.fromkeys([t[0] for t in synthetic_terms] + [canonical._fold_kana_for_match(t[0]) for t in synthetic_terms] +
        ['同じ 共通', '同じ 同じ', 'あ' * 25, 'こん からん', '猫', 'None True 12', '\u001c旗\u001f', '大文字ID', 'Ａ A', '共通 後の名前']))
    synthetic = capture(synthetic_queries, [(obs,k,minimum) for obs in [[], ['same'], ['minecraft:same'], [' minecraft:same '], ['minecraft:', '', '   ']] for k in [-1, 1, 3, 100] for minimum in [0.0,5.0,100.0]])
    synthetic['documents'] = {'mobs': sections, 'structures': structures}
canonical._topic_term_index.cache_clear()

hints = []
for hits in [[], [{'entry_id': 'example', 'kind': 'mob', 'label_ja': '', 'score': 0.0, 'matched_terms': [], 'observed': False}],
             [{'entry_id': f'id{i}', 'kind': 'structure' if i % 2 else 'mob', 'label_ja': ' ' if i == 0 else f'名{i}',
               'score': float(i), 'matched_terms': ['一', '', '二', '三', '五番目'], 'observed': i % 2 == 0} for i in range(6)]]:
    hints.append([hits, canonical.format_catalog_topic_hints(hits)])
files = ['dogido_server/entry_catalog.py', 'dogido_server/state_machine/mixins/narration.py',
         *[f'data/catalogs/entries/mobs/{n}.json' for n in ['hostile', 'neutral', 'passive']], 'data/catalogs/entries/minecraft_structure.json']
output = {'source_sha256': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in files},
          'pool': pool, 'real': real, 'real_edges': real_edges['cases'], 'synthetic': synthetic, 'hints': hints}
(OUT / 'fixtures.json').write_text(json.dumps(output, ensure_ascii=False, separators=(',', ':')) + '\n')
print(f"{len(real['term_rows'])} terms; {len(real['mobs'])} mobs; {len(real['structures'])} structures; "
      f"{len(real['cases']) + len(real_edges['cases']) + len(synthetic['cases'])} lookup/hint pairs")
