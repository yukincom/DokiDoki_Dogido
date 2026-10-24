"""ローカルMLXモデルの重み・tokenizer・templateを起動時のhashへ結び付ける。

実ファイルとnative model_keyを照合する。モデルのload・downloadは担当しない。
"""
import hashlib
import json
from pathlib import Path


def asset_paths(directory):
    directory = Path(directory).expanduser().resolve(strict=True)
    weights = sorted(directory.glob("*.safetensors"))
    if not weights:
        raise ValueError("local MLX model has no safetensors weights")
    names = {"config.json", "tokenizer.json", "tokenizer_config.json", "tokenizer.model",
        "special_tokens_map.json", "added_tokens.json", "generation_config.json",
        "model.safetensors.index.json", "vocab.json", "merges.txt", "vocab.txt"}
    assets = sorted({*weights, *(p for p in directory.iterdir()
                    if p.name in names or p.suffix == ".jinja")})
    if not (directory / "config.json").is_file() or not any(
            (directory / name).is_file() for name in ("tokenizer.json", "tokenizer.model", "vocab.json", "vocab.txt")):
        raise ValueError("local MLX model is missing configuration or tokenizer assets")
    return directory, assets


def prepare_identity(directory, fingerprint=None):
    # Reuse the same stat-checked streaming hash as the GGUF launcher.
    if fingerprint is None:
        from launch_gguf_capture import fingerprint
    directory, assets = asset_paths(directory)
    return {"schema": "dogido.mlx_capture_identity.v1", "resolved_model_path": str(directory),
            "files": {path.name: fingerprint(path) for path in assets}}


def verify_identity(identity, model_key):
    if not isinstance(identity, dict) or identity.get("schema") != "dogido.mlx_capture_identity.v1":
        raise ValueError("verified local MLX asset identity is required; pass --identity-model")
    if not isinstance(model_key, (tuple, list)) or len(model_key) != 3:
        raise ValueError("unsupported native model identity")
    if model_key[1] is not None or model_key[2] is not None:
        raise ValueError("adapter and draft asset binding is unsupported")
    directory, assets = asset_paths(model_key[0])
    if str(directory) != identity.get("resolved_model_path"):
        raise ValueError("native model path differs from fingerprinted local model")
    recorded = identity.get("files", {})
    if {path.name for path in assets} != set(recorded):
        raise ValueError("model assets changed after fingerprinting")
    for path in assets:
        item = recorded[path.name]
        stat = path.stat()
        if (item.get("path"), item.get("size_bytes"), item.get("mtime_ns")) != (
                str(path.resolve()), stat.st_size, stat.st_mtime_ns):
            raise ValueError("model asset changed after fingerprinting")
        digest = item.get("sha256", "")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("invalid model asset fingerprint")
    return identity


def template_identity(tokenizer):
    template = tokenizer.chat_template
    payload = json.dumps(template, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return {"encoding": "canonical-json-utf8", "sha256": hashlib.sha256(payload).hexdigest()}
