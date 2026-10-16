"""同じbatch=1のforwardを保ち、Jacobianの方向だけをまとめる実験用補助。

MLX 0.32.0に未実装の量子化行列のvmapを、入力微分に限定して補う。
重み・routerの通常計算は変更せず、方向軸を行列の行へ移して同じ演算へ渡す。
クラスの差し替えは独立測定プロセス内だけで、例外時も元へ戻す。
"""
from contextlib import contextmanager
import importlib.metadata

import mlx.core as mx
from mlx.nn import QuantizedLinear
from mlx_lm.models.switch_layers import QuantizedSwitchLinear


class _MappingAudit:
    """MLXの内部placeholderが全て実演算へ置換されたことを返却前に確認する。"""

    def __init__(self):
        self.created = set()
        self.replaced = set()

    def placeholder(self, cotangent, input_width, dtype, ticket):
        self.created.add(ticket)
        # 0.32.0のcustom_functionは本体も先にvmapするため、NYIのQMMをここへ
        # 置けない。この値は独自vmapが必ず捨てる。漏れた時はNaN検査でも止める。
        return mx.full((*cotangent.shape[:-1], input_width), float('nan'), dtype)

    def complete(self):
        if not self.created or self.created != self.replaced:
            raise RuntimeError('not all mapped derivative placeholders were replaced')


def _dense_projection(linear, x, native, audit):
    """固定2次元量子化重みへの入力微分だけを定義する。biasは定数。"""
    if linear.weight.ndim != 2:
        raise ValueError('coordinate vmap requires a 2D dense weight')

    def multiply(cotangent):
        return mx.quantized_matmul(
            cotangent, linear.weight, scales=linear.scales,
            biases=linear.get('biases'), transpose=False,
            group_size=linear.group_size, bits=linear.bits, mode=linear.mode)

    ticket = object()

    @mx.custom_function
    def backward(cotangent):
        return audit.placeholder(cotangent, x.shape[-1], x.dtype, ticket)

    @backward.vmap
    def mapped_backward(cotangent, axis):
        if axis is None:
            raise ValueError('only mapped cotangents are supported')
        audit.replaced.add(ticket)
        return multiply(mx.moveaxis(cotangent, axis, 0)), 0

    @mx.custom_function
    def projection(value):
        return native(linear, value)

    @projection.vjp
    def input_vjp(primals, cotangent, output):
        return multiply(cotangent) if audit is None else backward(cotangent)

    return projection(x)


def _switch_projection(linear, x, indices, sorted_indices, native, audit):
    """整列済み[N,1,D]だけを扱い、expert indexの並びと共有を維持する。"""
    if (not sorted_indices or x.ndim != 3 or x.shape[1] != 1
            or indices.ndim != 1 or x.shape[0] != indices.shape[0]):
        raise ValueError('coordinate vmap requires sorted SwitchGLU input [N,1,D]')

    def multiply(cotangent):
        return mx.gather_qmm(
            cotangent, linear.weight, linear.scales, linear.get('biases'),
            rhs_indices=indices, transpose=False, group_size=linear.group_size,
            bits=linear.bits, mode=linear.mode, sorted_indices=True)

    ticket = object()

    @mx.custom_function
    def backward(cotangent):
        return audit.placeholder(cotangent, x.shape[-1], x.dtype, ticket)

    @backward.vmap
    def mapped_backward(cotangent, axis):
        if axis is None:
            raise ValueError('only mapped cotangents are supported')
        values = mx.moveaxis(cotangent, axis, 0)
        if values.ndim != 4 or values.shape[2] != 1:
            raise ValueError('expected cotangent directions [C,N,1,D]')
        # C方向を各expert行列に対するM行へする。indexの複製や再整列は不要。
        rows = values[:, :, 0, :].transpose(1, 0, 2)
        result = multiply(rows).transpose(1, 0, 2)[:, :, None, :]
        audit.replaced.add(ticket)
        return result, 0

    @mx.custom_function
    def projection(value):
        return native(linear, value, indices, sorted_indices)

    @projection.vjp
    def input_vjp(primals, cotangent, output):
        return multiply(cotangent) if audit is None else backward(cotangent)

    return projection(x)


