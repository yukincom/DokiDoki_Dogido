"""評価結果の分母、依頼2値の境界、dev選択の分離を小さな既知例で確認する。"""
import unittest

from evaluation import LABELS, classification_metrics, selection_key, summarize


class EvaluationTests(unittest.TestCase):
    def test_action_errors_and_binary_request_have_distinct_denominators(self):
        gold = LABELS + ['other_request']
        predicted = ['equip_sword', 'enemy_information', 'recall_mount',
                     'recall_mount', 'not_request', 'other_request']
        rows = [dict(gold=a, predicted=b, tags=['negation'], needs_clarification=False,
                     context=[], source='synthetic') for a, b in zip(gold, predicted)]
        result = summarize(rows)
        self.assertEqual(result['correct'], 3)
        self.assertEqual(result['non_action_to_action'], {'count': 1, 'denominator': 4, 'rate': .25})
        self.assertEqual(result['sword_mount_confusion'], {'count': 1, 'denominator': 2, 'rate': .5})
        self.assertEqual(result['incorrect_action_predictions']['count'], 2)
        self.assertEqual(result['incorrect_action_predictions']['denominator'], 3)
        self.assertEqual(result['binary_request']['correct'], 4)
        self.assertEqual(result['subsets']['negation']['denominator'], 6)
        self.assertIsNone(result['subsets']['needs_clarification']['rate'])

    def test_scope_subsets_keep_separate_denominators(self):
        rows = [
            dict(gold='other_request', predicted='equip_sword', tags=['unsupported_request'],
                 needs_clarification=False, context=[], source='synthetic'),
            dict(gold='equip_sword', predicted='equip_sword', tags=['direct'],
                 needs_clarification=False, context=[], source='synthetic'),
            dict(gold='other_request', predicted='other_request', tags=['question'],
                 needs_clarification=False, context=[], source='synthetic'),
        ]
        result = summarize(rows)
        self.assertEqual(result['subsets']['unsupported_request'],
                         {'count': 0, 'denominator': 1, 'rate': 0.0})
        self.assertEqual(result['subsets']['ordinary_scope'],
                         {'count': 2, 'denominator': 2, 'rate': 1.0})
        self.assertEqual(result['correct'], 2)
        self.assertEqual(result['n'], 3)
        empty = summarize(rows[1:])['subsets']['unsupported_request']
        self.assertEqual(empty, {'count': 0, 'denominator': 0, 'rate': None})

    def test_macro_f1_and_confusion_orientation(self):
        result = classification_metrics(['a', 'a', 'b'], ['a', 'b', 'b'], ['a', 'b'])
        self.assertEqual(result['confusion_matrix'], [[1, 1], [0, 1]])
        self.assertAlmostEqual(result['macro_f1'], 2 / 3)
        self.assertEqual(result['per_class']['a']['support'], 2)

    def test_selection_rejects_test_and_uses_documented_ties(self):
        base = dict(selection_split='dev', macro_f1=.8, loss=.5, epoch=3., trial_index=0)
        self.assertGreater(selection_key(dict(base, macro_f1=.81, loss=.9)), selection_key(base))
        self.assertGreater(selection_key(dict(base, loss=.4, epoch=8.)), selection_key(base))
        self.assertGreater(selection_key(dict(base, epoch=2.)), selection_key(base))
        with self.assertRaises(ValueError):
            selection_key(dict(base, selection_split='test'))
        with self.assertRaises(ValueError):
            selection_key(dict(base, loss=float('nan')))


if __name__ == '__main__':
    unittest.main()
