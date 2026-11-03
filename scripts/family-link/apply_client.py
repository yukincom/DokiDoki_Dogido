"""Update only launcher/helper files in an existing Dogido kit."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import runpy
import unicodedata

ALLOWED_UPDATES = {
    '00_setup.command', '01_connect.command', '02_server.command', '03_voice.command',
    'ドギドを起動.command', '呼び名を設定.command',
    'client_tools/check.py', 'client_tools/install_fabric.py', 'client_tools/family_link.py',
    'client_tools/configure_call_name.py', 'client_tools/log_relay.py',
    'client_tools/minecraft_log_relay.py', 'client_tools/family_runtime.py',
    'client_tools/start_client.py',
    'はじめに.md', 'Minecraft設定.txt',
}
STATIC_PACKAGE_FILES = {
    'apply_client.py', 'family_runtime.py', '息子Macで接続を設定.command',
    *(f'updates/{relative}' for relative in ALLOWED_UPDATES),
}
LINK_FILES = {'link/identity', 'link/known_hosts', 'link/connection.json'}
MANIFEST_FILES = STATIC_PACKAGE_FILES | LINK_FILES


def reject_links(root, target):
    for path in (target, *target.parents):
        if path == root:
            break
        if path.is_symlink():
            raise ValueError(f'更新先がリンクです: {target.relative_to(root)}')


def _ignored_metadata(relative, name):
    return '__MACOSX' in relative.parts or name == '.DS_Store' or name.startswith('._')


def validate_package(package):
    """Verify the complete static updater before any destination write."""
    if package.is_symlink() or not package.is_dir():
        raise ValueError('更新パッケージが見つからないか、リンクになっています')
    manifest_path = package / 'manifest.json'
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError('更新パッケージのmanifestがありません')
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError('更新パッケージのmanifestが壊れています') from error
    if (not isinstance(manifest, dict) or set(manifest) != MANIFEST_FILES
            or any(not isinstance(value, str) or not re_full_sha256(value)
                   for value in manifest.values())):
        raise ValueError('更新パッケージのmanifest対象が一致しません')

    allowed = STATIC_PACKAGE_FILES | LINK_FILES | {'manifest.json'}
    found = {}
    for source in sorted(package.rglob('*')):
        if source.is_symlink():
            raise ValueError('更新パッケージにリンクが含まれています')
        if not source.is_file():
            continue
        relative_path = source.relative_to(package)
        if _ignored_metadata(relative_path, source.name):
            continue
        relative = unicodedata.normalize('NFC', relative_path.as_posix())
        if relative not in allowed:
            raise ValueError(f'更新対象外のファイルです: {relative}')
        if relative in found:
            raise ValueError(f'更新ファイル名が重複しています: {relative}')
        found[relative] = source
    if set(found) != allowed:
        missing = sorted(allowed - set(found))
        raise ValueError('更新パッケージが不足しています: ' + ', '.join(missing))
    for relative in MANIFEST_FILES:
        digest = hashlib.sha256(found[relative].read_bytes()).hexdigest()
        if digest != manifest[relative]:
            raise ValueError(f'更新パッケージのhashが一致しません: {relative}')
    return found


def re_full_sha256(value):
    return len(value) == 64 and all(char in '0123456789abcdef' for char in value)


def apply(package, kit):
    package_files = validate_package(package)
    kit = kit.resolve()
    if not all((kit / name).exists() for name in ('00_setup.command', '.env.shared', 'dogido_server', 'minecraft')):
        raise ValueError('ドギドの展開フォルダを選んでください（00_setup.command がある場所）')
    helper = package_files['family_runtime.py']
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
            if _ignored_metadata(relative_path, source.name):
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
    keys = {name: package_files[f'link/{name}'].read_bytes()
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
    legacy.write_text('接続先は .dogido_tools/family-link/connection.json で管理します。\nLANサーバーPCのパスワードは不要です。\n')
    print(f'接続更新が完了しました: {kit}')
    print('LANサーバーPCの専用接続を開いたまま、「ドギドを起動.command」を開いてください。')
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