def _rms_projection(x, weight, eps, native, audit):
    """方向軸をRMSNormの行として扱い、通常のnative入力VJPを維持する。"""
    ticket = object()

    def multiply(value, cotangent):
        return mx.vjp(lambda v: native(v, weight, eps), [value], [cotangent])[1][0]

    @mx.custom_function
    def backward(cotangent):
        return audit.placeholder(cotangent, x.shape[-1], x.dtype, ticket)

    @backward.vmap
    def mapped_backward(cotangent, axis):
        if axis is None:
            raise ValueError('only mapped RMS cotangents are supported')
        values = mx.moveaxis(cotangent, axis, 0)
        primal = mx.broadcast_to(x, values.shape)
        result = multiply(primal, values)
        audit.replaced.add(ticket)
        return result, 0

    @mx.custom_function
    def projection(value):
        return native(value, weight, eps)

    @projection.vjp
    def input_vjp(primals, cotangent, output):
        return multiply(primals, cotangent) if audit is None else backward(cotangent)

    return projection(x)


@contextmanager
def _input_derivatives(audit=None, native_rms=True):
    """固定重みの入力Jacobian測定中だけ使用する。モデル学習には使用しない。"""
    dense_native = QuantizedLinear.__call__
    switch_native = QuantizedSwitchLinear.__call__
    rms_native = mx.fast.rms_norm

    def dense(linear, x):
        return _dense_projection(linear, x, dense_native, audit)

    def switch(linear, x, indices, sorted_indices=False):
        return _switch_projection(linear, x, indices, sorted_indices, switch_native, audit)

    def rms(x, weight, eps, *, stream=None):
        if stream is not None:
            raise ValueError('coordinate RMS mapping requires the default stream')
        return _rms_projection(x, weight, eps, rms_native, audit)

    QuantizedLinear.__call__ = dense
    QuantizedSwitchLinear.__call__ = switch
    if native_rms:
        mx.fast.rms_norm = rms
    try:
        yield
    finally:
        QuantizedLinear.__call__ = dense_native
        QuantizedSwitchLinear.__call__ = switch_native
        mx.fast.rms_norm = rms_native


def scalar_rows(tape, cotangent):
    """独自の入力VJPを一方向だけ検査する。nativeとの比較用。"""
    if tape.core.training:
        raise ValueError('fixed-weight Jacobian capture requires model.eval()')
    with _input_derivatives():
        return tape.rows(cotangent)


def coordinate_rows(tape, cotangents):
    """[C,1,T,D]方向をまとめ、[C,S,1,D]微分と[C,1,T,D]primalを返す。

    model batchは常に1。固定重み・sorted expert・MLX 0.32.0に限定し、
    専用vmapによる全置換を確認してから配列を返す。呼出側は有限値も検査する。
    """
    if importlib.metadata.version('mlx') != '0.32.0':
        raise ValueError('coordinate mapping has only been checked for MLX 0.32.0')
    if tape.core.training:
        raise ValueError('fixed-weight Jacobian capture requires model.eval()')
    if tape.batch != 1 or cotangents.ndim != 4 or tuple(cotangents.shape[1:]) != tuple(tape.h.shape):
        raise ValueError('expected directions [C,1,T,D] with a batch=1 tape')
    if not 1 <= cotangents.shape[0] <= 16:
        raise ValueError('bounded mapping supports 1 to 16 directions')
    audit = _MappingAudit()

    def rows_and_primal(cotangent):
        outputs, gradients = mx.vjp(tape.forward, [tape.zeros], [cotangent])
        return gradients[0][:, :, 16:-1, :].astype(mx.float32).mean(axis=2), outputs[0]

    with _input_derivatives(audit):
        result = mx.vmap(rows_and_primal)(cotangents)
    audit.complete()
    return result
