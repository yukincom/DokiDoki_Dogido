"""採取中の排他、取消完了と失敗保留を一時ファイルで検査する。モデル・サーバーは起動しない。"""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True, help='採取パッチとヘッダーを適用したllama.cpp')
    parser.add_argument('--build', type=Path, required=True, help='同じrevisionのllama-commonビルド')
    args = parser.parse_args()
    source, build = args.source.resolve(strict=True), args.build.resolve(strict=True)
    own = Path(__file__).resolve().parent
    if (own / 'gguf_capture.h').read_bytes() != (source / 'tools/server/dogido-capture.h').read_bytes():
        parser.error('writer fixture needs the current capture header')
    include = [f'-I{source / d}' for d in ('common', 'include', 'ggml/include', 'tools/mtmd',
                                           'tools/server', 'vendor', 'vendor/hash', 'src')]
    with tempfile.TemporaryDirectory(prefix='dogido-writer-test-') as tmp:
        root = Path(tmp)
        sha = root / 'sha.o'
        subprocess.run([os.environ.get('CC', 'cc'), *include, '-c', source / 'vendor/hash/sha256/sha256.c',
                        '-o', sha], check=True)
        exe = root / 'writer'
        subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17', *include, own / 'test_gguf_writer.cpp',
                        sha, '-o', exe, f'-L{build / "bin"}', '-lllama-common', f'-Wl,-rpath,{build / "bin"}'], check=True)
        captures = root / 'captures'
        captures.mkdir()
        subprocess.run([exe, captures], check=True)
    print('GGUF writer lifecycle fixture: OK')


if __name__ == '__main__':
    main()
