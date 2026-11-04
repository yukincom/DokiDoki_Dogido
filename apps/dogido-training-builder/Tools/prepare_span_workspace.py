"""承認済みの語句抽出教材を編集用CSVへ移す。既存プロジェクトは上書きしない。"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys
import tempfile

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
import span_builder


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def prepare(destination, source_run=None):
    destination = Path(destination).expanduser().resolve()
    if destination.exists():
        raise ValueError('既存の教材を上書きしません: ' + str(destination))
    source = Path(source_run).expanduser().resolve() if source_run else None
    rows = []
    hashes = {}
    groups = {}
    if source:
        manifest = source / 'data/split-manifest.json'
        if manifest.is_file():
            for group in json.loads(manifest.read_text()).get('groups', []):
                for rid in group['row_ids']:
                    groups[rid] = group['group_id']
        for split in ('train', 'dev', 'test', 'regression'):
            path = source / 'data' / f'{split}.jsonl'
            if not path.exists():
                continue
            hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
            for row in read_jsonl(path):
                spans = {}
                for span in row['spans']:
                    if span['role'] in spans or row['text'][span['start']:span['end']] != span['text']:
                        raise ValueError('一役割一範囲の確認済み教材を指定してください: ' + row['id'])
                    spans[span['role']] = span
                occurrences = {}
                for role in ('from', 'to'):
                    span = spans.get(role)
                    starts = span_builder.occurrences(row['text'], span['text']) if span else []
                    occurrences[role] = str(starts.index(span['start']) + 1) if len(starts) > 1 else ''
                references = row.get('logged_matches', []) + row.get('related_log_records', [])
                entry_id = row.get('entry_id') or next((r['entry_id'] for r in references if r.get('entry_id')), '')
                group = groups.get(row['id']) or row.get('group_id') or row.get('split_group') or row.get('semantic_group') or entry_id or row['id']
                form = row.get('form', 'real')
                kind = {'canonical': '合成・正表記', 'stt_variant': '合成・認識表記', 'real': '実例'}[form]
                rows.append({'使う': 'はい', 'ID': row['id'], '用途': {'train': '学習', 'dev': '調整', 'test': 'テスト', 'regression': '回帰'}[split],
                    'グループ': group, '句ID': entry_id, '現在の句': row.get('current_verse') or '', '発話': row['text'],
                    '変更元': spans.get('from', {}).get('text', ''), '元の出現番号': occurrences['from'],
                    '変更先': spans.get('to', {}).get('text', ''), '先の出現番号': occurrences['to'],
                    '抽出なし': '' if spans else 'はい', '種別': kind,
                    'メモ': '既知の評価例。学習へ移さず回帰として保持。' if split == 'regression' else '承認済み初期教材。元の分割を保持。'})
    config = {'schema': span_builder.SCHEMA, 'id': 'dogido-edit-span', 'name': 'ドギド・語句抽出',
              'input_format': span_builder.INPUT_FORMAT, 'dataset_file': '教材.csv'}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.preparing-span-', dir=destination.parent) as temporary:
        stage = Path(temporary)
        (stage / 'project.json').write_bytes(span_builder.encoded(config))
        for filename, items in [('教材.csv', rows), ('空のテンプレート.csv', [])]:
            with (stage / filename).open('w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=span_builder.HEADERS, lineterminator='\n')
                writer.writeheader()
                writer.writerows(items)
        (stage / '使い方.md').write_bytes((SOURCE / '語句抽出の使い方.md').read_bytes())
        if source:
            splits, skipped, _ = span_builder.read_csv([stage / '教材.csv'])
            # 元の全教師値をCSV往復後に照合。表記・範囲・改行・用途を保つ。
            assert not skipped
            for split, actual in splits.items():
                path = source / 'data' / f'{split}.jsonl'
                original = {r['id']: r for r in read_jsonl(path)} if path.exists() else {}
                assert set(original) == {r['id'] for r in actual}
                for row in actual:
                    for field in ('text', 'current_verse', 'spans'):
                        assert row[field] == original[row['id']].get(field, ''), (row['id'], field)
            (stage / '初期教材の出典.json').write_bytes(span_builder.encoded({
                'source_run_name': source.name, 'source_sha256': hashes,
                'counts': {s: len(rs) for s, rs in splits.items()}, 'csv_roundtrip_exact': True,
                'scope': '教材の移行と原文範囲の一致。再学習・外部送信なし。'}))
        stage.rename(destination)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-run', type=Path)
    args = parser.parse_args()
    try:
        print(prepare(args.output, args.source_run))
    except (ValueError, OSError) as exc:
        parser.exit(1, str(exc) + '\n')
