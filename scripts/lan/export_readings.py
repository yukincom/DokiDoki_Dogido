"""共有アプリから既存の設定とRust readerだけを起動する。生成・録音・保存はしない。"""
from pathlib import Path
import os
import subprocess
import sys


def main():
    root = Path(sys.argv[1]).resolve(strict=True)
    os.chdir(root)
    sys.path.insert(0, str(root))
    from dogido_server.config import get_settings
    settings = get_settings()
    if not settings.memory_enabled:
        raise RuntimeError('読み辞書の保存が無効です。')
    env = dict(os.environ, **settings.primary_storage_env())
    binary = root / 'dogido-rust/target/release/dogido-rust'
    subprocess.run([str(binary), 'export-readings', '--memory-dir', str(settings.memory_dir.resolve())],
                   check=True, timeout=4, env=env)


if __name__ == '__main__':
    main()
