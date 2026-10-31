"""現行本体とローカル音声部品から、家庭内の別Mac用セットを作る。サービスは起動しない。"""
from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

VERSION = "fabric-loader-0.18.4-1.21.11"
TEMPLATES = Path(__file__).resolve().parent / "family-client"
sys.path.insert(0, str(TEMPLATES))
from family_runtime import CLASSIFIER_FILES, RUST_BINARY, RUNTIME_SCRIPTS, WHISPER_WORKER, validate_runtime
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dogido-rust/scripts"))
from runtime_build import check_binary_freshness
ENV = """# このフォルダ専用。個人の .env はコピーしない。
DOGIDO_BIND_HOST=127.0.0.1
DOGIDO_BIND_PORT=5055
DOGIDO_MEMORY_DIR=.dogido_memory
DOGIDO_REQUEST_CLASSIFIER_SENRYU_MODEL_ROOT=models/dogido-senryu424-seed42
DOGIDO_REQUEST_CLASSIFIER_NORMAL_MODEL_ROOT=models/dogido-normal16-seed48-mob
DOGIDO_REQUEST_CLASSIFIER_PYTHON=dogido-llm/bin/python
DOGIDO_CUE_AUDIO_DIR=cue_voice
DOGIDO_TTS_BACKEND=voicevox
DOGIDO_VOICEVOX_URL=http://127.0.0.1:50021
DOGIDO_VOICEVOX_SPEAKER=21
DOGIDO_TTS_READING_ENGINE=unidic
DOGIDO_VOICE_WHISPER_CLI=.dogido_tools/whisper/bin/whisper-cli
DOGIDO_VOICE_WHISPER_MODEL=.dogido_tools/models/ggml-kotoba-whisper-v2.0-q5_0.bin
DOGIDO_VOICE_VAD_CLI=.dogido_tools/whisper/bin/whisper-vad-speech-segments
DOGIDO_VOICE_VAD_MODEL=.dogido_tools/models/ggml-silero-v6.2.0.bin
DOGIDO_VOICE_ECHO_CANCELLATION=webrtc
DOGIDO_VOICE_ECHO_PYTHON=dogido-llm/bin/python
DOGIDO_VOICE_ECHO_HELPER=.dogido_tools/echo-cancel/bin/dogido-audio-capture
"""


def copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    # copyfile avoids carrying machine-specific extended attributes into the package.
    shutil.copyfile(source, target)
    target.chmod(source.stat().st_mode & 0o777)


def output(*command: str | Path) -> str:
    return subprocess.check_output([str(part) for part in command], text=True)


def dependencies(path: Path) -> list[str]:
    return [line.strip().split(" (", 1)[0]
            for line in output("otool", "-L", path).splitlines()[1:]]


def portable_whisper(source: Path, destination: Path, worker_source: Path, worker_target: Path) -> None:
    pending = []
    for name in ("whisper-cli", "whisper-vad-speech-segments"):
        target = destination / "bin" / name
        copy_file(source / "build/bin" / name, target)
        pending.append(target)
    copy_file(worker_source, worker_target)
    pending.append(worker_target)
    visited = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        for dep in dependencies(current):
            if dep.startswith("@rpath/"):
                name = dep.removeprefix("@rpath/")
                target = destination / "lib" / name
                if not target.exists():
                    matches = sorted((source / "build").rglob(name))
                    if not matches:
                        raise ValueError(f"Whisper の依存が不足: {name}")
                    copy_file(matches[0], target)
                    pending.append(target)
            elif not dep.startswith(("/usr/lib/", "/System/Library/")):
                raise ValueError(f"同梱できない Whisper 依存: {dep}")
    for binary in sorted(visited):
        load_commands = output("otool", "-l", binary)
        rpaths = re.findall(r"cmd LC_RPATH\s+cmdsize \d+\s+path (.*?) \(offset", load_commands)
        command = ["install_name_tool"]
        for rpath in rpaths:
            command.extend(["-delete_rpath", rpath])
        if binary == worker_target:
            rpath = "@executable_path/" + os.path.relpath(destination / "lib", binary.parent)
        else:
            rpath = "@executable_path/../lib" if binary.parent.name == "bin" else "@loader_path"
        command.extend(["-add_rpath", rpath, str(binary)])
        subprocess.run(command, check=True, capture_output=True, text=True)
        subprocess.run(["codesign", "--force", "--sign", "-", str(binary)],
                       check=True, capture_output=True, text=True)


