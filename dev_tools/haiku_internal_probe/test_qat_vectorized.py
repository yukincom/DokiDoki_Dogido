"""方向だけをまとめる入力VJPの数式・置換・復元を、小さなGPU配列で検査する。"""
import unittest

import mlx.core as mx
import numpy as np
from mlx.nn import QuantizedLinear
from mlx_lm.models.switch_layers import QuantizedSwitchLinear

from qat_vectorized import _MappingAudit, _input_derivatives


class CoordinateDerivativeTests(unittest.TestCase):
    def setUp(self):
        mx.random.seed(1743)

    def compare_directions(self, operation, x):
        """異なるcotangentを各方向へ渡し、独立なnative VJPと突合する。"""
        y = operation(x)
        directions = mx.random.normal((4, *y.shape))
        mx.eval(y, directions)
        expected = mx.stack([
            mx.vjp(operation, [x], [directions[i]])[1][0] for i in range(4)])
        mx.eval(expected)
        with _input_derivatives():
            actual_forward = operation(x)
            scalar = mx.vjp(operation, [x], [directions[0]])[1][0]
        mx.eval(actual_forward, scalar)
        np.testing.assert_array_equal(np.array(actual_forward), np.array(y))
        np.testing.assert_allclose(np.array(scalar), np.array(expected[0]), rtol=2e-5, atol=2e-5)
        for width in [1, 2, 4]:
            audit = _MappingAudit()
            with _input_derivatives(audit):
                actual = mx.vmap(lambda c: mx.vjp(operation, [x], [c])[1][0])(directions[:width])
            audit.complete()
            mx.eval(actual)
            self.assertTrue(bool(mx.all(mx.isfinite(actual)).item()))
            np.testing.assert_allclose(np.array(actual), np.array(expected[:width]), rtol=2e-5, atol=2e-5)

    def test_dense_with_bias(self):
        linear = QuantizedLinear(64, 128, bias=True)
        linear.eval()
        self.compare_directions(linear, mx.random.normal((1, 3, 64)))

    def test_switch_sorted_repeated_experts(self):
        linear = QuantizedSwitchLinear(64, 128, 4, bias=True)
        linear.eval()
        indices = mx.repeat(mx.arange(4), 16)
        self.compare_directions(lambda x: linear(x, indices, True), mx.random.normal((64, 1, 64)))

    def test_rms_with_and_without_weight(self):
        for weight in [None, mx.random.uniform(shape=(64,))]:
            self.compare_directions(lambda x: mx.fast.rms_norm(x, weight, 1e-6),
                                    mx.random.normal((1, 3, 64)))

    def test_audit_rejects_empty_and_unreplaced(self):
        audit = _MappingAudit()
        with self.assertRaises(RuntimeError):
            audit.complete()
        value = audit.placeholder(mx.ones((1, 3)), 2, mx.float32, object())
        mx.eval(value)
        self.assertTrue(bool(mx.all(mx.isnan(value)).item()))
        with self.assertRaises(RuntimeError):
            audit.complete()

    def test_restore_on_error_and_unsupported_unsorted_input(self):
        before = (QuantizedLinear.__call__, QuantizedSwitchLinear.__call__, mx.fast.rms_norm)
        linear = QuantizedSwitchLinear(64, 64, 4, bias=False)
        with self.assertRaises(ValueError):
            with _input_derivatives():
                linear(mx.ones((1, 1, 64)), mx.array([0]), False)
        self.assertEqual(before, (QuantizedLinear.__call__, QuantizedSwitchLinear.__call__, mx.fast.rms_norm))


if __name__ == '__main__':
    unittest.main()
