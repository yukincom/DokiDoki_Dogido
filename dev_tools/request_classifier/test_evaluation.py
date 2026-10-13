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


class ReplicateTests(unittest.TestCase):
    def trials(self):
        return [dict(selection_split='dev', seed=seed, learning_rate=lr, macro_f1=f1,
                     loss=.3, accuracy=f1, epoch=2, trial_index=index)
                for index, (seed, lr, f1) in enumerate([(42, .01, .9), (43, .01, .5),
                                                       (42, .02, .75), (43, .02, .75)])]

    def test_common_rate_uses_seed_average_not_best_single_seed(self):
        from evaluation import select_shared_learning_rate
        result = select_shared_learning_rate(self.trials(), [42, 43], [.01, .02])
        self.assertEqual(result['selected_learning_rate'], .02)
        self.assertEqual([t['seed'] for t in result['selected_models']], [42, 43])
        self.assertTrue(all(t['learning_rate'] == .02 for t in result['selected_models']))

    def test_missing_duplicate_and_test_trials_are_rejected(self):
        from evaluation import select_shared_learning_rate
        rows = self.trials()
        for invalid in [rows[:-1], rows + [rows[0]], [dict(rows[0], selection_split='test')] + rows[1:]]:
            with self.assertRaises(ValueError):
                select_shared_learning_rate(invalid, [42, 43], [.01, .02])

    def test_common_rate_ties_use_loss_then_epoch_then_declared_order(self):
        from evaluation import select_shared_learning_rate
        rows = [dict(t, macro_f1=.8) for t in self.trials()]
        self.assertEqual(select_shared_learning_rate(rows, [42, 43], [.02, .01])['selected_learning_rate'], .02)
        rows[0]['loss'] = .1
        self.assertEqual(select_shared_learning_rate(rows, [42, 43], [.02, .01])['selected_learning_rate'], .01)
        rows[0]['loss'] = .3
        rows[0]['epoch'] = 1
        self.assertEqual(select_shared_learning_rate(rows, [42, 43], [.02, .01])['selected_learning_rate'], .01)

    def test_dispersion_is_sample_standard_deviation(self):
        from evaluation import distribution
        self.assertEqual(distribution([1., 2., 3.]), dict(values=[1., 2., 3.], mean=2., sample_stddev=1., min=1., max=3.))
        self.assertIsNone(distribution([1.])['sample_stddev'])
        with self.assertRaises(ValueError):
            distribution([float('nan')])

    def test_comparison_checks_input_and_tracks_every_transition(self):
        from evaluation import compare_replicates
        base = [dict(id=str(i), text='本文', context=[], gold=gold, predicted=pred,
                     tags=[], source='synthetic', group_id=str(i), needs_clarification=False)
                for i, (gold, pred) in enumerate([('not_request', 'other_request'),
                    ('equip_sword', 'equip_sword'), ('recall_mount', 'other_request'),
                    ('enemy_information', 'enemy_information')])]
        a = [dict(r, predicted=v) for r, v in zip(base, ['not_request', 'other_request', 'not_request', 'enemy_information'])]
        b = [dict(r, predicted=v) for r, v in zip(base, ['not_request', 'other_request', 'other_request', 'not_request'])]
        result = compare_replicates(base, {'a': a, 'b': b})
        self.assertEqual({k: v['ids'] for k, v in result['transitions']['a'].items()},
                         dict(fixed=['0'], new_errors=['1'], still_wrong=['2'], still_correct=['3']))
        self.assertEqual(result['consensus'], dict(fixed_all=['0'], new_errors_all=['1'], still_wrong_all=['2'], variable=['3']))
        self.assertEqual(result['aggregate']['accuracy']['mean'], .375)
        self.assertEqual(result['aggregate']['action_to_non_action']['values'], [1., 1.])
        with self.assertRaises(ValueError):
            compare_replicates(base, {'changed': [dict(a[0], context=[dict(role='user', text='別の履歴')])] + a[1:]})


if __name__ == '__main__':
    unittest.main()
