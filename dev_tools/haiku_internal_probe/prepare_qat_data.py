"""公開corpusの学習・評価分割を、モデル試験より先に固定する。

WikiTextの既存train/testを保持し、段落のhash順で抽出する。文章は改変しない。
公式の意味評価セットも別ファイルとして識別し、ドギドの観測例は学習へ入れない。
"""
import argparse
import hashlib
import json
from pathlib import Path

import pyarrow.parquet as pq


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def prepare(base):
    selected = {}
    for split, count in [('train', 100), ('test', 32)]:
        source = base / 'data' / f'wikitext-{split}.parquet'
        rows = pq.read_table(source)['text'].to_pylist()
        # 128tokenを十分に超える段落だけを採る。先頭や短い見出しへの偏りを避ける。
        candidates = {sha(text): {'text': text, 'sha256': sha(text), 'source_row': row}
                      for row, text in enumerate(rows) if len(text) >= 1500}
        ordered = sorted(candidates, key=lambda key: sha('qat-jspace-20261017:' + key))
        selected[split] = [candidates[key] for key in ordered[:count]]
        if len(selected[split]) != count:
            raise ValueError('insufficient distinct paragraphs')
    if {x['sha256'] for x in selected['train']} & {x['sha256'] for x in selected['test']}:
        raise ValueError('train/test text overlap')
    official = base / 'reference/jacobian-lens/data/evaluations'
    manifest = {'dataset': 'Salesforce/wikitext', 'configuration': 'wikitext-2-raw-v1',
                'source': 'https://huggingface.co/datasets/Salesforce/wikitext',
                'license': ['CC-BY-SA-3.0', 'GFDL'],
                'selection': 'distinct >=1500-character paragraphs; seeded SHA256 ordering',
                'train_count': len(selected['train']), 'test_count': len(selected['test']),
                'source_files': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in sorted((base/'data').glob('*.parquet'))},
                'official_semantic_evaluations': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                                for p in sorted(official.glob('*.json'))},
                'dogido_examples_in_training': False,
                'tokenization': 'native tokenizer encode(add_special_tokens=True), first 128 tokens; no chat instruction added'}
    for split, values in selected.items():
        target = base / 'data' / f'{split}.json'
        target.write_text(json.dumps(values, ensure_ascii=False, indent=2))
        manifest[f'{split}_sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
    (base/'data/manifest.json').write_text(json.dumps(manifest,indent=2))
    print(json.dumps({k: manifest[k] for k in ['train_count','test_count','train_sha256','test_sha256']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('base', type=Path)
    prepare(parser.parse_args().base)
