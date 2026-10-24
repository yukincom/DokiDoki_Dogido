#!/usr/bin/env python3
"""既存の固定revisionへ採取差分を適用し、nativeビルドとその識別記録を作る。

ソース取得・重みのdownload・常駐プロセスの切替は呼び手が担当する。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

from launch_gguf_capture import fingerprint

BASE = "6190802c8d3391ee908fb254d12fc243ad4540c6"


def run(*command: str, cwd: Path) -> str:
    return subprocess.check_output(command, cwd=cwd, text=True).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=6)
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    build = args.build.resolve()
    own = Path(__file__).resolve().parent
    patch = own / "gguf_capture.patch"
    if run("git", "rev-parse", "HEAD", cwd=source) != BASE:
        parser.error(f"source must be the preserved runtime revision {BASE}")
    difference = run("git", "diff", "--binary", "HEAD", cwd=source)
    expected = patch.read_text().strip()
    if difference and difference != expected:
        parser.error("source has changes other than this capture patch; use a separate checkout")
    if not difference:
        subprocess.run(["git", "apply", "--check", str(patch)], cwd=source, check=True)
        subprocess.run(["git", "apply", str(patch)], cwd=source, check=True)
    header = source / "tools/server/dogido-capture.h"
    data = (own / "gguf_capture.h").read_bytes()
    if header.exists() and header.read_bytes() != data:
        parser.error("existing collector header differs; inspect it before rebuilding")
    header.write_bytes(data)
    subprocess.run(["cmake", "-S", str(source), "-B", str(build),
                    "-DCMAKE_BUILD_TYPE=Release", "-DGGML_METAL=ON",
                    "-DGGML_METAL_EMBED_LIBRARY=ON", "-DLLAMA_CURL=OFF",
                    "-DLLAMA_BUILD_UI=OFF", "-DLLAMA_BUILD_TESTS=OFF"], check=True)
    subprocess.run(["cmake", "--build", str(build), "--target", "llama-server",
                    "-j", str(args.jobs)], check=True)
    receipt = {
        "schema": "dogido.capture_build.v1", "source_base": BASE,
        "collector": fingerprint(own / "gguf_capture.h"),
        "source_patch": fingerprint(patch),
        "server_binary": fingerprint(build / "bin/llama-server"),
        "libraries": {p.name: fingerprint(p)["sha256"]
                      for p in sorted((build / "bin").glob("*.dylib")) if not p.is_symlink()},
    }
    (build / "dogido-capture-build.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(build / "bin/llama-server")


if __name__ == "__main__":
    main()
