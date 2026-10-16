"""固定QATモデルで、独立VJPと方向をまとめたVJPの速度・数値差を保存する。

既存JacobianTapeを共有し、二つの固定入力・分散した32座標だけを測る。
重みと実装のhashを照合し、同一方式の繰返し差も記録する。採用判定・本学習・
サーバーへの接続は行わない。失敗時も途中の測定記録を残す。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import time

import numpy as np

from colab_probe import digest, metrics


def capture_forward(core, ids):
    """30層の残差とexpert集合・重みを、nativeの読取りhookで保存する。"""
    import mlx.core as mx
    from mlx_lm.models.gemma4_text import Router
    from qat_jacobian import native_hidden

    routes = []
    native = Router.__call__

    def call(router, x):
        indices, weights = native(router, x)
        routes.append((indices, weights))
        return indices, weights

    Router.__call__ = call
    try:
        hidden = native_hidden(core, ids)
        mx.eval([value for pair in routes for value in pair])
    finally:
        Router.__call__ = native
    if len(hidden) != 30 or len(routes) != 30:
        raise ValueError('expected exactly 30 hidden states and routers')
    arrays = {f'layer_{i}': np.array(hidden[i].astype(mx.float32)) for i in range(30)}
    for i, (indices, weights) in enumerate(routes):
        indices = np.array(indices).astype(np.int32)
        order = np.argsort(indices, axis=-1)
        arrays[f'experts_{i}'] = np.take_along_axis(indices, order, axis=-1)
        arrays[f'weights_{i}'] = np.take_along_axis(np.array(weights.astype(mx.float32)), order, axis=-1)
    if not all(np.isfinite(value).all() for value in arrays.values()):
        raise ValueError('forward contains non-finite values')
    return arrays


def compare_rows(actual, expected):
    """全体と各source層の差を返す。測定後に許容幅を設定しない。"""
    return {'global': metrics(actual, expected),
            'by_layer': {str(i): metrics(actual[i], expected[i]) for i in range(29)}}


def measure(tape, cotangents, width):
    """一度warmup後、32方向を評価する。GPU待機まで計時し、比較と保存は除く。"""
    import mlx.core as mx
    from qat_vectorized import coordinate_rows

    def evaluate(directions):
        if width == 1:
            primal, gradients = mx.vjp(tape.forward, [tape.zeros], [directions[0]])
            rows = gradients[0][:, :, 16:-1, :].astype(mx.float32).mean(axis=2)[None]
            return rows, primal[0][None]
        return coordinate_rows(tape, directions)

    mx.clear_cache()
    started = time.perf_counter()
    warmup = evaluate(cotangents[:width])
    mx.eval(warmup)
    warmup_seconds = time.perf_counter() - started
    del warmup
    mx.reset_peak_memory()
    measured = []
    started = time.perf_counter()
    for first in range(0, len(cotangents), width):
        values = evaluate(cotangents[first:first + width])
        mx.eval(values)
        measured.append(values)
    seconds = time.perf_counter() - started
    peak = mx.get_peak_memory() / 1024**3
    rows = np.concatenate([np.array(pair[0]) for pair in measured])[:, :, 0, :].transpose(1, 0, 2)
    errors = [float(mx.max(mx.abs(pair[1] - tape.hidden[29])).item()) for pair in measured]
    if not np.isfinite(rows).all() or not np.isfinite(errors).all() or max(errors) != 0:
        raise ValueError('non-finite derivative or VJP primal differs from native')
    return rows, {'seconds_for_32_rows': seconds, 'warmup_seconds': warmup_seconds,
                  'directions_per_call': width, 'peak_mlx_memory_gib': peak,
                  'vjp_primal_native_max_abs': max(errors)}


def run(args):
    import mlx.core as mx
    import mlx_lm
    from mlx_lm import load
    from qat_jacobian import JacobianTape, differentiable_routes
    from qat_vectorized import _input_derivatives

    # 完了・失敗にかかわらず既存runへ追記せず、再測定を別記録にする。
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'status': 'started', 'scope': 'bounded_optimization_measurement_not_lens_fit',
              'model_weights_updated': False, 'platform': platform.platform(),
              'python': platform.python_version(), 'cases': [],
              'timing_scope': 'graph construction and synchronous GPU evaluation after one warmup; excludes comparison and saving'}

    def save():
        (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))

    try:
        spec = json.loads((args.bundle / 'spec.json').read_text())
        cases = json.loads((args.bundle / 'optimization-cases.json').read_text())
        report['spec_sha256'] = digest(args.bundle / 'spec.json')
        report['cases_sha256'] = digest(args.bundle / 'optimization-cases.json')
        report['source_sha256'] = {name: digest(Path(__file__).parent / name) for name in
                                  ['optimization_probe.py', 'qat_vectorized.py', 'qat_jacobian.py', 'colab_probe.py']}
        cuda = hasattr(mx, 'cuda') and mx.cuda.is_available()
        report['backend'] = 'cuda' if cuda else 'metal' if mx.metal.is_available() else 'cpu'
        if (args.require_cuda and not cuda) or report['backend'] == 'cpu':
            raise RuntimeError('required GPU backend unavailable; refusing fallback')
        mx.set_default_device(mx.gpu)
        report['versions'] = {name: importlib.metadata.version(name) for name in spec['versions']}
        if report['versions'] != spec['versions']:
            raise ValueError('runtime versions changed')
        package = Path(mlx_lm.__file__).parent
        for name, expected in spec['implementation_sha256'].items():
            if digest(package / name) != expected:
                raise ValueError(f'model implementation changed: {name}')
        print('Checking fixed model files…', flush=True)
        for name, info in spec['model_files'].items():
            path = args.model / name
            if path.stat().st_size != info['size'] or digest(path) != info['sha256']:
                raise ValueError(f'model file mismatch: {name}')
        report['model_files_verified'] = True
        coordinates = cases['coordinates']
        if (len(cases['cases']) != 2 or len(coordinates) != 32 or len(set(coordinates)) != 32
                or any(type(i) is not int or not 0 <= i < 2816 for i in coordinates)):
            raise ValueError('expected two cases and 32 distinct coordinates within [0,2816)')
        mx.set_cache_limit(512 * 1024**2)
        mx.set_memory_limit(24 * 1024**3)
        started = time.perf_counter()
        model, tokenizer = load(str(args.model))
        model.eval()
        report['load_seconds'] = time.perf_counter() - started
        core = model.language_model.model
        config = core.config
        if (len(core.layers), config.hidden_size, config.num_kv_shared_layers,
                config.hidden_size_per_layer_input) != (30, 2816, 0, 0):
            raise ValueError('unexpected Gemma4 architecture')
        save()
        for index, case in enumerate(cases['cases']):
            if 'text' in case:
                if hashlib.sha256(case['text'].encode()).hexdigest() != case['text_sha256']:
                    raise ValueError('case text hash mismatch')
                ids = tokenizer.encode(case['text'])[:128]
            else:
                ids = case['token_ids']
            if len(ids) != 128 or any(type(i) is not int or i < 0 for i in ids):
                raise ValueError('expected 128 fixed token ids')
            entry = {'id': case['id'], 'token_ids': ids, 'coordinates': coordinates, 'methods': {}}
            report['cases'].append(entry)
            print(f"Case {index + 1}: {case['id']}", flush=True)
            native = capture_forward(core, ids)
            with _input_derivatives():
                custom = capture_forward(core, ids)
            entry['forward_max_abs_by_array'] = {name: metrics(custom[name], value)['max_abs']
                                                 for name, value in native.items()}
            if any(entry['forward_max_abs_by_array'].values()):
                raise ValueError('custom input derivatives changed forward or router values')
            np.savez_compressed(args.output / f'case-{index}-forward.npz', **native)
            del native, custom
            arrays = {}
            with differentiable_routes():
                tape = JacobianTape(core, ids, list(range(29)), batch=1)
                entry['zero_tap_native_max_abs'] = tape.assert_native()
                cotangents = mx.stack([tape.coordinate_cotangent(i) for i in coordinates])
                mx.eval(cotangents)
                # ABBA順で両方式を二回測り、一方だけを実行後半へ偏らせない。
                for label, width in [('native_1', 1), ('vector_1', 16), ('vector_2', 16), ('native_2', 1)]:
                    arrays[label], entry['methods'][label] = measure(tape, cotangents, width)
                    np.save(args.output / f'case-{index}-{label}.npy', arrays[label])
                    save()
                    print(label, json.dumps(entry['methods'][label]), flush=True)
                del tape, cotangents
            entry['comparisons'] = {
                'native_repeat': compare_rows(arrays['native_2'], arrays['native_1']),
                'vector_repeat': compare_rows(arrays['vector_2'], arrays['vector_1']),
                'vector_1_vs_native_1': compare_rows(arrays['vector_1'], arrays['native_1']),
                'vector_2_vs_native_2': compare_rows(arrays['vector_2'], arrays['native_2']),
            }
            seconds = {name: value['seconds_for_32_rows'] for name, value in entry['methods'].items()}
            entry['speedup_from_mean_seconds'] = ((seconds['native_1'] + seconds['native_2']) /
                                                  (seconds['vector_1'] + seconds['vector_2']))
            save()
            del arrays
            mx.clear_cache()
        report['status'] = 'optimization_measurement_complete_review_required'
        save()
        print(json.dumps({'status': report['status'], 'backend': report['backend'],
                          'speedups': [c['speedup_from_mean_seconds'] for c in report['cases']]}, indent=2), flush=True)
    except Exception as exc:
        report.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        save()
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--require-cuda', action='store_true')
    run(parser.parse_args())
