#!/usr/bin/env python3
"""保存済みメッセージで短い実生成を行い、数値・token位置・採取有無を照合する。

起動済みの検証サーバーを使う。サービスの起動・停止と本体の記憶保存は行わない。
"""
from __future__ import annotations

import argparse
import array
import hashlib
import json
import math
from pathlib import Path
import urllib.request


def request(base_url: str, body: dict) -> dict:
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions",
                                 json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as response:
        return json.load(response)


def inspect(root: Path, response: dict) -> dict:
    folder = root / response["id"]
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["response_id"] == response["id"]
    assert manifest["schema"] == "dogido.internal_capture.v1"
    assert manifest["status"] in {"complete", "partial"}, manifest
    assert manifest["boundary"] == "decoder_layer_output_pre_final_norm"
    n, layers, width = manifest["shape"]
    assert (layers, width) == (30, 2816)
    assert n == len(manifest["positions"]) == len(manifest["predicted_token_ids"])
    assert n > 0
    payload = (folder / "hidden.f32").read_bytes()
    assert len(payload) == n * layers * width * 4 == manifest["hidden_bytes"]
    assert hashlib.sha256(payload).hexdigest() == manifest["hidden_sha256"]
    values = array.array("f", payload)
    assert all(math.isfinite(x) for x in values)
    for i, position in enumerate(manifest["positions"]):
        assert position == {"generation_index": i,
                            "input_position": len(manifest["prompt_token_ids"]) + i - 1,
                            "predicted_token_id": manifest["predicted_token_ids"][i]}
        for layer in range(layers):
            offset = (i * layers + layer) * width
            assert any(values[offset:offset + width]), (i, layer)
    assert manifest["total_generated_tokens"] == response["usage"]["completion_tokens"]
    return {"id": response["id"], "shape": manifest["shape"], "status": manifest["status"],
            "sha256": manifest["hidden_sha256"], "total_tokens": manifest["total_generated_tokens"],
            "predicted_token_ids": manifest["predicted_token_ids"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8083/v1")
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume-check", action="store_true",
                        help="make the first request after native sleep opt in to capture")
    args = parser.parse_args()
    saved = json.loads(args.request.read_text())
    prepared = saved.get("next", saved.get("request", saved))
    body = {"model": "default_model", "messages": prepared["messages"],
            "temperature": 0, "seed": 101, "max_tokens": 16, "stream": False,
            "chat_template_kwargs": {"enable_thinking": False}}
    args.output.mkdir(parents=True, exist_ok=False)
    if args.resume_check:
        response = request(args.base_url, {**body, "dogido_capture": True})
        result = inspect(args.capture_dir, response)
        (args.output / "checks.json").write_text(json.dumps(result, indent=2))
        print("resumed capture", result, flush=True)
        return
    results = {}
    for label, changes in [("off", {"dogido_capture": False}),
                           ("cached", {"dogido_capture": True}),
                           ("uncached", {"dogido_capture": True, "cache_prompt": False}),
                           ("capped", {"dogido_capture": True, "max_tokens": 40, "ignore_eos": True}),
                           ("off_after", {"dogido_capture": False})]:
        response = request(args.base_url, {**body, **changes})
        (args.output / f"{label}.json").write_text(json.dumps(response, ensure_ascii=False, indent=2))
        results[label] = inspect(args.capture_dir, response) if changes["dogido_capture"] else {
            "id": response["id"], "content": response["choices"][0]["message"]["content"]}
        if not changes["dogido_capture"]:
            assert not (args.capture_dir / response["id"]).exists()
        elif label != "capped":
            assert response["choices"][0]["message"]["content"] == results["off"]["content"]
        print(label, results[label], flush=True)
    assert results["cached"]["predicted_token_ids"] == results["uncached"]["predicted_token_ids"]
    assert results["capped"]["status"] == "partial"
    assert results["capped"]["shape"][0] == 32
    (args.output / "checks.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
