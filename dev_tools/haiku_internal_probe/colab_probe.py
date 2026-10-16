"""固定QAT重みのMac/CUDA比較を、サーバーと独立して実行する。

入力は保存済みtoken列。32tokenの全層残差・末尾logits・expert選択と、
128tokenのJacobian先頭32座標を保存する。本学習と重み更新は行わない。
比較値は実行環境の互換性を調べる材料であり、レンズの精度認定ではない。
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


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(8 * 1024**2), b''):
            h.update(block)
    return h.hexdigest()


def metrics(actual, expected):
    """同じ座標の差を返す。丸め差の許容幅を後から作ってPASSにはしない。"""
    a, b = np.asarray(actual, dtype=np.float64), np.asarray(expected, dtype=np.float64)
    if a.shape != b.shape:
        raise ValueError(f'shape mismatch: {a.shape} != {b.shape}')
    if not np.isfinite(a).all() or not np.isfinite(b).all():
        raise ValueError('non-finite measurement')
    a, b = a.ravel(), b.ravel()
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return {'max_abs': float(np.max(np.abs(a-b))),
            'relative_l2': float(np.linalg.norm(a-b) / max(nb, 1e-30)),
            'cosine': float(np.dot(a, b) / max(na * nb, 1e-30))}


def compare(output, reference):
    with np.load(output / 'forward.npz', allow_pickle=False) as actual, \
         np.load(reference / 'forward.npz', allow_pickle=False) as expected:
        hidden = {str(i): metrics(actual[f'layer_{i}'], expected[f'layer_{i}'])
                  for i in range(30)}
        logits = metrics(actual['last_logits'], expected['last_logits'])
        # argpartitionの並び順は意味を持たないので、同じ8 expertの集合を比べる。
        routes = {str(i): float(np.mean(np.all(
            actual[f'experts_{i}'] == expected[f'experts_{i}'], axis=-1)))
                  for i in range(30)}
        router_weights = {str(i): metrics(actual[f'expert_weights_{i}'],
                                          expected[f'expert_weights_{i}'])
                          for i in range(30)}
        a, b = actual['last_logits'].ravel(), expected['last_logits'].ravel()
        top_a = np.argsort(a)[-20:][::-1]
        top_b = np.argsort(b)[-20:][::-1]
        logits.update({'top1_same': bool(top_a[0] == top_b[0]),
                       'top20_overlap': len(set(top_a) & set(top_b)) / 20,
                       'actual_top20_ids': top_a.tolist(),
                       'mac_top20_ids': top_b.tolist()})
    a = np.load(output / 'jacobian_rows.npy', allow_pickle=False)
    b = np.load(reference / 'jacobian_rows.npy', allow_pickle=False)
    return {'hidden_by_layer': hidden, 'last_token_logits': logits,
            'expert_set_match_fraction_by_layer': routes,
            'expert_weights_sorted_by_id': router_weights,
            'jacobian_by_layer': {str(i): metrics(a[i], b[i]) for i in range(29)},
            'judgement': 'review_required_no_automatic_training'}


def run(args):
    import mlx.core as mx
    import mlx_lm
    from mlx_lm import load
    from mlx_lm.models.gemma4_text import DecoderLayer, Router
    from qat_jacobian import JacobianTape, differentiable_routes

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    if (output / 'report.json').exists():
        raise ValueError('output already contains a run; choose a new output directory')
    spec = json.loads((args.bundle / 'spec.json').read_text())
    report = {'scope': 'compatibility_and_speed_only_not_lens_training',
              'status': 'started', 'model_weights_updated': False,
              'platform': platform.platform(), 'python': platform.python_version(),
              'spec_sha256': digest(args.bundle / 'spec.json')}

    def save():
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))

    try:
        cuda = hasattr(mx, 'cuda') and mx.cuda.is_available()
        report['backend'] = 'cuda' if cuda else 'metal' if mx.metal.is_available() else 'cpu'
        if args.require_cuda and not cuda:
            raise RuntimeError('CUDA backend is unavailable; refusing CPU fallback')
        if report['backend'] == 'cpu':
            raise RuntimeError('this bounded GPU check does not support CPU execution')
        mx.set_default_device(mx.gpu)
        report['versions'] = {name: importlib.metadata.version(name) for name in spec['versions']}
        if report['versions'] != spec['versions']:
            raise ValueError(f"runtime versions changed: {report['versions']}")
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
        mx.set_cache_limit(512 * 1024**2)
        mx.set_memory_limit(24 * 1024**3)
        started = time.monotonic()
        model, _ = load(str(args.model))
        model.eval()
        report['load_seconds'] = time.monotonic() - started
        core = model.language_model.model
        config = core.config
        if (len(core.layers), config.hidden_size, config.num_kv_shared_layers,
                config.hidden_size_per_layer_input) != (30, 2816, 0, 0):
            raise ValueError('unexpected Gemma4 architecture')
        ids = mx.array([spec['forward_token_ids']])
        native = model(ids)
        mx.eval(native)
        saved, active = {}, [None]
        layer_call, router_call = DecoderLayer.__call__, Router.__call__

        def capture_layer(layer, *inputs, **kwargs):
            active[0] = layer.layer_idx
            result = layer_call(layer, *inputs, **kwargs)
            saved[f'layer_{layer.layer_idx}'] = result[0]
            active[0] = None
            return result

        def capture_router(router, x):
            indices, weights = router_call(router, x)
            saved[f'experts_{active[0]}'] = indices
            saved[f'expert_weights_{active[0]}'] = weights
            return indices, weights

        DecoderLayer.__call__, Router.__call__ = capture_layer, capture_router
        try:
            hooked = model(ids)
            mx.eval(hooked, list(saved.values()))
        finally:
            DecoderLayer.__call__, Router.__call__ = layer_call, router_call
        hook_error = float(mx.max(mx.abs(hooked - native)).item())
        report['native_vs_hook_max_abs'] = hook_error
        if hook_error != 0:
            raise ValueError(f'capture changes forward values: {hook_error}')
        arrays = {key: np.array(value.astype(mx.float32)) for key, value in saved.items()}
        for i in range(30):
            indices = arrays[f'experts_{i}'].astype(np.int32)
            order = np.argsort(indices, axis=-1)
            arrays[f'experts_{i}'] = np.take_along_axis(indices, order, axis=-1)
            arrays[f'expert_weights_{i}'] = np.take_along_axis(arrays[f'expert_weights_{i}'], order, axis=-1)
        arrays['last_logits'] = np.array(native[:, -1, :].astype(mx.float32))
        if not all(np.isfinite(value).all() for value in arrays.values()):
            raise ValueError('forward contains non-finite values')
        np.savez_compressed(output / 'forward.npz', **arrays)
        del saved, native, hooked, arrays
        mx.clear_cache()
        save()
        print('Forward capture complete. Measuring 32 Jacobian coordinates…', flush=True)
        with differentiable_routes():
            tape = JacobianTape(core, spec['jacobian_token_ids'], list(range(29)), batch=1)
            report['zero_tap_native_max_abs'] = tape.assert_native()
            started = time.monotonic()
            warmup = tape.rows(tape.coordinate_cotangent(0))
            mx.eval(warmup)
            report['vjp_cold_seconds'] = time.monotonic() - started
            started = time.monotonic()
            rows = []
            for coordinate in range(32):
                result = tape.rows(tape.coordinate_cotangent(coordinate))
                mx.eval(result)
                rows.append(np.array(result))
            elapsed = time.monotonic() - started
        values = np.concatenate(rows, axis=1)
        if not np.isfinite(values).all():
            raise ValueError('Jacobian contains non-finite values')
        np.save(output / 'jacobian_rows.npy', values)
        report['seconds_for_32_rows'] = elapsed
        report['estimated_hours_for_100_prompts'] = elapsed / 32 * 2816 * 100 / 3600
        report['estimate_scope'] = 'one 128-token prefix; linear extrapolation; not a runtime guarantee'
        report['peak_mlx_memory_gib'] = mx.get_peak_memory() / 1024**3
        if args.reference:
            report['comparison_with_mac'] = compare(output, args.reference)
        report['status'] = 'measurement_complete_review_required'
        save()
        print(json.dumps({k: report[k] for k in ['status', 'backend', 'seconds_for_32_rows',
                                                'estimated_hours_for_100_prompts', 'peak_mlx_memory_gib']},
                         ensure_ascii=False, indent=2), flush=True)
    except Exception as exc:
        report['status'] = 'failed'
        report['error'] = f'{type(exc).__name__}: {exc}'
        save()
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--reference', type=Path)
    parser.add_argument('--require-cuda', action='store_true')
    run(parser.parse_args())
