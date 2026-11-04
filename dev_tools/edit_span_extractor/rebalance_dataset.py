"""旧合成方式の記録。教材は不採用となり、CLI生成入口は停止中。

句の分割を固定し、抽出語自体の漢字・仮名の配分を変える独立実験。

見込み: 依頼動詞だけでなく語句にも漢字を含め、漢字境界の欠落を減らす。
合格: 分割・実対話評価の不変と変形後offset一致。実モデルの改善は別に測る。
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import re
import shutil

from augmentation import kana, kana_only, kana_template, noise, noise_template, KANA_LINES, KANA_PREFIXES, KANA_SUFFIXES
from build_dataset import TEMPLATES, LINE_NAMES, PREFIXES, SUFFIXES, render, EXPECTATION, CRITERIA
from span_model import read_jsonl, validate_spans, write_json


def has_kanji(text):
    return bool(re.search(r'[一-龯]', text))


def profile_plan(count, rng):
    if count % 10:
        raise ValueError('Count must be divisible by ten')
    result = ['kanji'] * (count * 5 // 10) + ['kana'] * (count * 3 // 10) + ['asr_noise'] * (count * 2 // 10)
    rng.shuffle(result)
    return result


def checked_variants(materials, mapping):
    available = {item['text'] for item in materials}
    if set(mapping) - available:
        raise ValueError('Variants reference material from outside this split')
    result = {}
    for source, values in mapping.items():
        if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v or not has_kanji(v) for v in values):
            raise ValueError(f'Invalid kanji variants: {source}')
        result[source] = list(dict.fromkeys(values))
    for source in available:
        if has_kanji(source):
            result.setdefault(source, []).append(source)
    return result


def summarize(rows):
    positives = [r for r in rows if r['spans']]
    return {'rows': len(rows), 'profiles': dict(Counter(r.get('variant', 'real') for r in rows)),
        'positive_rows': len(positives), 'utterance_has_kanji': sum(has_kanji(r['text']) for r in rows),
        'positive_with_kanji_slot': sum(any(has_kanji(s['text']) for s in r['spans']) for r in positives),
        'positive_all_kana_slots': sum(all(not has_kanji(s['text']) for s in r['spans']) for r in positives)}


def build_split(split, count, materials, variants, protected, seen, rng):
    plans = profile_plan(count, rng)
    result = []
    for profile in plans:
        for attempt in range(1000):
            mode = rng.choices(['pair', 'whole', 'from_only', 'none'], [45, 30, 5, 20])[0]
            slot_style = 'kanji' if profile == 'kanji' or profile == 'asr_noise' and rng.random() < .5 else 'kana'
            pool = [item for item in materials if item['text'] in variants] if slot_style == 'kanji' else [item for item in materials if kana_only(item['text'])]
            if len(pool) < 2:
                raise ValueError(f'Insufficient {split}/{slot_style} materials')
            source, target = rng.sample(pool, 2)
            def surface(item):
                return rng.choice(variants[item['text']]) if slot_style == 'kanji' else kana(item['text'])
            old, new = surface(source), surface(target)
            if rng.random() < .08:
                new = old
            partial = rng.random() < .2 and len(old) >= 4
            if partial:
                begin = rng.randrange(len(old) - 2)
                old = old[begin:rng.randrange(begin + 2, len(old) + 1)]
            request_kana = profile == 'kana' or rng.random() < .25
            names, prefixes, suffixes = (KANA_LINES, KANA_PREFIXES, KANA_SUFFIXES) if request_kana else (LINE_NAMES, PREFIXES, SUFFIXES)
            template = rng.choice(TEMPLATES[split][mode])
            if request_kana:
                template = kana_template(template)
            values = {'line': rng.choice(names[source['line_index'] % 3]), 'old': old, 'new': new}
            prefix, suffix = rng.choice(prefixes), rng.choice(suffixes)
            if profile == 'asr_noise':
                template = noise_template(template, rng)
                values = {key: noise(value, rng, slot=key in {'old', 'new'}, line=key == 'line') for key, value in values.items()}
                prefix, suffix = noise(prefix, rng), noise(suffix, rng)
            text, spans = render(template, values, prefix, suffix)
            if text in seen or any(s['text'] in protected for s in spans):
                continue
            if slot_style == 'kanji' and spans and not any(has_kanji(s['text']) for s in spans):
                continue
            row = {'id': f'{split}-v3-{len(result):05d}', 'text': text, 'current_verse': source['verse'],
                'spans': spans, 'kind': 'synthetic_' + mode, 'variant': profile, 'slot_style': slot_style,
                'partial_source': partial, 'source_entry_ids': [source['entry_id'], target['entry_id']],
                'source_groups': [source['group_id'], target['group_id']], 'template': template,
                'source_reading_phrases': [source['text'], target['text']],
                'expectation': EXPECTATION, 'pass_criteria': CRITERIA,
                'scope': '合成の漢字表記案。本人の元表記やASR修復ではなく、文字範囲抽出だけを評価'}
            validate_spans(row)
            result.append(row)
            seen.add(text)
            break
        else:
            raise ValueError(f'Cannot fill {split}/{profile} without overlap')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--previous-data', type=Path, required=True)
    parser.add_argument('--train-variants', type=Path, required=True)
    parser.add_argument('--eval-variants', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    raise SystemExit("旧方式のランダム組合せ・文字切り出し教材は不採用です。この生成入口は停止しています。")
    if args.output.exists():
        raise ValueError('Never overwrite a dataset')
    materials = json.loads((args.previous_data / 'materials.json').read_text())
    train_annotations = json.loads(args.train_variants.read_text())
    eval_annotations = json.loads(args.eval_variants.read_text())
    variants = {split: checked_variants(materials[split], train_annotations['variants'] if split == 'train' else eval_annotations[split]['variants']) for split in ['train', 'dev', 'test']}
    # 分割済みの句群を変えず、表記案でも全文が他分割の素材と重なるものは使わない。
    surfaces = {split: {s for values in variants[split].values() for s in values} | {item['text'] for item in materials[split]} for split in materials}
    for a, b in [('train', 'dev'), ('train', 'test'), ('dev', 'test')]:
        if surfaces[a] & surfaces[b]:
            raise ValueError(f'Cross-split surface overlap: {a}/{b}')
    heldout_names = ['real_test', 'new_real_test', 'regression']
    heldout = [r for name in heldout_names for r in read_jsonl(args.previous_data / f'{name}.jsonl')]
    protected = {s['text'] for row in heldout for s in row['spans']}
    protected |= {line.strip() for row in heldout for line in row['current_verse'].splitlines() if line.strip()}
    seen = {r['text'] for r in heldout}
    # 手動教師もv2と同じ44行。独立した実例数11件を増やしたとは数えない。
    manual = [r for r in read_jsonl(args.previous_data / 'train.jsonl') if r.get('kind') == 'real_manually_annotated']
    seen |= {r['text'] for r in manual}
    rng = random.Random(args.seed)
    data = {}
    for split, count in [('test', 960), ('dev', 960), ('train', 6000)]:
        other_surfaces = set().union(*(surfaces[s] for s in surfaces if s != split))
        data[split] = build_split(split, count, materials[split], variants[split], protected | other_surfaces, seen, rng)
    data['train'].extend(manual)
    args.output.mkdir(parents=True)
    for split, rows in data.items():
        for row in rows:
            validate_spans(row)
        (args.output / f'{split}.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
    for name in heldout_names:
        shutil.copy2(args.previous_data / f'{name}.jsonl', args.output / f'{name}.jsonl')
    shutil.copy2(args.previous_data / 'test.jsonl', args.output / 'previous_test.jsonl')
    for name in ['materials.json', 'poem-groups.json', 'source-manifest.json', 'ambiguous-not-scored.json']:
        shutil.copy2(args.previous_data / name, args.output / name)
    report = {'seed': args.seed, 'target_mix': {'kanji': .5, 'kana': .3, 'asr_noise': .2},
        'splits': {name: summarize(rows) for name, rows in data.items()},
        'variants': {split: len(mapping) for split, mapping in variants.items()},
        'previous_data': str(args.previous_data), 'fixed_groups': True,
        'annotation_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in [args.train_variants, args.eval_variants]},
        'frozen_evaluation_sha256': {name: hashlib.sha256((args.output / f'{name}.jsonl').read_bytes()).hexdigest() for name in heldout_names + ['previous_test']},
        'scope': 'All real dialogue sets are now known regression sets; no claim of a new blind real-dialogue test'}
    write_json(args.output / 'dataset-report.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
