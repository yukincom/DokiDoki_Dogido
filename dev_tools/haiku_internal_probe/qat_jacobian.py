"""同じQAT重みのnative計算から、全座標のJacobianを採る共通処理。

層出力へのゼロ摂動を独立入力とし、target全位置の総和を各source位置で平均する。
モデル重みは更新しない。サーバーには接続せず、MoEの離散indexだけ微分対象から外す。
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import time

import mlx.core as mx
import numpy as np
from mlx_lm import load
from mlx_lm.models.gemma4_text import DecoderLayer, Router


@contextmanager
def differentiable_routes():
    original = Router.__call__

    def call(router, x):
        indices, weights = original(router, x)
        return mx.stop_gradient(indices), weights

    Router.__call__ = call
    try:
        yield
    finally:
        Router.__call__ = original


def native_hidden(core, ids, batch=1):
    """nativeの層出力を採る。final norm前を保持し、計算の入出力は変えない。"""
    hidden = {}
    original = DecoderLayer.__call__

    def call(layer, *args, **kwargs):
        result = original(layer, *args, **kwargs)
        hidden[layer.layer_idx] = result[0]
        return result

    DecoderLayer.__call__ = call
    try:
        normalized = core(mx.array([ids] * batch))
        mx.eval(normalized, list(hidden.values()))
    finally:
        DecoderLayer.__call__ = original
    return hidden


class JacobianTape:
    """同一promptの各層への独立摂動を、一度の逆伝播で同時に測定する。"""

    def __init__(self, core, ids, sources, batch=1):
        self.core = core
        self.sources = list(sources)
        self.batch = batch
        self.hidden = native_hidden(core, ids, batch)
        self.target = len(core.layers) - 1
        self.first = min(sources)
        self.h = mx.stop_gradient(self.hidden[self.first])
        self.masks = core._make_masks(self.h, [None] * len(core.layers))
        self.zeros = mx.zeros((len(sources), *self.h.shape), dtype=self.h.dtype)
        self.mask = mx.array([0] * 16 + [1] * (len(ids)-17) + [0]).astype(self.h.dtype)
        self.source_index = {layer: i for i, layer in enumerate(sources)}
        mx.eval(self.zeros, self.mask)

    def forward(self, deltas):
        x = self.h
        for layer in range(self.first, self.target + 1):
            if layer != self.first:
                x, _, _ = self.core.layers[layer](x, self.masks[layer], None)
            if layer in self.source_index:
                x = x + deltas[self.source_index[layer]]
        return x

    def rows(self, cotangent):
        _, grads = mx.vjp(self.forward, [self.zeros], [cotangent])
        return grads[0][:, :, 16:-1, :].astype(mx.float32).mean(axis=2)

    def coordinate_cotangent(self, first_dim):
        d = self.h.shape[-1]
        dimensions = mx.arange(self.batch) + first_dim
        vectors = (dimensions[:, None] == mx.arange(d)[None, :]).astype(self.h.dtype)
        return vectors[:, None, :] * self.mask[None, :, None]

    def assert_native(self):
        y = self.forward(self.zeros)
        mx.eval(y)
        difference = mx.max(mx.abs(y-self.hidden[self.target])).item()
        if difference != 0:
            raise ValueError(f"zero-tap forward differs from native: {difference}")
        return difference


def benchmark(model_path, output):
    output.mkdir(parents=True, exist_ok=True)
    mx.set_memory_limit(24 * 1024**3)
    mx.set_cache_limit(512 * 1024**2)
    model, _ = load(str(model_path))
    model.eval()
    core = model.language_model.model
    config = core.config
    if config.num_kv_shared_layers or config.hidden_size_per_layer_input:
        raise ValueError("suffix requires residual-only Gemma4 without PLE/KV sharing")
    prior = json.loads(Path('.dogido_tools/experiments/2026-10-16-haiku-jspace/run-101/irony.json').read_text())
    ids = prior['prompt_token_ids'][:128]
    report = {"scope": "operator_and_speed_check_not_fit", "prompt_token_ids": ids, "batches": []}
    baseline_rows = None
    with differentiable_routes():
        for batch in [1, 4, 8]:
            tape = JacobianTape(core, ids, list(range(29)), batch)
            zero_error = tape.assert_native()
            started = time.monotonic()
            rows = tape.rows(tape.coordinate_cotangent(0))
            mx.eval(rows)
            cold_seconds = time.monotonic()-started
            first_rows = np.array(rows)
            started = time.monotonic()
            block = []
            for first in range(0, 32, batch):
                result = tape.rows(tape.coordinate_cotangent(first))
                mx.eval(result)
                block.append(np.array(result))
            seconds = time.monotonic()-started
            values = np.concatenate(block, axis=1)
            np.save(output / f"batch-{batch}-rows.npy", values)
            entry = {"batch": batch, "zero_tap_native_max_abs": zero_error,
                     "cold_seconds": cold_seconds, "seconds_for_32_rows": seconds,
                     "estimated_seconds_per_prompt": seconds/32*config.hidden_size,
                     "peak_memory_gib": mx.get_peak_memory()/1024**3,
                     "finite": bool(np.isfinite(values).all())}
            if batch == 1:
                baseline_rows = values
                # 同じ1方向を、既存の層ごとのsuffixと独立に突合する。
                comparisons = []
                for source in [14, 24, 28]:
                    h = mx.stop_gradient(tape.hidden[source])
                    def suffix(x):
                        for i in range(source+1, 30):
                            x, _, _ = core.layers[i](x, tape.masks[i], None)
                        return x
                    _, grad = mx.vjp(suffix, [h], [tape.coordinate_cotangent(0)])
                    row = grad[0][:,16:-1,:].astype(mx.float32).mean(axis=1)
                    mx.eval(row)
                    a, b = np.array(row)[0], values[source,0]
                    err = float(np.max(np.abs(a-b)))
                    relative = float(np.linalg.norm(a-b)/np.linalg.norm(a))
                    cosine = float(np.dot(a,b)/(np.linalg.norm(a)*np.linalg.norm(b)))
                    # ゼロ加算でforwardは一致しても、BF16の逆伝播は丸め順が変わる。
                    # 差を記録し、許容幅を越えた場合は学習へ進めない。
                    comparisons.append({"source": source, "max_abs": err,
                                        "relative_l2": relative, "cosine": cosine})
                    print('suffix comparison', comparisons[-1], flush=True)
                    if relative > 0.03 or cosine < 0.995:
                        raise ValueError(f"multi-source/suffix VJP differs beyond BF16 gate: {comparisons[-1]}")
                entry['individual_suffix_comparison'] = comparisons
            else:
                a, b = values.reshape(-1,config.hidden_size), baseline_rows.reshape(-1,config.hidden_size)
                entry['vs_batch1'] = {
                    'median_cosine': float(np.median(np.sum(a*b,axis=1)/(np.linalg.norm(a,axis=1)*np.linalg.norm(b,axis=1)))),
                    'relative_l2': float(np.linalg.norm(a-b)/np.linalg.norm(b)),
                    'max_abs': float(np.max(np.abs(a-b)))}
            report['batches'].append(entry)
            (output / 'benchmark.json').write_text(json.dumps(report,indent=2))
            print(json.dumps(entry), flush=True)
            if batch == 1:
                compiled_rows = mx.compile(tape.rows)
                started = time.monotonic()
                compiled = compiled_rows(tape.coordinate_cotangent(0))
                mx.eval(compiled)
                compile_seconds = time.monotonic()-started
                error = float(np.linalg.norm(np.array(compiled)-values[:, :1])/np.linalg.norm(values[:, :1]))
                started = time.monotonic()
                for first in range(32):
                    result = compiled_rows(tape.coordinate_cotangent(first))
                    mx.eval(result)
                compiled_seconds = time.monotonic()-started
                entry['compiled'] = {'cold_seconds': compile_seconds,
                    'seconds_for_32_rows': compiled_seconds, 'vs_uncompiled_relative_l2': error,
                    'estimated_seconds_per_prompt': compiled_seconds/32*config.hidden_size}
                print('compiled', json.dumps(entry['compiled']), flush=True)
                (output / 'benchmark.json').write_text(json.dumps(report,indent=2))
                del compiled_rows, compiled
            del tape, rows, result
            mx.clear_cache()
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    benchmark(args.model, args.output)
