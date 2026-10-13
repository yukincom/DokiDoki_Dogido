"""5分類の予測を集計し、devだけで学習設定を選ぶ独立補助。

入力は正解・予測ラベルと注釈、出力は混同行列・分類別指標・誤誘導率。
モデル呼出しや本体操作は行わず、評価分割の成績を設定選択には使わない。
"""
from __future__ import annotations

import math
from dataset import LABEL_TO_ID

LABELS = list(LABEL_TO_ID)
ACTIONS = {'equip_sword', 'recall_mount'}


def classification_metrics(gold: list[str], predicted: list[str], labels: list[str]) -> dict:
    """同じ順序の正解・予測を集計する。行が正解、列が予測で、ゼロ除算は0。"""
    if len(gold) != len(predicted) or not gold:
        raise ValueError('nonempty aligned gold and predictions required')
    index = {label: i for i, label in enumerate(labels)}
    matrix = [[0 for _ in labels] for _ in labels]
    for actual, estimate in zip(gold, predicted):
        matrix[index[actual]][index[estimate]] += 1
    per_class = {}
    for label, i in index.items():
        tp = matrix[i][i]
        support = sum(matrix[i])
        predicted_count = sum(row[i] for row in matrix)
        precision = tp / predicted_count if predicted_count else 0.0
        recall = tp / support if support else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {'precision': precision, 'recall': recall, 'f1': f1,
                            'support': support, 'predicted_count': predicted_count}
    correct = sum(matrix[i][i] for i in range(len(labels)))
    return {'n': len(gold), 'correct': correct, 'accuracy': correct / len(gold),
            'macro_f1': sum(v['f1'] for v in per_class.values()) / len(labels),
            'per_class': per_class, 'label_order': labels, 'confusion_matrix': matrix}


def rate(numerator: int, denominator: int) -> dict:
    """分母と件数を保持する。対象がない率は0%とせずnullにする。"""
    return {'count': numerator, 'denominator': denominator,
            'rate': numerator / denominator if denominator else None}


def summarize(rows: list[dict]) -> dict:
    """各行のラベル・注釈から5分類、依頼2値、操作の取り違え、部分成績を返す。"""
    gold = [r['gold'] for r in rows]
    predicted = [r['predicted'] for r in rows]
    result = classification_metrics(gold, predicted, LABELS)
    to_binary = lambda label: 'not_request' if label == 'not_request' else 'request'
    result['binary_request'] = classification_metrics(
        list(map(to_binary, gold)), list(map(to_binary, predicted)), ['not_request', 'request'])
    non_actions = [r for r in rows if r['gold'] not in ACTIONS]
    action_rows = [r for r in rows if r['gold'] in ACTIONS]
    result['non_action_to_action'] = rate(
        sum(r['predicted'] in ACTIONS for r in non_actions), len(non_actions))
    result['sword_mount_confusion'] = rate(
        sum(r['predicted'] in ACTIONS and r['predicted'] != r['gold'] for r in action_rows),
        len(action_rows))
    result['incorrect_action_predictions'] = rate(
        sum(r['predicted'] in ACTIONS and r['predicted'] != r['gold'] for r in rows),
        sum(r['predicted'] in ACTIONS for r in rows))
    result['subsets'] = {}
    subsets = {tag: [r for r in rows if tag in r['tags']]
               for tag in ['negation', 'quotation', 'ellipsis', 'self_location', 'iruka_collision', 'unsupported_request']}
    subsets['ordinary_scope'] = [r for r in rows if 'unsupported_request' not in r['tags']]
    subsets['needs_clarification'] = [r for r in rows if r['needs_clarification']]
    subsets['with_context'] = [r for r in rows if r['context']]
    subsets['user_example'] = [r for r in rows if r['source'] == 'user_example']
    for name, subset in subsets.items():
        result['subsets'][name] = rate(
            sum(r['gold'] == r['predicted'] for r in subset), len(subset))
    return result


def selection_key(trial: dict) -> tuple:
    """dev F1、dev loss、早いepoch、事前の設定順で選ぶ。testの選択使用を拒否する。"""
    if trial['selection_split'] != 'dev':
        raise ValueError('selection is restricted to dev')
    values = [trial['macro_f1'], trial['loss'], trial['epoch']]
    if not all(math.isfinite(x) for x in values):
        raise ValueError('non-finite selection metric')
    return trial['macro_f1'], -trial['loss'], -trial['epoch'], -trial['trial_index']


