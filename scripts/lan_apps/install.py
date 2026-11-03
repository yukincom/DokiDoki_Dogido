"""Install a native Dogido app and a Finder alias. Does not start the app or change macOS settings."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import os
from pathlib import Path
import plistlib
import runpy
import shutil
import subprocess
import sys
import tempfile


HERE = Path(__file__).resolve().parent
NAMES = {"host": "ドギド共有", "client": "ドギド"}
LAUNCHER = "DogidoLauncher"
PRODUCT = "Dogido LAN app installer v1"
SERVICES = ["_dogido._tcp"]


def build_launcher(destination: Path) -> None:
    """Package builder uses this on the build Mac; receiving kits use the compiled executable."""
    if os.path.lexists(destination):
        raise ValueError(f"起動部品の出力先が使用中です: {destination}")
    subprocess.run(["/usr/bin/xcrun", "clang", "-fobjc-arc", "-Wall", "-Werror",
                    "-Wno-deprecated-declarations", "-mmacosx-version-min=14.2",
                    "-framework", "Cocoa", str(HERE / "launcher.m"), "-o", str(destination)], check=True)


def copy_regular(source: Path, target: Path, executable=False) -> None:
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"アプリの素材を確認できません: {source.name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target.chmod(0o755 if executable else 0o644)


def app_config(role: str, kit: Path | None) -> dict:
    if role == "host":
        return {"python": sys.executable}
    if kit is None:
        raise ValueError("遊ぶMacの初期設定済みフォルダを --kit で指定してください。")
    runpy.run_path(str(kit / "client_tools/family_runtime.py"))["validate_runtime"](kit)
    python = kit / "dogido-llm/bin/python"
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError("セットのPython環境が見つかりません。初期設定を完了してください。")
    return {"python": str(python), "kit": str(kit)}


def assemble(app: Path, role: str, kit: Path | None, config: dict, source: Path) -> None:
    """Build complete bundle first. source is the repository scripts directory or the prepared kit."""
    resources = app / "Contents/Resources"
    binary = app / "Contents/MacOS" / LAUNCHER
    binary.parent.mkdir(parents=True)
    resources.mkdir()
    if (HERE / LAUNCHER).is_file():
        copy_regular(HERE / LAUNCHER, binary, executable=True)
    else:
        build_launcher(binary)
    packaged = (source / "lan_tools").is_dir()
    lan = source / ("lan_tools" if packaged else "lan")
    for name in ("connection.py", "transport.py", "discovery.py"):
        copy_regular(lan / name, resources / "lan" / name)
    gateway = lan / "gateway.py" if packaged else source / "family-link/gateway.py"
    copy_regular(gateway, resources / "lan/gateway.py")
    if role == "client":
        for name in ("start_client.py", "log_relay.py", "minecraft_log_relay.py",
                     "family_runtime.py", "check.py"):
            if packaged:
                helper = source / "client_tools" / name
            else:
                folder = "family-client" if name in {"family_runtime.py", "check.py"} else "family-link"
                helper = source / folder / name
            copy_regular(helper, resources / "client_tools" / name)
        copy_regular(kit / "dogido-rust/target/release/dogido-rust", resources / "runtime/dogido-rust",
                     executable=True)
    (resources / "launch.plist").write_bytes(plistlib.dumps(config))
    info = {
        "CFBundleName": NAMES[role], "CFBundleDisplayName": NAMES[role],
        "CFBundleExecutable": LAUNCHER, "CFBundleIdentifier": "com.dokidoki-dogido.lan." + role,
        "CFBundlePackageType": "APPL", "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleVersion": "1", "CFBundleShortVersionString": "1.0",
        "LSMinimumSystemVersion": "14.2", "NSHighResolutionCapable": True,
        "DogidoRole": role, "DogidoInstaller": PRODUCT, "NSBonjourServices": SERVICES,
        "NSLocalNetworkUsageDescription": "同じLANにあるドギドを見つけ、ペアリングしたMacと接続します。",
    }
    if role == "client":
        info["NSMicrophoneUsageDescription"] = "マイクでドギドに話しかけるために使用します。"
        info["NSAudioCaptureUsageDescription"] = "ドギドの声がマイクに繰り返し入らないようにします。"
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
    subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(app)], check=True)


def verify_existing(path: Path, role: str) -> None:
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"同名の別ファイルがあります: {path}")
    try:
        info = plistlib.loads((path / "Contents/Info.plist").read_bytes())
        if (info.get("DogidoInstaller") != PRODUCT or info.get("DogidoRole") != role
                or info.get("CFBundleIdentifier") != "com.dokidoki-dogido.lan." + role):
            raise ValueError
        # Only replace an intact bundle produced by this installer, never hand-edited contents.
        if any(entry.is_symlink() for entry in path.rglob("*")):
            raise ValueError
        subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(path)],
                       check=True, capture_output=True)
    except (OSError, ValueError, plistlib.InvalidFileException, subprocess.CalledProcessError) as exc:
        raise ValueError(f"同名の別アプリまたは変更済みアプリがあります: {path}") from exc


def files_digest(app: Path) -> dict:
    """Use the signed content to skip reinstalling the same app and retain the Finder alias identity."""
    result = {}
    for path in sorted(app.rglob("*")):
        if path.is_file():
            with path.open("rb") as handle:
                result[path.relative_to(app)] = hashlib.file_digest(handle, "sha256").hexdigest()
    return result


def swap(first: Path, second: Path) -> None:
    # Both paths belong to the same volume. RENAME_SWAP keeps a complete app visible at all times.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.renamex_np(os.fsencode(first), os.fsencode(second), 2) != 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number), str(second))


def install(role: str, applications: Path, desktop: Path, kit: Path | None = None) -> tuple[Path, Path]:
    kit = kit.resolve() if kit else None
    config = app_config(role, kit)
    source = HERE.parent if HERE.parent.name == "scripts" else kit
    if source is None:
        raise ValueError("共有Mac用のアプリはドギドのソースから作成してください。")
    if not applications.is_dir() or not desktop.is_dir():
        raise ValueError("ApplicationsまたはDesktopフォルダが見つかりません。")
    target = applications / (NAMES[role] + ".app")
    alias = desktop / NAMES[role]
    existing = os.path.lexists(target)
    if existing:
        verify_existing(target, role)
    with tempfile.TemporaryDirectory(prefix=".dogido-install-", dir=applications) as stage:
        staged = Path(stage) / target.name
        assemble(staged, role, kit, config, source)
        verify_existing(staged, role)
        helper = staged / "Contents/MacOS" / LAUNCHER
        existing_alias = os.path.lexists(alias)
        if existing_alias and (alias.is_symlink() or not alias.is_file() or not existing or
                subprocess.run([str(helper), "--check-alias", str(alias), str(target)]).returncode):
            raise ValueError(f"デスクトップに同名の別ファイルがあります: {alias}")
        unchanged = existing and files_digest(target) == files_digest(staged)
        if not unchanged:
            if existing:
                swap(staged, target)
            else:
                # Refuse to replace a destination created concurrently (RENAME_EXCL).
                libc = ctypes.CDLL(None, use_errno=True)
                if libc.renamex_np(os.fsencode(staged), os.fsencode(target), 4) != 0:
                    number = ctypes.get_errno()
                    raise OSError(number, os.strerror(number), str(target))
        try:
            if not (unchanged and existing_alias):
                with tempfile.TemporaryDirectory(prefix=".dogido-alias-", dir=desktop) as folder:
                    draft = Path(folder) / alias.name
                    executable = target / "Contents/MacOS" / LAUNCHER
                    subprocess.run([str(executable), "--write-alias", str(draft), str(target)], check=True)
                    subprocess.run([str(executable), "--check-alias", str(draft), str(target)], check=True)
                    if existing_alias:
                        os.replace(draft, alias)
                    else:
                        # Fail on a racing unrelated desktop item rather than overwriting it.
                        os.link(draft, alias)
        except BaseException:
            if not unchanged:
                if existing:
                    swap(staged, target)
                else:
                    target.rename(staged)
            raise
    return target, alias


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="role", required=True)
    compile_parser = subcommands.add_parser("build-launcher")
    compile_parser.add_argument("destination", type=Path)
    for role in NAMES:
        cmd = subcommands.add_parser(role)
        cmd.add_argument("--applications", type=Path, default=Path("/Applications"))
        cmd.add_argument("--desktop", type=Path, default=Path.home() / "Desktop")
        if role == "client":
            cmd.add_argument("--kit", type=Path, required=True)
    args = parser.parse_args()
    if args.role == "build-launcher":
        build_launcher(args.destination)
        return
    app, alias = install(args.role, args.applications.absolute(), args.desktop.absolute(), getattr(args, "kit", None))
    print(f"アプリを配置しました: {app}\nFinderエイリアス: {alias}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit(f"ドギドの導入を完了できませんでした: {error}") from error
