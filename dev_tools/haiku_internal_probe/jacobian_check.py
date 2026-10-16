"""公開レンズと実モデルの少数VJP方向を比較する数値診断。

公式の「有効target位置を総和、source位置を平均」という推定量を使う。
一つの既存prompt先頭128tokenだけなので、レンズ再学習や意味精度の検証とはしない。
"""
import json
import time

import mlx.core as mx
import numpy as np
from mlx_lm.models.gemma4_text import DecoderLayer, Router


def check(model, prompt_ids, lens_path, output):
    core = model.language_model.model
    hidden = {}
    native_layer = DecoderLayer.__call__
    def capture(layer, *args, **kwargs):
        result = native_layer(layer, *args, **kwargs)
        hidden[layer.layer_idx] = result[0]
        return result
    DecoderLayer.__call__ = capture
    try:
        logits = model(mx.array([prompt_ids[:128]]))
        mx.eval(logits, list(hidden.values()))
    finally:
        DecoderLayer.__call__ = native_layer
    del logits
    lens = np.load(lens_path)
    native_router = Router.__call__
    def stop_indices(router, x):
        i, w = native_router(router, x)
        return mx.stop_gradient(i), w
    Router.__call__ = stop_indices
    report = {"scope": "single_prompt_random_vjp_diagnostic_not_transfer_validation",
              "calibration_token_ids": prompt_ids[:128], "skip_first": 16,
              "exclude_final_position": True, "directions": 4, "layers": []}
    try:
        mx.random.seed(20261016)
        d = core.config.hidden_size
        c = (mx.random.normal((4, d)) / d**0.5).astype(hidden[29].dtype).astype(mx.float32)
        mx.eval(c)
        for source in [14, 24, 28, 29]:
            started = time.monotonic()
            h = mx.stop_gradient(hidden[source])
            masks = core._make_masks(h, [None]*len(core.layers))
            valid = mx.array([0]*16 + [1]*(h.shape[1]-17) + [0]).astype(h.dtype)
            def suffix(x):
                for i in range(source + 1, len(core.layers)):
                    x, _, _ = core.layers[i](x, masks[i], None)
                return x
            directional_rows = []
            # batch数を変えるとBF16丸め・expert選択も変わり得る。同じbatch=1で微分する。
            for direction in c:
                cotangent = (direction[None, None, :] * valid[None, :, None]).astype(h.dtype)
                y, grad = mx.vjp(suffix, [h], [cotangent])
                y = y[0] if isinstance(y, (tuple, list)) else y
                mx.eval(y, grad)
                if mx.max(mx.abs(y-hidden[29])).item() != 0:
                    raise ValueError("suffix differs from native final hidden state")
                directional_rows.append(grad[0][0,16:-1,:].astype(mx.float32).mean(axis=0))
            observed = mx.stack(directional_rows)
            expected = c if source == 29 else c @ mx.array(lens[f"layer_{source}"])
            mx.eval(y, observed, expected)
            a, b = np.array(observed), np.array(expected)
            cosine = np.sum(a*b, axis=1)/(np.linalg.norm(a,axis=1)*np.linalg.norm(b,axis=1))
            report["layers"].append({"source_layer": source, "seconds": time.monotonic()-started,
                "suffix_vs_native_final_max_abs": mx.max(mx.abs(y[0]-hidden[29][0])).item(),
                "cosine": cosine.tolist(), "observed_norm": np.linalg.norm(a,axis=1).tolist(),
                "relative_l2": (np.linalg.norm(a-b,axis=1)/np.linalg.norm(a,axis=1)).tolist(),
                "identity_cosine": (np.sum(a*np.array(c),axis=1)/(np.linalg.norm(a,axis=1)*np.linalg.norm(np.array(c),axis=1))).tolist(),
                "published_norm": np.linalg.norm(b,axis=1).tolist(), "finite": bool(np.isfinite(a).all())})
            np.savez(output / f"jacobian-directions-layer-{source}.npz", observed=a, published=b, c=np.array(c))
            (output / "jacobian-diagnostic.json").write_text(json.dumps(report,indent=2))
            print("jacobian diagnostic",source,cosine.tolist(),flush=True)
            del y, grad, observed, expected
            mx.clear_cache()
    finally:
        Router.__call__ = native_router
    return report
