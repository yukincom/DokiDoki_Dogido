"""Build the small Qwen connection updater; no keys, processes or OS changes."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

SCRIPTS = Path(__file__).resolve().parent


def build(target):
    target = target.resolve()
    if target.exists() or target.with_suffix('.zip').exists():
        raise ValueError('出力先は未使用のフォルダ名を指定してください')
    def copy(source, relative):
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, path)
        path.chmod(0o755 if path.suffix == '.command' else 0o644)
    for name in ('gateway.py', '親Macで起動.command', 'はじめに.md'):
        copy(SCRIPTS / 'family-link' / name, name)
    for name in ('apply_client.py', '息子Macで接続を設定.command'):
        copy(SCRIPTS / 'family-link' / name, 'client-package/' + name)
    copy(SCRIPTS / 'family-client/family_runtime.py', 'client-package/family_runtime.py')
    for name in ('00_setup.command', '01_connect.command', '02_server.command', '03_voice.command',
                 'ドギドを起動.command', 'はじめに.md', 'Minecraft設定.txt'):
        copy(SCRIPTS / 'family-client' / name, 'client-package/updates/' + name)
    for name in ('check.py', 'install_fabric.py', 'family_runtime.py'):
        copy(SCRIPTS / 'family-client' / name, 'client-package/updates/client_tools/' + name)
    for source, name in (('client.py', 'family_link.py'), ('log_relay.py', 'log_relay.py'),
                         ('start_client.py', 'start_client.py')):
        copy(SCRIPTS / 'family-link' / source, 'client-package/updates/client_tools/' + name)
    manifest = {p.relative_to(target).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(target.rglob('*')) if p.is_file()}
    (target / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    with zipfile.ZipFile(target.with_suffix('.zip'), 'x', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(target.rglob('*')):
            if path.is_file():
                archive.write(path, path.relative_to(target.parent))
    print(target / '親Macで起動.command')
    print(target.with_suffix('.zip'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    build(parser.parse_args().output)
