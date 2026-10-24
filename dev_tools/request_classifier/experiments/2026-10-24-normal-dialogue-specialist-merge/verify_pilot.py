"""保存済みの全予測・入力・合成式を、評価ドライバーと独立に照合する。"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

import torch
from safetensors.torch import load_file

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
RUN = HERE / 'runs/pilot'
SOURCE = HERE.parent / '2026-10-24-normal-dialogue-colab/artifacts/colab-results'
RELEASE = SOURCE / 'dev_tools/request_classifier/releases/2026-10-24-normal-dialogue-578'
CANONICAL = REPO / 'dev_tools/request_classifier/releases/2026-10-24-normal-dialogue-578'


def read(path):
    return json.loads(path.read_text())


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hashes(path):
    return {p.name: digest(p) for p in sorted(path.iterdir()) if p.is_file()}


def main():
    torch.set_num_threads(4)
    protocol = read(RUN / 'protocol.json')
    labels = list(protocol['label_to_id'])
    assert len(labels) == 16
    for path, expected in protocol['source_sha256'].items():
        assert digest(REPO / path) == expected
    assert digest(RUN / 'source-at-execution.py') == digest(HERE / 'run_pilot.py')
    for path, expected in protocol['source_training_sha256'].items():
        assert digest(SOURCE / path) == expected
    assert hashes(REPO / protocol['base']['path']) == protocol['base']['sha256']
    source_rows = {s: rows(RELEASE / f'{s}.jsonl') for s in ('train', 'dev', 'test')}
    for split, expected in protocol['source_data_sha256'].items():
        assert digest(RELEASE / f'{split}.jsonl') == digest(CANONICAL / f'{split}.jsonl') == expected
    train = {r['id']: r for r in source_rows['train']}
    held = source_rows['dev'] + source_rows['test']
    held_ids, held_groups = {r['id'] for r in held}, {r['group_id'] for r in held}
    data = {n: rows(RUN / 'data' / f'{n}.jsonl') for n in ('gaze', 'recall', 'sword', 'joint')}
    for name, rr in data.items():
        assert digest(RUN / 'data' / f'{name}.jsonl') == protocol['data_sha256'][name]
        assert all(r == train[r['id']] and r['id'] not in held_ids and r['group_id'] not in held_groups for r in rr)
        assert set(r['label'] for r in rr) == set(labels)
        if name != 'joint':
            assert len(rr) == len({r['id'] for r in rr})
            target = protocol['inventory'][name]['target_labels']
            positive = [r for r in rr if r['label'] in target]
            assert {r['id'] for r in positive} == {r['id'] for r in train.values() if r['label'] in target}
            assert len(positive) * 2 == len(rr)
    assert Counter(r['id'] for n in ('gaze', 'recall', 'sword') for r in data[n]) == Counter(r['id'] for r in data['joint'])
    base = load_file(REPO / protocol['base']['path'] / 'model.safetensors')
    keys = sorted(base)
    trained = read(RUN / 'training-results.json')
    experts = []
    for name, info in trained.items():
        path = RUN / 'weights' / name
        assert hashes(path) == info['checkpoint_sha256']
        assert info['initial_all_tensors_equal_base'] and info['final_epoch'] == 3
        assert info['steps'] == math.ceil(len(data[name]) / 16) * 3
        state = load_file(path / 'model.safetensors')
        assert state.keys() == base.keys()
        assert all(v.dtype == torch.float32 and bool(torch.isfinite(v).all()) for v in state.values())
        assert info['changed_tensors'] == [k for k in base if not torch.equal(base[k], state[k])]
        if name != 'joint':
            experts.append(state)
    vectors = torch.stack([torch.cat([(s[k] - base[k]).flatten() for k in keys]) for s in experts])
    # 作者関数を呼ばず、同じ閾値・符号合意・非ゼロ平均を別の計算で照合。
    threshold = torch.kthvalue(vectors.abs(), vectors.shape[1] - int(vectors.shape[1] * .2), dim=1).values[:, None]
    trimmed = torch.where(vectors.abs() >= threshold, vectors, torch.zeros_like(vectors))
    signs = trimmed.sum(0).sign()
    majority = signs.sum().sign()
    signs = torch.where(signs == 0, majority, signs)
    keep = ((signs > 0)[None, :] & (trimmed > 0)) | ((signs <= 0)[None, :] & (trimmed < 0))
    numerator = torch.where(keep, trimmed, torch.zeros_like(trimmed)).sum(0)
    ties = numerator / keep.sum(0).float().clamp(min=1)
    arithmetic = vectors.sum(0)
    merged_hashes = read(RUN / 'merged-models.json')
    for recipe in protocol['recipes']:
        path = RUN / 'weights' / recipe['id']
        assert hashes(path) == merged_hashes[recipe['id']]
        actual = load_file(path / 'model.safetensors')
        delta = arithmetic if recipe['method'] == 'arithmetic' else ties
        offset = 0
        for key in keys:
            n = base[key].numel()
            expected = base[key] + recipe['scale'] * delta[offset:offset+n].reshape(base[key].shape)
            assert torch.equal(actual[key], expected), (recipe['id'], key)
            offset += n
        assert offset == delta.numel()
    summary = read(RUN / 'summary.json')
    checked, group_metrics = 0, {}
    for name, results in summary.items():
        group_metrics[name] = {}
        for split in ('dev', 'test'):
            pred = rows(RUN / 'reports' / name / f'{split}-predictions.jsonl')
            gold = source_rows[split]
            assert len(pred) == len(gold)
            matrix = [[0 for _ in labels] for _ in labels]
            for p, r in zip(pred, gold, strict=True):
                assert all(p[k] == r[k] for k in ('id', 'text', 'context', 'source', 'group_id', 'tags', 'needs_clarification'))
                assert p['gold'] == r['label']
                probs = p['probabilities']
                assert list(probs) == labels and all(math.isfinite(v) and 0 <= v <= 1 for v in probs.values())
                assert abs(sum(probs.values()) - 1) < 1e-5
                assert max(probs, key=probs.get) == p['predicted']
                assert p['correct'] == (p['gold'] == p['predicted'])
                assert abs(p['confidence'] - max(probs.values())) < 1e-7
                matrix[labels.index(p['gold'])][labels.index(p['predicted'])] += 1
            correct = sum(matrix[i][i] for i in range(len(labels)))
            m = results[split]
            assert m['n'] == len(pred) and m['correct'] == correct and m['confusion_matrix'] == matrix
            f1s = []
            for i, label in enumerate(labels):
                tp, support, predicted = matrix[i][i], sum(matrix[i]), sum(row[i] for row in matrix)
                precision, recall = tp / predicted if predicted else 0, tp / support if support else 0
                f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0
                expected = {'precision': precision, 'recall': recall, 'f1': f1,
                    'support': support, 'predicted_count': predicted}
                assert all(math.isclose(m['per_class'][label][k], v, abs_tol=1e-10) for k, v in expected.items())
                f1s.append(f1)
            assert abs(m['macro_f1'] - sum(f1s) / len(f1s)) < 1e-10
            assert abs(m['accuracy'] - correct / len(pred)) < 1e-10
            groups = {n: protocol['inventory'][n]['target_labels'] for n in ('gaze', 'recall', 'sword')}
            groups['other'] = ['not_target', 'enemy_information']
            group_metrics[name][split] = {n: {
                'n': sum(p['gold'] in labs for p in pred),
                'correct': sum(p['correct'] for p in pred if p['gold'] in labs),
                'false_group_entries': sum(p['gold'] not in labs and p['predicted'] in labs for p in pred)
                } for n, labs in groups.items()}
            checked += len(pred)
    selection = read(RUN / 'selection.json')
    test_start = read(RUN / 'test-started.json')
    order = protocol['candidate_order']
    rank = lambda n: (-summary[n]['dev']['macro_f1'], summary[n]['dev']['loss'], order.index(n))
    assert selection['ranking'] == sorted(order, key=rank)
    assert selection['best_overall'] == min(order, key=rank)
    assert selection['best_merge'] == min([r['id'] for r in protocol['recipes']], key=rank)
    assert selection['results'] == {n: {'dev': m['dev']} for n, m in summary.items()}
    assert test_start['selection_sha256'] == digest(RUN / 'selection.json')
    assert datetime.fromisoformat(selection['frozen_at_utc']) < datetime.fromisoformat(test_start['started_at_utc'])
    baseline = {}
    for split in ('dev', 'test'):
        original = rows(SOURCE / 'reports/seed-43' / f'{split}-predictions.jsonl')
        current = rows(RUN / 'reports/base-seed43' / f'{split}-predictions.jsonl')
        assert [r['id'] for r in original] == [r['id'] for r in current]
        assert [r['predicted'] for r in original] == [r['predicted'] for r in current]
        baseline[split] = max(abs(a['probabilities'][k] - b['probabilities'][k])
            for a, b in zip(original, current) for k in labels)
    verified = {'verified_at_utc': datetime.now(timezone.utc).isoformat(),
        'prediction_rows': checked, 'training_rows_unchanged': True, 'heldout_ids_groups_in_training': 0,
        'joint_occurrences_match_all_experts': True, 'joint_unique_rows': len({r['id'] for r in data['joint']}),
        'trained_models': 4, 'merged_models': 5, 'merged_tensors_checked': 5 * len(keys),
        'all_saved_merges_match_independent_formulas': True, 'all_prediction_metrics_recomputed': True,
        'selection_frozen_before_test': True, 'base_predictions_identical_to_colab': True,
        'base_max_probability_difference_vs_colab': baseline, 'source_models_and_materials_unchanged': True,
        'verifier_sha256': digest(Path(__file__))}
    (RUN / 'verification.json').write_text(json.dumps(verified, ensure_ascii=False, indent=2) + '\n')
    (RUN / 'group-metrics.json').write_text(json.dumps(group_metrics, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(verified, ensure_ascii=False))


if __name__ == '__main__':
    main()
