#!/usr/bin/env python3
"""指定済みnativeサーバーを起動し、明示された要求だけの採取先と重み識別を渡す。

生成・停止・保存済みの句との結合は担当しない。起動前にビルド記録を照合する。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

# Store only generation/runtime options. In particular API keys, HF tokens,
# headers and unknown future credential options never enter research exports.
RECORDED_OPTIONS = {
    "-m", "--model", "-md", "--model-draft", "--mmproj", "--alias",
    "-c", "--ctx-size", "-np", "--parallel", "-b", "--batch-size",
    "-ub", "--ubatch-size", "-ngl", "--n-gpu-layers", "-ngld", "--n-gpu-layers-draft",
    "--spec-type", "--spec-draft-n-max", "--reasoning", "--chat-template-file",
    "-fa", "--flash-attn", "-ctk", "--cache-type-k", "-ctv", "--cache-type-v",
}


def recorded_options(arguments: list[str]) -> dict:
    return {name: option_values(arguments, {name}) for name in sorted(RECORDED_OPTIONS)
            if option_values(arguments, {name})}


def fingerprint(path: Path) -> dict:
    """実ファイルを読み、途中で変更されていない場合だけSHA256とサイズを返す。"""
    path = path.expanduser().resolve(strict=True)
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"file changed while hashing: {path}")
    return {"path": str(path), "size_bytes": after.st_size,
            "mtime_ns": after.st_mtime_ns, "sha256": digest.hexdigest()}


def option_values(arguments: list[str], names: set[str]) -> list[str]:
    values = []
    for i, argument in enumerate(arguments):
        name, equal, value = argument.partition("=")
        if name in names:
            if equal:
                values.append(value)
            elif i + 1 < len(arguments):
                values.append(arguments[i + 1])
            else:
                raise ValueError(f"missing value for {name}")
    return values


def verify_build(binary: Path, own: Path) -> dict:
    """ソース・実行ファイル・同梱ライブラリをビルド時の記録と照合する。"""
    receipt = json.loads((binary.parent.parent / "dogido-capture-build.json").read_text())
    expected = {
        "collector": fingerprint(own / "gguf_capture.h"),
        "source_patch": fingerprint(own / "gguf_capture.patch"),
        "server_binary": fingerprint(binary),
    }
    for key, actual in expected.items():
        if actual["sha256"] != receipt[key]["sha256"]:
            raise ValueError(f"capture build binding mismatch: {key}; rebuild before launching")
    for name, expected_hash in receipt["libraries"].items():
        if fingerprint(binary.parent / name)["sha256"] != expected_hash:
            raise ValueError(f"capture build binding mismatch: {name}")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--max-positions", type=int, default=256)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("server_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    native = args.server_args
    if native[:1] == ["--"]:
        native = native[1:]
    if not 1 <= args.max_positions <= 4096:
        parser.error("--max-positions must be 1..4096")
    models = option_values(native, {"-m", "--model"})
    if len(models) != 1 or not Path(models[0]).is_absolute():
        parser.error("pass exactly one absolute -m/--model path after --")
    if any(arg in {"-hf", "--hf-repo", "--model-url", "-mu"} for arg in native):
        parser.error("capture launcher uses an explicit local model; download options are unsupported")
    binary = args.binary.expanduser().resolve(strict=True)
    own = Path(__file__).resolve().parent
    build = verify_build(binary, own)
    root = args.capture_dir.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    identity = {
        "schema": "dogido.capture_identity.v1",
        "model": fingerprint(Path(models[0])),
        "server_binary": fingerprint(binary),
        "collector": fingerprint(Path(__file__).with_name("gguf_capture.h")),
        "source_patch": fingerprint(Path(__file__).with_name("gguf_capture.patch")),
        "generation_options": recorded_options(native),
        "build": build,
        "libraries": [fingerprint(path) for path in sorted(binary.parent.glob("*.dylib"))
                      if not path.is_symlink()],
    }
    # The native server compares this path with its own parsed model path.
    identity["model"]["path"] = models[0]
    auxiliary = []
    for value in option_values(native, {"-md", "--model-draft", "--mmproj", "--chat-template-file"}):
        auxiliary.append(fingerprint(Path(value)))
    identity["auxiliary_files"] = auxiliary
    payload = json.dumps(identity, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    identities = root / "_identities"
    identities.mkdir(exist_ok=True)
    identity_path = identities / (hashlib.sha256(payload.encode()).hexdigest() + ".json")
    if not identity_path.exists():
        with identity_path.open("x", encoding="utf-8") as output:
            output.write(payload)
    if args.prepare_only:
        print(identity_path)
        return
    environment = os.environ.copy()
    environment.update(DOGIDO_CAPTURE_DIR=str(root),
                       DOGIDO_CAPTURE_IDENTITY=str(identity_path),
                       DOGIDO_CAPTURE_MAX_POSITIONS=str(args.max_positions))
    os.execve(binary, [str(binary), *native], environment)


if __name__ == "__main__":
    main()
