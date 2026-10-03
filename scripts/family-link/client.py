"""Pinned-host, key-only forwarding client. No password fallback or remote commands."""
from __future__ import annotations
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import sys


def command(root):
    state = root / '.dogido_tools/family-link'
    config = json.loads((state / 'connection.json').read_text())
    host = str(ipaddress.IPv4Address(config['host']))
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', config['user']):
        raise ValueError('接続ユーザー名が不正です')
    if (config['port'], config['remote_port'], config['local_port']) != (22080, 22081, 8080):
        raise ValueError('ドギド専用接続のポート設定が一致しません')
    identity, known = state / 'identity', state / 'known_hosts'
    if not identity.is_file() or not known.is_file():
        raise ValueError('専用鍵がありません。接続更新ZIPを先に適用してください。')
    identity.chmod(0o600)
    known_option = str(known).replace('\\', '\\\\').replace('"', '\\"')
    return ['/usr/bin/ssh', '-F', '/dev/null', '-T', '-N', '-p', '22080',
            '-i', str(identity), '-o', 'IdentityAgent=none', '-o', 'IdentitiesOnly=yes',
            '-o', 'BatchMode=yes', '-o', 'PasswordAuthentication=no',
            '-o', 'KbdInteractiveAuthentication=no', '-o', 'PreferredAuthentications=publickey',
            '-o', 'StrictHostKeyChecking=yes', '-o', f'UserKnownHostsFile="{known_option}"',
            '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'UpdateHostKeys=no',
            '-o', 'ExitOnForwardFailure=yes', '-o', 'ConnectTimeout=10',
            '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
            '-o', 'ControlMaster=no', '-o', 'ControlPath=none', '-o', 'ForwardAgent=no',
            '-o', 'ForwardX11=no', '-o', 'PermitLocalCommand=no', '-o', 'LogLevel=VERBOSE',
            '-L', '127.0.0.1:8080:127.0.0.1:22081', config['user'] + '@' + host]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        argv = command(root)
        print('ドギド専用の鍵で接続します。親Macのパスワードは入力しません。')
        print('親Macの「親Macで起動.command」を開いた状態で使ってください。')
        print('Authenticated to ... の後、02_server.command へ進みます。終了は Ctrl+C。', flush=True)
        if args.dry_run:
            print('設定確認のみ。SSHを開始していません。')
            return 0
        os.execv(argv[0], argv)
    except (OSError, ValueError, KeyError) as error:
        print(f'接続できません: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
