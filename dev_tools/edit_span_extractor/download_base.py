"""固定revisionの公開日本語文字BERTを指定したローカル領域へ取得する。

Hugging FaceへのGETだけを行い、句・対話・設定は読み取らない。既存ファイルは
既知のSHA-256を照合し、別内容なら上書きせず停止する。
"""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

REPO = "tohoku-nlp/bert-base-japanese-char-v3"
REVISION = "ccddee593a9e03a589ae1feedcd4d1eb06d6ba02"
FILES = {
    "pytorch_model.bin": "eb91b018b5226bdede70a07333ad58507a0479ffd3f2698109c29110e9078ee0",
    "config.json": "555245f0e3c4fc2362fc39026d665f605be28aa03a47b2ea99d6f5aeb9bebabf",
    "vocab.txt": "57411bcac5e9559f2aa4d316a2217289048cb40fe23187b02a81aeb3e5d61cf3",
    "tokenizer_config.json": "d433b268e882ad1771cf0803c7f39f0bf444355bbeb299479d1c1a302b38b7ca",
    "README.md": "0f1a671d6f9c7836af8576c3a262c33f0de55e4fc8b9a990c6b6083a8c82f0ea",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = {"repo": REPO, "revision": REVISION, "files": {}}
    for name, expected in FILES.items():
        path = args.output / name
        if not path.exists():
            temporary = path.with_suffix(path.suffix + ".part")
            with urllib.request.urlopen(f"https://huggingface.co/{REPO}/resolve/{REVISION}/{name}", timeout=60) as response, temporary.open("wb") as out:
                while chunk := response.read(1024 * 1024):
                    out.write(chunk)
            if hashlib.sha256(temporary.read_bytes()).hexdigest() != expected:
                raise ValueError(f"Downloaded content differs from pinned SHA-256: {name}")
            temporary.rename(path)
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"Existing file differs from pinned SHA-256: {name}")
        manifest["files"][name] = {"bytes": path.stat().st_size, "sha256": actual}
    (args.output / "provenance.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
