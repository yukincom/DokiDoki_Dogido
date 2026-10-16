"""同じローカル重みの通常計算・層出力・微分を独立プロセスで照合する。

常駐サーバーへ接続しない。入力は既存prompt fixtureのtoken列で、生成はしない。
native DecoderLayerの戻り値を読むだけにし、変更はこのプロセス内で復元する。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import time

import mlx.core as mx
import numpy as np
from mlx_lm import load
from mlx_lm.models.gemma4_text import DecoderLayer, Router


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"scope": "isolated_exact_checkpoint_validation", "generated": False}

    def save():
        (args.output / "validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))

    config = json.loads((args.model / "config.json").read_text())
    text_config = config["text_config"]
    # suffixの独立再計算に必要な状態を見落とさない。別構成は黙って通さない。
    assert config["model_type"] == "gemma4"
    assert text_config["num_kv_shared_layers"] == 0
    assert text_config["hidden_size_per_layer_input"] == 0
    report["model"] = {
        "path": str(args.model.resolve()),
        "config_sha256": hashlib.sha256((args.model / "config.json").read_bytes()).hexdigest(),
        "text_config": text_config,
        "versions": {n: importlib.metadata.version(n) for n in ["mlx", "mlx-lm", "transformers", "numpy"]},
    }
    mx.set_memory_limit(24 * 1024**3)
    mx.set_cache_limit(512 * 1024**2)
    started = time.monotonic()
    model, tokenizer = load(str(args.model))
    model.eval()
    core = model.language_model.model
    report["load_seconds"] = time.monotonic() - started
    print("loaded", report["load_seconds"], flush=True)
    fixtures = json.loads(Path("dogido-rust/src/haiku_prompt/fixtures.json").read_text())
    fixture = next(x for x in fixtures if x["request"]["kind"] == "haiku_irony" and x["name"] != "empty")
    all_ids = tokenizer.apply_chat_template(fixture["messages"], tokenize=True, add_generation_prompt=True,
                                           enable_thinking=False, preserve_thinking=False)
    ids = mx.array([all_ids[:32]])
    report["input"] = {"fixture_name": fixture["name"], "truncated_for_operator_check": True,
                       "token_ids": all_ids[:32], "original_token_count": len(all_ids)}
    native = model(ids)
    mx.eval(native)
    captured = {}
    original = DecoderLayer.__call__

    def capture(layer, *inputs, **kwargs):
        out = original(layer, *inputs, **kwargs)
        captured[layer.layer_idx] = out[0]
        return out

    DecoderLayer.__call__ = capture
    try:
        hooked = model(ids)
        mx.eval(hooked, list(captured.values()))
    finally:
        DecoderLayer.__call__ = original
    final = captured[len(core.layers) - 1]
    decoded = core.embed_tokens.as_linear(core.norm(final))
    softcap = model.language_model.final_logit_softcapping
    if softcap is not None:
        decoded = mx.tanh(decoded / softcap) * softcap
    mx.eval(decoded)
    report["capture"] = {
        "boundary": "decoder_output_after_layer_scalar_before_final_norm",
        "layers": len(captured),
        "native_vs_hook_max_abs": mx.max(mx.abs(native - hooked)).item(),
        "native_vs_final_readout_max_abs": mx.max(mx.abs(native - decoded)).item(),
        "all_finite": all(mx.all(mx.isfinite(v)).item() for v in captured.values()),
        "final_top1_match": mx.mean((mx.argmax(native, -1) == mx.argmax(decoded, -1)).astype(mx.float32)).item(),
    }
    save()
    print("capture", report["capture"], flush=True)
    # 最終層の一つ前から、expert選択を含むnative処理の入力VJPを実測する。
    source_layer = 28
    h = mx.stop_gradient(captured[source_layer])
    masks = core._make_masks(h, [None] * len(core.layers))

    def suffix(x):
        for i in range(source_layer + 1, len(core.layers)):
            x, _, _ = core.layers[i](x, masks[i], None)
        return x

    mx.random.seed(20261016)
    cotangent = mx.random.normal(h.shape).astype(h.dtype)
    vjp_started = time.monotonic()
    try:
        y, grads = mx.vjp(suffix, [h], [cotangent])
        mx.eval(y, grads)
        report["vjp"] = {"ok": True, "source_layer": source_layer,
                         "seconds": time.monotonic() - vjp_started,
                         "finite": mx.all(mx.isfinite(grads[0])).item(),
                         "gradient_norm": mx.linalg.norm(grads[0].astype(mx.float32)).item()}
    except Exception as exc:
        report["vjp"] = {"ok": False, "source_layer": source_layer, "error": str(exc),
                         "seconds": time.monotonic() - vjp_started}
    original_router = Router.__call__

    def fixed_index_router(router, x):
        indices, weights = original_router(router, x)
        return mx.stop_gradient(indices), weights

    Router.__call__ = fixed_index_router
    try:
        routed = model(ids)
        mx.eval(routed)
        report["fixed_index_forward_max_abs"] = mx.max(mx.abs(native - routed)).item()
        vjp_started = time.monotonic()
        y, grads = mx.vjp(suffix, [h], [cotangent])
        mx.eval(y, grads)
        report["fixed_index_vjp"] = {"ok": True, "source_layer": source_layer,
                                     "seconds": time.monotonic() - vjp_started,
                                     "finite": mx.all(mx.isfinite(grads[0])).item(),
                                     "gradient_norm": mx.linalg.norm(grads[0].astype(mx.float32)).item()}
    except Exception as exc:
        report["fixed_index_vjp"] = {"ok": False, "error": str(exc)}
    finally:
        Router.__call__ = original_router
    report["peak_memory_gb"] = mx.get_peak_memory() / 1024**3
    np.savez(args.output / "operator_hidden_states.npz", **{
        f"layer_{i}": np.array(v.astype(mx.float32)) for i, v in captured.items()})
    save()
    print("vjp", report["vjp"], "peak GiB", report["peak_memory_gb"], flush=True)


if __name__ == "__main__":
    main()
