"""Build the native manager without starting Dogido or recording audio."""
import argparse
from pathlib import Path
import plistlib
import platform
import re
import shutil
import subprocess
import tempfile

SOURCE = Path(__file__).resolve().parents[1]
LOCAL_SIGNING_IDENTITY = "Dogido Local Code Signing"


def resolve_signing_identity(name, output):
    identities = dict(re.findall(r'\)\s+([0-9A-Fa-f]{40})\s+"([^"]+)"', output))
    matches = [fingerprint for fingerprint, label in identities.items()
               if name == label or name.upper() == fingerprint.upper()]
    if len(matches) != 1:
        raise SystemExit(
            f"署名用の証明書を一意に確認できません: {name}。"
            "キーチェーンアクセスでコード署名用の証明書を一度作成してください。"
            "一時的な試用のみ --ad-hoc を指定できますが、更新後にmacOSのアクセス確認が再表示されます。"
        )
    return matches[0]


def signing_key(name, ad_hoc=False):
    if ad_hoc:
        print("一時署名でビルドします。更新後にmacOSのアクセス許可が再確認される場合があります。")
        return "-"
    result = subprocess.run(
        ["security", "find-identity", "-p", "codesigning"],
        check=True, capture_output=True, text=True,
    )
    return resolve_signing_identity(name, result.stdout)


def build(project, destination, identity=LOCAL_SIGNING_IDENTITY, ad_hoc=False):
    project = project.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise SystemExit(f"出力先が既にあります。別の出力先を指定してください: {destination}")
    for path in ("dogido-rust/start_dialogue.command", "dogido-rust/start_voice.command",
                 "dogido-rust/src/server/utterance-view.js"):
        if not (project / path).is_file():
            raise SystemExit(f"本体のファイルがありません: {path}")
    identity = signing_key(identity, ad_hoc)
    with tempfile.TemporaryDirectory(prefix="dogido-manager-build-") as temporary:
        work = Path(temporary)
        app = work / "ドギド サーバー.app"
        executable = app / "Contents/MacOS/DogidoServerManager"
        executable.parent.mkdir(parents=True)
        resources = app / "Contents/Resources"
        resources.mkdir()
        subprocess.run([
            "xcrun", "swiftc", "-O", "-module-cache-path", str(work / "swift-cache"),
            "-target", f"{platform.machine()}-apple-macos13.0", "-framework", "AppKit", "-framework", "WebKit",
            str(SOURCE / "Sources/ServiceController.swift"), str(SOURCE / "Sources/DialogueLog.swift"), str(SOURCE / "Sources/main.swift"), "-o", str(executable),
        ], check=True)
        shutil.copyfile(project / "dogido-rust/src/server/utterance-view.js", resources / "utterance-view.js")
        iconset = work / "DogidoServer.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                pixels = size * scale
                name = f"icon_{size}x{size}" + ("@2x" if scale == 2 else "") + ".png"
                subprocess.run([
                    "sips", "-z", str(pixels), str(pixels),
                    str(SOURCE / "Resources/dogido-server-icon.png"), "--out", str(iconset / name),
                ], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(resources / "DogidoServer-v2.icns")], check=True)
        info = {
            "CFBundleName": "ドギド サーバー", "CFBundleDisplayName": "ドギド サーバー",
            "CFBundleIdentifier": "local.dogido.server-manager", "CFBundleExecutable": "DogidoServerManager",
            "CFBundlePackageType": "APPL", "CFBundleShortVersionString": "0.1.4", "CFBundleVersion": "5",
            "CFBundleIconFile": "DogidoServer-v2.icns",
            "LSMinimumSystemVersion": "13.0", "NSHighResolutionCapable": True,
            "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
            "NSMicrophoneUsageDescription": "ドギドの音声入力で、プレイヤーの声を認識するためにマイクを使います。",
            "NSAudioCaptureUsageDescription": "ドギドの音声入力で、スピーカー音声の回り込みを除去するために再生音を参照します。",
            "DogidoProjectRoot": str(project),
        }
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
        subprocess.run(["codesign", "--force", "--sign", identity, "--timestamp=none", str(app)], check=True)
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(app, destination)
    print(f"作成しました: {destination}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=SOURCE.parents[1])
    parser.add_argument("--output", type=Path, required=True)
    signing = parser.add_mutually_exclusive_group()
    signing.add_argument("--signing-identity", default=LOCAL_SIGNING_IDENTITY,
                         help="更新後も同じ証明書を使うコード署名identityの名前またはSHA-1")
    signing.add_argument("--ad-hoc", action="store_true", help="権限を引き継がない試用専用ビルド")
    args = parser.parse_args()
    build(args.project_root, args.output, args.signing_identity, args.ad_hoc)