def select_shared_learning_rate(trials: list[dict], seeds: list[int], learning_rates: list[float]) -> dict:
    """各seed・学習率の最良devを受け、全seed平均で一つの学習率を選ぶ。"""
    from statistics import mean
    if not seeds or not learning_rates or len(set(seeds)) != len(seeds) or len(set(learning_rates)) != len(learning_rates):
        raise ValueError('nonempty unique seeds and learning rates required')
    expected = {(seed, lr) for seed in seeds for lr in learning_rates}
    by_pair = {(t['seed'], t['learning_rate']): t for t in trials}
    if set(by_pair) != expected or len(by_pair) != len(trials):
        raise ValueError('exactly one best-dev trial per seed and learning rate required')
    for trial in trials:
        selection_key(trial)  # test混入とNaNを既存の検査で拒否する。
    candidates = []
    for index, lr in enumerate(learning_rates):
        rows = [by_pair[seed, lr] for seed in seeds]
        candidates.append(dict(selection_split='dev', learning_rate=lr, trial_index=index,
            **{key: mean(row[key] for row in rows) for key in ['macro_f1', 'loss', 'epoch', 'accuracy']}))
    chosen = max(candidates, key=selection_key)
    return {'trials': trials, 'learning_rate_means': candidates,
            'selected_learning_rate': chosen['learning_rate'],
            'selected_models': [by_pair[seed, chosen['learning_rate']] for seed in seeds]}


def distribution(values: list[float]) -> dict:
    """反復実験の平均・標本標準偏差・範囲を返す。1回のばらつきは未測定。"""
    from statistics import mean, stdev
    if not values or not all(math.isfinite(v) for v in values):
        raise ValueError('nonempty finite measurements required')
    return {'values': values, 'mean': mean(values),
            'sample_stddev': stdev(values) if len(values) > 1 else None,
            'min': min(values), 'max': max(values)}


def compare_replicates(baseline: list[dict], replicates: dict[str, list[dict]]) -> dict:
    """同一入力の予測を照合し、平均・ばらつき・修正/新規/継続誤りを全seedで残す。"""
    if not baseline or not replicates or len({r['id'] for r in baseline}) != len(baseline):
        raise ValueError('nonempty unique baseline and replicates required')
    keys = ['id', 'text', 'context', 'gold', 'tags', 'needs_clarification', 'source', 'group_id']
    for rows in replicates.values():
        if len(rows) != len(baseline) or any(
            any(old[key] != new[key] for key in keys) for old, new in zip(baseline, rows)):
            raise ValueError('comparison requires identical ordered inputs, labels and annotations')
    def measurements(rows):
        result = summarize(rows)
        values = {key: result[key] for key in ['correct', 'accuracy', 'macro_f1']}
        values['binary_request_accuracy'] = result['binary_request']['accuracy']
        for key in ['non_action_to_action', 'sword_mount_confusion', 'incorrect_action_predictions']:
            values[key] = result[key]['rate']
        actions = [r for r in rows if r['gold'] in ACTIONS]
        values['action_to_non_action'] = rate(sum(r['predicted'] not in ACTIONS for r in actions), len(actions))['rate']
        for key, subset in result['subsets'].items():
            values['subset.' + key] = subset['rate']
        for label, stats in result['per_class'].items():
            for key in ['precision', 'recall', 'f1']:
                values[f'{label}.{key}'] = stats[key]
        return values
    old_values = measurements(baseline)
    values = {name: measurements(rows) for name, rows in replicates.items()}
    stats = {key: distribution([v[key] for v in values.values()])
             for key in old_values if all(v[key] is not None for v in values.values())}
    transitions = {}
    for name, rows in replicates.items():
        groups = {key: [] for key in ['fixed', 'new_errors', 'still_wrong', 'still_correct']}
        for old, new in zip(baseline, rows):
            before, after = old['predicted'] == old['gold'], new['predicted'] == new['gold']
            key = ('still_correct' if after else 'new_errors') if before else ('fixed' if after else 'still_wrong')
            groups[key].append(old['id'])
        transitions[name] = {key: {'count': len(ids), 'ids': ids} for key, ids in groups.items()}
    consensus = {key: [] for key in ['fixed_all', 'new_errors_all', 'still_wrong_all', 'variable']}
    cases = []
    for index, old in enumerate(baseline):
        predictions = {name: rows[index]['predicted'] for name, rows in replicates.items()}
        correct = sum(label == old['gold'] for label in predictions.values())
        before = old['predicted'] == old['gold']
        if correct == len(replicates) and not before:
            consensus['fixed_all'].append(old['id'])
        elif correct == 0:
            consensus['new_errors_all' if before else 'still_wrong_all'].append(old['id'])
        elif correct < len(replicates):
            consensus['variable'].append(old['id'])
        if not before or correct < len(replicates):
            cases.append({**{key: old[key] for key in keys}, 'baseline': old['predicted'],
                          'predictions': predictions, 'correct_seeds': correct})
    return {'n': len(baseline), 'replicate_order': list(replicates),
            'baseline': old_values, 'aggregate': stats, 'transitions': transitions,
            'consensus': consensus, 'cases': cases}
