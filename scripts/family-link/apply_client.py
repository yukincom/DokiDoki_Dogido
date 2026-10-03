"""Update only launcher/helper files in an existing Dogido kit."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import shutil
import runpy
import unicodedata

ALLOWED_UPDATES = {
    '00_setup.command', '01_connect.command', '02_server.command', '03_voice.command',
    'ドギドを起動.command',
    'client_tools/check.py', 'client_tools/install_fabric.py', 'client_tools/family_link.py',
    'client_tools/log_relay.py', 'client_tools/family_runtime.py', 'client_tools/start_client.py',
    'はじめに.md', 'Minecraft設定.txt',
}


def reject_links(root, target):
    for path in (target, *target.parents):
        if path == root:
            break
        if path.is_symlink():
            raise ValueError(f'更新先がリンクです: {target.relative_to(root)}')


def apply(package, kit):
    kit = kit.resolve()
    if not all((kit / name).exists() for name in ('00_setup.command', '.env.shared', 'dogido_server', 'minecraft')):
        raise ValueError('ドギドの展開フォルダを選んでください（00_setup.command がある場所）')
    helper = Path(__file__).resolve().parent / 'family_runtime.py'
    if not helper.is_file():  # Source checkout layout; shipped updater keeps it beside this file.
        helper = Path(__file__).resolve().parents[1] / 'family-client/family_runtime.py'
    runpy.run_path(str(helper))['validate_runtime'](kit)
    # Validate the entire package and destination before the first write. An
    # accidental extra file must never overwrite settings, runtime or worlds.
    updates = {}
    for source in sorted((package / 'updates').rglob('*')):
        if source.is_symlink():
            raise ValueError('更新パッケージにリンクが含まれています')
        if source.is_file():
            relative_path = source.relative_to(package / 'updates')
            # Finder/Archive Utility can add metadata and decompose Japanese
            # filenames. Neither changes which application files are allowed.
            if ('__MACOSX' in relative_path.parts or source.name == '.DS_Store'
                    or source.name.startswith('._')):
                continue
            relative = unicodedata.normalize('NFC', relative_path.as_posix())
            if relative not in ALLOWED_UPDATES:
                raise ValueError(f'更新対象外のファイルです: {relative}')
            if relative in updates:
                raise ValueError(f'更新ファイル名が重複しています: {relative}')
            updates[relative] = source.read_bytes()
    if not updates:
        raise ValueError('更新ファイルがありません')
    if 'Minecraft設定.txt' in updates:
        updates['Minecraft設定.txt'] = updates['Minecraft設定.txt'].decode('utf-8').replace(
            'このテキストと同じ場所にある minecraft フォルダを指定', str(kit / 'minecraft')).encode('utf-8')
    keys = {name: (package / 'link' / name).read_bytes()
            for name in ('identity', 'known_hosts', 'connection.json')}
    for relative in (*updates, 'connection.txt', '.dogido_tools/family-link',
                     '.dogido_tools/setup-backups/qwen-link-v1',
                     *(f'.dogido_tools/family-link/{name}' for name in keys)):
        reject_links(kit, kit / relative)
    for relative in updates:
        target = kit / relative
        reject_links(kit, target.with_name(target.name + '.dogido-update'))
        reject_links(kit, kit / '.dogido_tools/setup-backups/qwen-link-v1' / relative)
    state = kit / '.dogido_tools/family-link'
    if state.is_symlink():
        raise ValueError('鍵の保存先がリンクのため停止しました')
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    state.chmod(0o700)
    backups = kit / '.dogido_tools/setup-backups/qwen-link-v1'
    for relative, data in updates.items():
        target = kit / relative
        if target.is_symlink():
            raise ValueError(f'更新先がリンクです: {relative}')
        if target.exists() and not (backups / relative).exists():
            (backups / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backups / relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + '.dogido-update')
        reject_links(kit, temporary)
        temporary.write_bytes(data)
        temporary.chmod(0o755 if target.suffix == '.command' else 0o644)
        os.replace(temporary, target)
    for name, data in keys.items():
        target = state / name
        if target.is_symlink():
            raise ValueError(f'鍵の保存先がリンクです: {name}')
        # Restrict permissions before writing private key bytes, including an
        # existing file with overly broad permissions from a previous install.
        target.touch(mode=0o600, exist_ok=True)
        target.chmod(0o600)
        target.write_bytes(data)
    # Keep the old connection text as a backup; the new connector uses only the JSON above.
    legacy = kit / 'connection.txt'
    if legacy.exists() and not (backups / 'connection.txt').exists():
        backups.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy, backups / 'connection.txt')
    legacy.write_text('接続先は .dogido_tools/family-link/connection.json で管理します。\n親Macのパスワードは不要です。\n')
    print(f'接続更新が完了しました: {kit}')
    print('親Macの専用接続を開いたまま、「ドギドを起動.command」を開いてください。')
    print('記憶・ワールド・.env・専用Python環境はそのままです。')
    print(f'以前の導入ファイルの控え: {backups}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('kit', type=Path)
    args = parser.parse_args()
    try:
        apply(Path(__file__).resolve().parent, args.kit)
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error
