"""抽出器の早期停止を固定成績で検証する。実モデルの精度は別の評価で測る。

見込み: best更新と停止時計を同じ成績で扱えば、同点を改善扱いして学習を延長しない。
合格: 改善後3周で停止、途中の改善なら時計をリセット。停止時も最良成績を保持する。
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dev_tools' / 'edit_span_extractor'))
from early_stopping import EarlyStopping


class EarlyStoppingTests(unittest.TestCase):
    def test_three_equal_epochs_stop_and_keep_first_best(self):
        # 見込み: 4周で満点、その後同点なら7周で止まり4周目を残す。
        # 合格: 保存対象は1〜4周、停止は7周目だけ。固定dev成績による制御検査。
        tracker = EarlyStopping(3)
        saved = []; stopped = []
        scores = [(0, 0.0), (18, 0.5), (30, 0.69)] + [(41, 1.0)] * 4
        for epoch, score in enumerate(scores, 1):
            if tracker.observe(score): saved.append(epoch)
            if tracker.should_stop: stopped.append(epoch)
        self.assertEqual(saved, [1, 2, 3, 4])
        self.assertEqual(stopped, [7])
        self.assertEqual(tracker.best, (41, 1.0))

    def test_strict_improvement_resets_counter_in_selection_order(self):
        # 見込み: 完全一致が同じならF1改善で継続、完全一致増ならF1低下でも更新する。
        # 合格: 悪化・同点2周の後の改善で時計を0へ戻し、そこから3周で停止。固定成績のみ。
        tracker = EarlyStopping(3)
        self.assertTrue(tracker.observe((30, .80)))
        self.assertFalse(tracker.observe((30, .80)))
        self.assertFalse(tracker.observe((29, .99)))
        self.assertFalse(tracker.should_stop)
        self.assertTrue(tracker.observe((30, .85)))
        self.assertEqual(tracker.stale_epochs, 0)
        self.assertTrue(tracker.observe((31, .70)))
        for expected in [1, 2, 3]:
            self.assertFalse(tracker.observe((30, .99)))
            self.assertEqual(tracker.stale_epochs, expected)
            self.assertEqual(tracker.should_stop, expected == 3)
        self.assertEqual(tracker.best, (31, .70))

    def test_zero_patience_retains_previous_no_early_stop_behavior(self):
        # 見込み: オプション未指定なら従来どおり設定epochを完走する。
        # 合格: 20周改善なしでも止まらずbestは維持。負の待機数は受付時に拒否。
        tracker = EarlyStopping()
        self.assertTrue(tracker.observe((41, 1.0)))
        for _ in range(20):
            self.assertFalse(tracker.observe((40, .98)))
            self.assertFalse(tracker.should_stop)
        self.assertEqual(tracker.best, (41, 1.0))
        with self.assertRaises(ValueError): EarlyStopping(-1)


if __name__ == '__main__':
    unittest.main()
