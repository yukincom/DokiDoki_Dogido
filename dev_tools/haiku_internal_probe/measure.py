"""既存発句経路の独立再実行と、生成時の全層活性・MoE選択・J-lens読出し。

Startと本体が組み立てた要求だけを使う。HTTP・音声・保存APIは呼ばない。
量子化版への移植レンズであることを全結果へ記録し、感情ラベルへ変換しない。
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import importlib.metadata
import inspect
import json
from pathlib import Path
import re
import subprocess
import time

import mlx.core as mx
import numpy as np
from mlx_lm import load
from mlx_lm.models import gemma4
from mlx_lm.models.cache import make_prompt_cache
from mlx_lm.models.gemma4_text import DecoderLayer, Router
from mlx_lm.sample_utils import make_sampler

from native_decoder_capture import observe_decoder_outputs


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(8*1024**2), b""):
            h.update(block)
    return h.hexdigest()


def model_identity(model_path):
    return {"resolved_model_path": str(model_path.resolve()),
            "files": {p.name: {"sha256": digest(p), "size": p.stat().st_size}
                      for p in sorted(model_path.iterdir())
                      if p.name.endswith(".safetensors") or p.name in
                      {"config.json", "tokenizer.json", "tokenizer_config.json",
                       "chat_template.jinja", "generation_config.json", "tokenizer.model",
                       "special_tokens_map.json", "added_tokens.json"}}}


def implementation_identity():
    """実際に使うnative計算・ロード・採取器の実装も再開時に照合する。"""
    sources = {Path(inspect.getsourcefile(obj)) for obj in
               [gemma4, DecoderLayer, Router, make_sampler, make_prompt_cache, load]}
    sources.update({Path(__file__), Path(__file__).with_name("jacobian_check.py"),
                    Path(inspect.getsourcefile(observe_decoder_outputs))})
    return {str(p): digest(p) for p in sorted(sources)}


def validate_binding(binding, identity):
    """同形の別重みも拒否する。新しいモデルへの適合確認を形状比較で代用しない。"""
    if (binding["model_files"] != identity["model"]["files"]
            or binding["lens_npz_sha256"] != identity["lens_npz_sha256"]
            or binding["target_layer"] != identity["lens_target_layer"]
            or binding["target_layer"] != 29
            or binding["runtime"] != identity["runtime"]
            or binding["implementation_sha256"] != identity["implementation_sha256"]):
        raise ValueError("checkpoint/lens binding mismatch; model changes need a new reviewed binding and validation")


class Capture:
    """nativeの戻り値を観測する。モデル計算もexpert選択も変更しない。"""
    def __init__(self, core):
        self.core = core
        self.h = {}
        self.routes = {}
        self.router_layers = {id(layer.router): i for i, layer in enumerate(core.layers)}

    @contextmanager
    def active(self):
        original_router = Router.__call__
        def layer_output(index, result):
            self.h[index] = result[0][0, -1, :]
        def router_call(router, x):
            indices, weights = original_router(router, x)
            # 8位と9位の境界の余裕も記録。順位番号に意味ラベルは付けない。
            normed = mx.fast.rms_norm(x[:, -1:, :], router.scale * router._root_size, router.eps)
            scores = router.proj(normed)[0, 0, :]
            ordered = mx.sort(scores)
            k = router.config.top_k_experts
            self.routes[self.router_layers[id(router)]] = (
                indices[0, -1], weights[0, -1], ordered[-k] - ordered[-k-1])
            return indices, weights
        Router.__call__ = router_call
        try:
            with observe_decoder_outputs(self.core, DecoderLayer, layer_output):
                yield
        finally:
            Router.__call__ = original_router

    def arrays(self):
        hidden = mx.stack([self.h[i].astype(mx.float32) for i in range(len(self.h))])
        idx = mx.stack([self.routes[i][0] for i in range(len(self.h))])
        weights = mx.stack([self.routes[i][1].astype(mx.float32) for i in range(len(self.h))])
        margin = mx.stack([self.routes[i][2].astype(mx.float32) for i in range(len(self.h))])
        mx.eval(hidden, idx, weights, margin)
        return tuple(np.array(v) for v in (hidden, idx, weights, margin))


def decode(core, residual, softcap):
    logits = core.embed_tokens.as_linear(core.norm(residual))
    return mx.tanh(logits / softcap) * softcap if softcap else logits


def top_tokens(logits, tokenizer, count=16):
    values = np.array(logits.astype(mx.float32)).reshape(-1)
    indices = np.argsort(values)[-count:][::-1]
    return [{"id": int(i), "token": tokenizer.decode([int(i)]), "score": float(values[i])}
            for i in indices]


def generate(model, tokenizer, core, request, output, stage, seed):
    saved = output / f"{stage}.json"
    if saved.exists():
        metadata = json.loads(saved.read_text())
        if metadata["request"] != request or metadata["seed"] != seed:
            raise ValueError("saved generation request/seed mismatch")
        expected_ids = tokenizer.apply_chat_template(request["messages"], tokenize=True,
            add_generation_prompt=True, enable_thinking=False, preserve_thinking=False)
        if metadata["prompt_token_ids"] != expected_ids:
            raise ValueError("saved generation tokenizer/template mismatch")
        if stage != "scene":
            a = np.load(output / f"{stage}-activations.npz")
            if a["hidden"].shape[1:] != (len(core.layers), core.config.hidden_size):
                raise ValueError("saved activation shape mismatch")
        return metadata, parse_native(metadata)
    messages = request["messages"]
    config = request["request"]
    ids = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
                                        enable_thinking=False, preserve_thinking=False)
    cache = make_prompt_cache(model)
    sampler = make_sampler(temp=config["temperature"])
    mx.random.seed(seed)
    capture = Capture(core)
    generated, hidden, indices, weights, margins, logits_top = [], [], [], [], [], []
    started = time.monotonic()
    limit = config["max_tokens"] or 192
    next_input = mx.array([ids])
    with capture.active():
        for i in range(limit):
            raw = model(next_input, cache=cache)[:, -1, :]
            mx.eval(raw)
            sample = sampler(raw - mx.logsumexp(raw, axis=-1, keepdims=True))
            token = int(sample.item())
            if stage != "scene":
                h, idx, w, margin = capture.arrays()
                hidden.append(h); indices.append(idx); weights.append(w); margins.append(margin)
                logits_top.append(top_tokens(raw, tokenizer, 5))
            if token in tokenizer.eos_token_ids:
                break
            generated.append(token)
            next_input = mx.array([[token]])
            if (i + 1) % 40 == 0:
                print(stage, i + 1, tokenizer.decode(generated)[-100:], flush=True)
    text = tokenizer.decode(generated)
    metadata = {"stage": stage, "prompt_token_ids": ids, "output_token_ids": generated,
                "output": text, "request": request, "seed": seed, "elapsed_seconds": time.monotonic() - started,
                "finish_reason": "stop" if token in tokenizer.eos_token_ids else "length",
                "positions": "hidden[i] is after prompt + output[:i], predicting output[i]",
                "predicted_token_ids": generated + ([token] if token in tokenizer.eos_token_ids else []),
                "activation_dtype": str(capture.h[0].dtype),
                "generation_script_sha256": digest(Path(__file__)),
                "native_top_tokens": logits_top}
    write_json(output / f"{stage}.json", metadata)
    if stage != "scene":
        np.savez(output / f"{stage}-activations.npz", hidden=np.array(hidden),
                 expert_indices=np.array(indices), expert_weights=np.array(weights),
                 routing_margin=np.array(margins))
    del cache
    mx.clear_cache()
    print(stage, text, flush=True)
    return metadata, parse_native(metadata)


def parse_native(metadata):
    value = {"parse": {"request": metadata["request"]["request"], "generated": {
        "text": metadata["output"], "finish_reason": metadata["finish_reason"],
        "completion_tokens": len(metadata["output_token_ids"]),
        "prompt_tokens": len(metadata["prompt_token_ids"])}}}
    result = subprocess.run(["dogido-rust/target/debug/examples/haiku_probe_requests"],
                            input=json.dumps(value), text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def bridge(start, irony=None, scene=None):
    value = {"start": start, "irony": irony, "scene": scene}
    result = subprocess.run(["dogido-rust/target/debug/examples/haiku_probe_requests"],
                            input=json.dumps(value), text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def position_for_char(tokenizer, tokens, char_index):
    # 文頭文字を含むtokenの直前を選ぶ。JSON区切りと同一tokenなら、その混在を記録する。
    for i in range(len(tokens)):
        if len(tokenizer.decode(tokens[:i+1]).rstrip("�")) > char_index:
            return i
    raise ValueError("character not covered by output tokens")


def readouts(model, tokenizer, metadata, output, lens_path):
    core = model.language_model.model
    softcap = model.language_model.final_logit_softcapping
    lens = np.load(lens_path)
    rows = []
    for stage in ["irony", "draft"]:
        m = metadata[stage]
        a = np.load(output / f"{stage}-activations.npz")
        tokens, text = m["output_token_ids"], m["output"]
        positions = [("response_onset", 0)]
        if stage == "irony":
            match = re.search(r'"description"\s*:\s*"', text)
            if match:
                end = text.find('"', match.end())
                for name, char in [("description_onset", match.end()),
                                   ("description_middle", (match.end()+end)//2),
                                   ("description_end", max(match.end(), end-1))]:
                    positions.append((name, position_for_char(tokenizer, tokens, char)))
        else:
            match = re.search(r'"(?:lines|text)"\s*:\s*(?:\[\s*)?"', text)
            if match:
                positions.append(("poem_onset", position_for_char(tokenizer, tokens, match.end())))
        # hookを付けずに同じcache手順・同じ生成済みtoken列を再計算する。
        # full-prefixとの違いだけでは、BF16の行列形状による差と誤対応を分離できないため。
        cached_baseline = {}
        wanted = {p for _, p in positions}
        cache = make_prompt_cache(model)
        for p in range(max(wanted)+1):
            x = m["prompt_token_ids"] if p == 0 else [tokens[p-1]]
            baseline = model(mx.array([x]), cache=cache)[0,-1]
            mx.eval(baseline)
            if p in wanted:
                stored = m["native_top_tokens"][p]
                error = max(abs(float(baseline[v["id"]].item())-v["score"]) for v in stored)
                cached_baseline[p] = {"stored_top5_max_abs": error,
                    "baseline_argmax_id": int(mx.argmax(baseline).item()),
                    "stored_first_id": stored[0]["id"],
                    "stored_first_tied_for_max": float(mx.max(baseline).item()) == stored[0]["score"]}
                if error != 0 or not cached_baseline[p]["stored_first_tied_for_max"]:
                    raise ValueError(f"unhooked cached replay differs at {stage}:{p}: {cached_baseline[p]}")
        del cache
        for name, pos in positions:
            prefix = m["prompt_token_ids"] + tokens[:pos]
            # 別forwardでキャッシュによる採取位置の誤対応も検査する。
            direct = model(mx.array([prefix]))[0, -1]
            final_h = mx.array(a["hidden"][pos, -1]).astype(core.norm.weight.dtype)
            reconstructed = decode(core, final_h, softcap)
            mx.eval(direct, reconstructed)
            check = {"max_abs": mx.max(mx.abs(direct-reconstructed)).item(),
                     "direct_top1": int(mx.argmax(direct).item()),
                     "cached_top1": int(mx.argmax(reconstructed).item())}
            item = {"stage": stage, "position": name, "generated_token_index": pos,
                    "prefix_text": tokenizer.decode(tokens[:pos]), "next_token": tokenizer.decode(tokens[pos:pos+1]),
                    "cache_validation": check, "layers": []}
            item["same_cache_replay"] = cached_baseline[pos]
            for layer in range(30):
                h = mx.array(a["hidden"][pos, layer])
                raw = decode(core, h.astype(core.norm.weight.dtype), softcap)
                entry = {"layer_zero_based": layer, "raw_logit_lens": top_tokens(raw, tokenizer),
                         "expert_indices": a["expert_indices"][pos, layer].tolist(),
                         "expert_weights": a["expert_weights"][pos, layer].tolist(),
                         "routing_margin": float(a["routing_margin"][pos, layer])}
                if layer < 29:
                    transported = h @ mx.array(lens[f"layer_{layer}"]).T
                    projected = decode(core, transported.astype(core.norm.weight.dtype), softcap)
                    entry["transferred_j_lens"] = top_tokens(projected, tokenizer)
                item["layers"].append(entry)
            rows.append(item)
            write_json(output / "readouts.json", rows)
            print("readout", stage, name, check, flush=True)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=101)
    parser.add_argument("--fixture-index", type=int)
    args = parser.parse_args()
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    reference = output.parent / "reference"
    start_file = output.parent / "start.json"
    start_data = json.loads(start_file.read_text())
    if args.fixture_index is not None:
        fixture_lines = Path("dogido-rust/fixtures/haiku-preparation.jsonl").read_text().splitlines()
        start_data = {"start": json.loads(fixture_lines[args.fixture_index])["start"],
                      "source_fixture_index": args.fixture_index}
    config = json.loads((args.model / "config.json").read_text())
    assert config["text_config"]["hidden_size"] == 2816
    assert config["model_type"] == "gemma4"
    assert config["text_config"]["enable_moe_block"] is True
    assert config["text_config"]["tie_word_embeddings"] is True
    assert config["text_config"]["num_hidden_layers"] == 30
    assert config["text_config"]["num_kv_shared_layers"] == 0
    assert config["text_config"]["hidden_size_per_layer_input"] == 0
    identity_path = output / "identity.json"
    identity = {"model": model_identity(args.model), "lens_npz_sha256": digest(reference / "lens.npz"),
                "lens_target_layer": 29, "boundary": "decoder_output_after_layer_scalar_before_final_norm",
                "implementation_sha256": implementation_identity(),
                "runtime": {n: importlib.metadata.version(n) for n in ["mlx", "mlx-lm", "transformers"]}}
    binding = json.loads((reference / "checkpoint-binding.json").read_text())
    validate_binding(binding, identity)
    if identity_path.exists() and json.loads(identity_path.read_text()) != identity:
        raise ValueError("model/weights/tokenizer/lens/runtime identity changed; use a new run directory")
    if not identity_path.exists() and any((output / name).exists() for name in
            ["manifest.json", "irony.json", "scene.json", "draft.json"]):
        raise ValueError("saved results without current identity cannot be adopted; use a new run directory")
    write_json(identity_path, identity)
    start_path = output / "start.json"
    if start_path.exists() and json.loads(start_path.read_text()) != start_data:
        raise ValueError("saved input fixture changed; use a new run directory")
    if (output / "manifest.json").exists():
        prior = json.loads((output / "manifest.json").read_text())
        if prior["source_fixture_index"] != start_data["source_fixture_index"]:
            raise ValueError("saved fixture index mismatch")
    write_json(start_path, start_data)
    manifest = {"status": "running", "scope": "isolated_replay_of_existing_synthetic_fixture",
                "model_path": str(args.model), "model_revision": args.model.name,
                "model_config_sha256": digest(args.model / "config.json"),
                "tokenizer_sha256": digest(args.model / "tokenizer.json"),
                "tokenizer_config_sha256": digest(args.model / "tokenizer_config.json"),
                "lens": json.loads((reference / "lens-metadata.json").read_text()),
                "interpretation_status": "exploratory_transfer_not_validated_emotion_measurement",
                "source_fixture_index": start_data["source_fixture_index"],
                "implementation_sha256": identity["implementation_sha256"],
                "versions": {n: importlib.metadata.version(n) for n in ["mlx", "mlx-lm", "transformers", "numpy"]},
                "source_files": {str(p): digest(p) for p in [Path(__file__),
                    Path("dogido-rust/examples/haiku_probe_requests.rs"),
                    Path("dogido-rust/src/haiku_prompt/templates.json"),
                    Path("dogido_server/llm/companion_prompts.json")]}}
    if (output / "manifest.json").exists():
        manifest = json.loads((output / "manifest.json").read_text())
        manifest.setdefault("analysis_runs", []).append({"script_sha256": digest(Path(__file__)),
            "identity_sha256": digest(identity_path)})
        manifest["status"] = "running_reanalysis"
    write_json(output / "manifest.json", manifest)
    mx.set_memory_limit(24*1024**3)
    mx.set_cache_limit(512*1024**2)
    model, tokenizer = load(str(args.model))
    model.eval()
    core = model.language_model.model
    start = start_data["start"]
    metadata = {}
    request = bridge(start)
    metadata["irony"], irony = generate(model, tokenizer, core, request["next"], output, "irony", args.seed)
    request = bridge(start, irony)
    write_json(output / "inspiration.json", request)
    metadata["scene"], scene = generate(model, tokenizer, core, request["next"], output, "scene", args.seed)
    request = bridge(start, irony, scene)
    write_json(output / "materials.json", request)
    metadata["draft"], draft = generate(model, tokenizer, core, request["next"], output, "draft", args.seed)
    readouts(model, tokenizer, metadata, output, reference / "lens.npz")
    from jacobian_check import check
    check(model, metadata["irony"]["prompt_token_ids"], reference / "lens.npz", output)
    manifest.update(status="complete_exploratory_transfer", peak_memory_gb=mx.get_peak_memory()/1024**3,
                    poem_status="raw_draft_not_domain_validated_or_saved")
    write_json(output / "manifest.json", manifest)


if __name__ == "__main__":
    main()
