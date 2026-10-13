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