def uv_license_files(binary: Path, version: str) -> list[Path]:
    """同じuvの配布ライセンスを確認する。Homebrew・公式archive・wheelの配置を読む。

    wheelはmetadata上の版と実行ファイルの所属を照合し、別venvの資料を流用しない。
    不足は出力先を作る前に報告し、未完の家庭用セットを残さない。
    """
    names = ("LICENSE-MIT", "LICENSE-APACHE")
    for directory in (binary.parent.parent, binary.parent):
        files = [directory / name for name in names]
        if all(path.is_file() for path in files):
            return files
    for site in binary.parent.parent.glob("lib/python*/site-packages"):
        for distribution in metadata.distributions(path=[str(site)]):
            if distribution.metadata["Name"] != "uv" or distribution.version != version:
                continue
            entries = distribution.files or []
            if not any(Path(distribution.locate_file(entry)).resolve() == binary for entry in entries):
                continue
            files = [Path(distribution.locate_file(entry)) for name in names for entry in entries
                     if entry.name == name and ".dist-info/licenses/" in str(entry)]
            if len(files) == len(names) and all(path.is_file() for path in files):
                return files
    raise ValueError("指定uvのLICENSE-MIT／LICENSE-APACHEが不足しています。公式配布物を確認してください。")


def build(args: argparse.Namespace) -> None:
    source, target = args.source_root.resolve(), args.output.resolve()
    if target.exists() or target.with_suffix(".zip").exists():
        raise ValueError("出力先は未使用のフォルダ名を指定してください")
    # Python取得より前に使う同梱uvを、出力先を作る前に検査する。
    uv_version = output("sh", source / "scripts/check_uv.sh", args.uv.resolve()).strip()
    uv_licenses = uv_license_files(args.uv.resolve(), uv_version)
    validate_runtime(source)
    # A prepared Apple Silicon release is shipped; the receiving Mac needs no Rust compiler.
    binary = source / RUST_BINARY
    check_binary_freshness(source, binary)
    with binary.open("rb") as handle:
        header = handle.read(8)
    if header != b"\xcf\xfa\xed\xfe\x0c\x00\x00\x01":
        raise ValueError("同梱するRust本体はApple Silicon向けreleaseを指定してください")
    target.mkdir(parents=True)
    copy_file(binary, target / RUST_BINARY)
    for relative in CLASSIFIER_FILES:
        copy_file(source / relative, target / relative)
    for name in RUNTIME_SCRIPTS:
        relative = "dogido-rust/scripts/" + name
        copy_file(source / relative, target / relative)
    for name in ("README.md", "sources.json", "makurakotoba.json", "japanese_poetry_forms.json", "world_poetry.json", "index.json"):
        relative = "reference/language_education_and_poetry/" + name
        copy_file(source / relative, target / relative)
    copy_file(source / "reference/minecraft_technical/source_lock.json",
              target / "reference/minecraft_technical/source_lock.json")
    copy_file(source / "dogido-rust/Cargo.lock", target / "licenses/rust-Cargo.lock")
    shutil.copytree(source / "dogido-rust/third-party", target / "licenses/rust-third-party",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
    for name in ("dogido_server", "data"):
        shutil.copytree(source / name, target / name,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
    shutil.copytree(source / "cue_voice", target / "cue_voice",
                    ignore=shutil.ignore_patterns("player_names", ".DS_Store"))
    # Do not transfer household names or the parent's name-to-audio mapping.
    copy_file(source / "cue_voice/player_names/ushiro_tail.mp3",
              target / "cue_voice/player_names/ushiro_tail.mp3")
    for template in TEMPLATES.iterdir():
        if not template.is_file():
            continue
        path = target / ("client_tools" if template.suffix == ".py" else "") / template.name
        copy_file(template, path)
        if template.suffix == ".command":
            path.chmod(0o755)
    for source_name, installed in (("client.py", "family_link.py"), ("log_relay.py", "log_relay.py"),
                                   ("start_client.py", "start_client.py")):
        copy_file(TEMPLATES.parent / "family-link" / source_name, target / "client_tools" / installed)
    (target / ".env").write_text(ENV, encoding="utf-8")
    copy_file(source / ".env.shared.example", target / ".env.shared")
    (target / "connection.txt").write_text(
        "親Macで専用接続を起動し、息子Macへ渡す.zip を適用してください。パスワードは不要です。\n",
        encoding="utf-8")
    copy_file(source / "LICENSE.md", target / "LICENSE.md")
    copy_file(source / "LICENSE-CHARACTER.md", target / "LICENSE-CHARACTER.md")
    copy_file(source / ".dogido_tools/echo-cancel/bin/dogido-audio-capture",
              target / ".dogido_tools/echo-cancel/bin/dogido-audio-capture")
    portable_whisper(args.whisper_root, target / ".dogido_tools/whisper",
                     source / WHISPER_WORKER, target / WHISPER_WORKER)
    copy_file(args.whisper_root / "models/ggml-kotoba-whisper-v2.0-q5_0.bin",
              target / ".dogido_tools/models/ggml-kotoba-whisper-v2.0-q5_0.bin")
    copy_file(args.vad_model, target / ".dogido_tools/models/ggml-silero-v6.2.0.bin")
    copy_file(args.uv.resolve(), target / ".dogido_tools/uv")
    copy_file(source / "scripts/check_uv.sh", target / ".dogido_tools/check_uv.sh")
    for license_path in uv_licenses:
        copy_file(license_path, target / "licenses" / f"uv-{license_path.name}")
    copy_file(args.whisper_root / "LICENSE", target / "licenses/whisper-MIT.txt")
    (target / "licenses/SOURCES.md").write_text(
        "# 同梱部品の出典\n\n"
        "- uv: https://github.com/astral-sh/uv (MIT / Apache-2.0)\n"
        "- whisper.cpp: https://github.com/ggml-org/whisper.cpp (MIT)\n"
        "- Kotoba Whisper: https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0\n"
        "- Silero VAD: https://github.com/snakers4/silero-vad (MIT)\n"
        "- Fabric: https://fabricmc.net/ (Fabric API のライセンスは jar 内に同梱)\n"
        "- Rust本体: 同梱LICENSE.md、組込み素材: rust-third-party/、依存版: rust-Cargo.lock\n"
        "- 詩資料: reference/language_education_and_poetry/README.md、sources.json\n"
        "- Python / WebRTC 等は初期設定時に各パッケージから取得します。\n"
        "\nこのセットは家庭内テスト用です。Qwen・VOICEVOX・Minecraft本体は含みません。\n",
        encoding="utf-8")
    copy_file(source / "adapter/minecraft-fabric/build/libs/dogido-fabric-client-0.1.0.jar",
              target / "minecraft/mods/dogido-fabric-client-0.1.0.jar")
    copy_file(args.fabric_api, target / "minecraft/mods" / args.fabric_api.name)
    with zipfile.ZipFile(args.fabric_profile) as archive:
        # Read only the two expected entries instead of extracting arbitrary archive paths.
        for extension in ("json", "jar"):
            relative = f"{VERSION}/{VERSION}.{extension}"
            payload = archive.read(relative)
            destination = target / "fabric-version" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
    validate_runtime(target)
    manifest = {
        "source_head": output("git", "-C", source, "rev-parse", "HEAD").strip(),
        "source": "main working tree including current local fixes",
        "mode": "Rust independent single player; remote Qwen; local VOICEVOX and STT",
        "runtime": "rust",
        "uv_version": uv_version,
        "files": {},
    }
    for path in sorted(target.rglob("*")):
        if path.is_file():
            with path.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            manifest["files"][path.relative_to(target).as_posix()] = digest
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                          encoding="utf-8")
    # Standard ZIP preserves executable permission bits and has no macOS metadata sidecars.
    with zipfile.ZipFile(target.with_suffix(".zip"), "x", zipfile.ZIP_DEFLATED,
                         compresslevel=1) as archive:
        for path in sorted(target.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(target.parent))
    print(target.with_suffix(".zip"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("source-root", "whisper-root", "vad-model", "fabric-profile", "fabric-api", "uv", "output"):
        parser.add_argument("--" + flag, type=Path, required=True)
    build(parser.parse_args())
