"""devのみでseedを固定してから、全seedを同じ評価セットと既知回帰で比較する。

見込み: 句・依頼文を分離した評価で丸暗記とseed依存を見分ける。
合格目安: 合成test完全一致95%以上。実対話は件数と全失敗を別集計する。
範囲: 保存済みテキストの実モデル推論。本体・音声認識・句編集は実行しない。
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import time
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'

from span_model import CharacterCodec, metrics, predict_rows, read_jsonl, write_json


def select_seed(runs, data):
    """test予測を読む前に選択を書き出す。exact→F1→小さいseedの固定順。"""
    candidates = []
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in data.glob('*.jsonl')}
    for run in runs:
        manifest = json.loads((run / 'manifest.json').read_text())
        if manifest['dataset_sha256'] != hashes:
            raise ValueError(f'Dataset drift: {run}')
        if not (run / 'training-complete.json').exists():
            raise ValueError(f'Training is incomplete: {run}')
        selection = json.loads((run / 'selection.json').read_text())
        candidates.append({'seed': manifest['arguments']['seed'], 'path': str(run),
            'epoch': selection['epoch'], 'dev': selection['dev_metrics']['all']})
    candidates.sort(key=lambda r: (-r['dev']['exact'], -(r['dev']['span_f1'] or 0), r['seed']))
    return {'selected_seed': candidates[0]['seed'], 'selected_path': candidates[0]['path'],
        'selection_rule': 'dev exact count, then dev span F1, then smaller seed; no test selection',
        'candidates': candidates, 'dataset_sha256': hashes}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runs', type=Path, required=True)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--device', choices=['mps', 'cpu'], default='mps')
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Never overwrite a comparison')
    runs = sorted(args.runs.glob('seed-*'))
    if len(runs) != 5:
        raise ValueError('This experiment requires five completed seeds')
    selection = select_seed(runs, args.data)
    args.output.mkdir(parents=True)
    write_json(args.output / 'frozen-selection.json', selection)
    print(json.dumps({'event': 'selection_frozen', **selection}, ensure_ascii=False), flush=True)
    import torch
    from transformers import BertForTokenClassification
    torch.set_num_threads(4)
    if args.device == 'mps' and not torch.backends.mps.is_available():
        raise RuntimeError('MPS unavailable')
    summary = []
    set_names = ['test', 'new_real_test', 'real_test', 'regression']
    if (args.data / 'previous_test.jsonl').exists():
        set_names.append('previous_test')
    for run in runs:
        codec = CharacterCodec(run / 'model' / 'vocab.txt')
        model = BertForTokenClassification.from_pretrained(run / 'model', local_files_only=True,
            attn_implementation='sdpa').to(args.device)
        report = {'seed': int(run.name.removeprefix('seed-')), 'sets': {}}
        for name in set_names:
            results = predict_rows(model, codec, read_jsonl(args.data / f'{name}.jsonl'), args.device, 48)
            report['sets'][name] = metrics(results)
            report['sets'][name]['by_variant'] = {variant: metrics([r for r in results if r.get('variant', 'real') == variant])
                for variant in sorted({r.get('variant', 'real') for r in results})}
            report['sets'][name]['by_kind'] = {kind: metrics([r for r in results if r.get('kind', 'real') == kind])
                for kind in sorted({r.get('kind', 'real') for r in results})}
            (args.output / f'{run.name}-{name}-predictions.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in results))
        timing = read_jsonl(args.data / 'dev.jsonl')[:12]
        for row in timing:
            predict_rows(model, codec, [row], args.device, 1)
        durations = []
        for _ in range(4):
            for row in timing:
                if args.device == 'mps':
                    torch.mps.synchronize()
                start = time.perf_counter()
                predict_rows(model, codec, [row], args.device, 1)
                if args.device == 'mps':
                    torch.mps.synchronize()
                durations.append((time.perf_counter() - start) * 1000)
        report['latency'] = {'median_ms': statistics.median(durations), 'count': len(durations),
            'scope': 'warm model, encode+inference+decode, batch 1; no audio or startup'}
        summary.append(report)
        write_json(args.output / f'{run.name}-metrics.json', report)
        print(json.dumps({'event': 'seed_evaluated', **report}, ensure_ascii=False), flush=True)
        del model
        if args.device == 'mps':
            torch.mps.empty_cache()
    aggregate = {}
    for name in set_names:
        rates = [r['sets'][name]['all']['exact_rate'] for r in summary]
        aggregate[name] = {'mean': statistics.mean(rates), 'min': min(rates), 'max': max(rates), 'std_population': statistics.pstdev(rates)}
    write_json(args.output / 'comparison.json', {'selection': selection, 'seeds': summary, 'aggregate': aggregate,
        'evaluation_scope': {'test': 'synthetic held-out phrases/templates',
            'new_real_test': 'known regression since v2', 'real_test': 'known regression since v1',
            'regression': 'known three utterances and their ASR variants',
            'previous_test': 'unchanged v2 synthetic test, now regression'}})


if __name__ == '__main__':
    main()
