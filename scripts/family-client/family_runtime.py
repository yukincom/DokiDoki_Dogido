"""家庭用Rustセットのファイル確認と共通の起動指定。起動・通信・書込みはしない。"""
from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import sys


RUST_BINARY = "dogido-rust/target/release/dogido-rust"
RUNTIME_SCRIPTS = (
    "launch_dialogue.py", "runtime_build.py", "haiku_tokens.py", "workshop_helper.py",
    "tts_shared_tokens.py", "tts_unidic_adapter.py", "combat_input_helper.py",
    "web_adapter.py",
)
RUNTIME_DATA = (
    "dogido_server/runtime_defaults.json",
    "dogido_server/runtime_settings.py",
    "dogido_server/config.py",
    "dogido_server/platform_ai.py",
    "dogido_server/combat_input_contract.py",
    "dogido_server/language_dialogue/main_web.py",
    "dogido_server/language_dialogue/chrome_web.py",
    "dogido_server/language_dialogue/google_overview.py",
    "dogido_server/language_dialogue/web_types.py",
    "dogido_server/voice_settings.py",
    "dogido_server/voice_capture.py",
    "dogido_server/echo_input.py",
    "dogido_server/language_dialogue/source_cards.json",
    "reference/language_education_and_poetry/data/normalized/index.json",
)
LEGACY_RUNTIME = ("dogido_server/app.py", "dogido_server/service.py", "dogido_server/voice_input.py")


def runtime_files():
    return (RUST_BINARY, *("dogido-rust/scripts/" + name for name in RUNTIME_SCRIPTS), *RUNTIME_DATA)


def reject_runtime_link(root: Path, path: Path) -> None:
    while path != root:
        if path.is_symlink():
            raise ValueError(f"Rust本体の部品がリンクです: {path.relative_to(root)}")
        path = path.parent


def validate_runtime(root: Path) -> None:
    """旧セットや一部だけの更新を、設定や鍵を書き換える前に拒否する。"""
    required = list(runtime_files())
    index = root / 'reference/language_education_and_poetry/data/normalized/index.json'
    reject_runtime_link(root, index)
    if index.is_file():
        try:
            manifest = json.loads(index.read_text(encoding='utf-8'))
            datasets = manifest['datasets']
            if not isinstance(datasets, list) or not datasets:
                raise ValueError('empty datasets')
            for dataset in datasets:
                for field in ('path', 'index_path'):
                    name = dataset[field]
                    if not isinstance(name, str) or not name or Path(name).name != name or name in {'.', '..'}:
                        raise ValueError('invalid dataset path')
                    required.append((index.parent / name).relative_to(root).as_posix())
        except (ValueError, TypeError, KeyError) as error:
            raise ValueError('Rust本体の国語資料目録が不正です') from error
    for relative in required:
        path = root / relative
        if not path.is_file():
            raise ValueError(f"Rust本体セットが不足しています: {relative}。接続更新だけでは本体を移行できません。")
        reject_runtime_link(root, path)
    if not os.access(root / RUST_BINARY, os.X_OK):
        raise ValueError("同梱Rust本体に実行権限がありません")
    legacy = [relative for relative in LEGACY_RUNTIME if (root / relative).exists()]
    if legacy:
        raise ValueError("旧Python本体のセットです。Rust本体を含む新しい導入セットを用意してください。既存の設定・記憶・ワールドは変更していません。")


def server_url(settings) -> str:
    host = settings.bind_host
    try:
        local = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = False
    if not local or not 1 <= settings.bind_port <= 65535:
        raise ValueError("家庭用セットの本体は、このMacの有効なローカルポートを指定してください")
    authority = f"[{host}]" if ":" in host else host
    return f"http://{authority}:{settings.bind_port}"


def is_rust_ready(value) -> bool:
    return (isinstance(value, dict) and value.get("ok") is True
            and value.get("service") == "dogido-server" and value.get("runtime") == "rust"
            and value.get("dialogue_ready") is True)


def launch_command(root: Path, *, voice=False, check=False) -> list[str]:
    command = [sys.executable, "-u", str(root / "dogido-rust/scripts/launch_dialogue.py"),
               "--settings-dir", str(root)]
    if voice:
        command.append("--voice")
    if check:
        command.append("--check")
    return command
