"""Canonical lexical cases; dictionary tokens are mocked, no model or audio."""
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json
import random
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dogido_server import tts_reading
from dogido_server.entry_catalog import all_mob_entries
from dogido_server.haiku.lexical_correction import correct_grounded_catalog_kana, _KANA_RUN_RE, _hiragana
from dogido_server.haiku.source_atoms import HaikuSourceAtom
from haiku_helper import signature


def atom(text, key='a', kind='catalog_label'):
    return HaikuSourceAtom(key, text, 'fixture', 'label', 'observed', kind, 'factual', ('identity_only',))


corrections = []
def record(text, atoms, ids=None):
    ids = list(ids) if ids is not None else [a.atom_id for a in atoms]
    result = correct_grounded_catalog_kana(text, atom_ids=tuple(ids), atom_by_id={a.atom_id:a for a in atoms})
    corrections.append(dict(text=text, source_atoms=[asdict(a) for a in atoms], atom_ids=ids,
                            expected=asdict(result) if result else None))


labels = sorted({str(row.get('label') or '') for row in all_mob_entries().values()} | {
    'シラカバの木', 'さくらばやし', 'アカシアの苗木', 'ネザライトの剣', 'カタカナカナ', 'ーーーあ', '朝鮮'})
for label in labels:
    a = atom(label)
    record(label, [a])
    for raw in _KANA_RUN_RE.findall(label):
        term = _hiragana(raw)
        if len(term) < 4:
            continue
        for i in range(len(term)):
            changed = term[:i] + ('あ' if term[i] != 'あ' else 'い') + term[i+1:]
            for text in [changed, 'の'+changed+'や', ' \x1c'+changed+'\n', changed+changed,
                         term+changed, changed+'カナ', changed[:-1], changed+'あ']:
                record(text, [a])
            record(changed, [a], [])
            record(changed, [atom(label, kind='catalog_note')])
            record(changed, [a, atom(label, 'b')], ['b','a'])
            record(changed, [a, atom(label, 'a', 'catalog_note')])
record('しろかば', [atom('シラカバ'), atom('シロカマ','b')])
record('しろかば', [atom('シラカバ')], ['missing','a'])

rng = random.Random(1049)
samples = {'', ' 朝鮮ー㍻　猫！ ', 'カタカナ\nひらがな', 'Straße İΣςKÅ', '「１２③」', '\x1c𠮷\x1f', 'ゟヿヷヸヹヺ'}
grammar = json.loads((ROOT/'dogido-rust/src/knowledge/query-grammar.json').read_text())
points = list(range(256)) + list(range(0x3000, 0x3100)) + [ord(c) for c in grammar['casefold']]
points += [rng.randrange(0x110000) for _ in range(2400)]
samples.update(chr(c) for c in points if not 0xD800 <= c <= 0xDFFF)
signatures = [[s, signature(s)] for s in sorted(samples)]
normalized = []
for source in ['', '「猫」', 'かな', ' \x1c\"しろかば\' ', '\n猫\n', ' \n ']:
    for dictionary in [None, '', ' ', 'ねこ', '「ねこ」', '\nね\nこ\n', '\rねこ', ' ねこ\x1f ', 'カタカナ']:
        text = (dictionary if dictionary is not None else source).strip().strip('「」"\' ')
        if not text or '\n' in text or '\r' in text:
            text = source
        normalized.append([source, dictionary, {'text':text,'signature':signature(text)}])
neutral = []
for i, case in enumerate(json.loads((ROOT/'dogido-rust/src/tts_reading/token-fixtures.json').read_text())):
    words = [SimpleNamespace(surface=r['surface'], feature=SimpleNamespace(**{k:v for k,v in r.items() if k != 'surface'})) for r in case['tokens']]
    with patch.object(tts_reading, '_get_unidic_tagger', return_value=lambda _:words):
        text = tts_reading.hiraganize_japanese_text('元の文')
    neutral.append([i,text])
out = ROOT/'dogido-rust/src/haiku/lexical-fixtures.json'
out.write_text(json.dumps(dict(corrections=corrections,signatures=signatures,normalized=normalized,neutral=neutral),ensure_ascii=False,separators=(',',':'))+'\n')
print({k:len(v) for k,v in dict(corrections=corrections,signatures=signatures,normalized=normalized,neutral=neutral).items()})
