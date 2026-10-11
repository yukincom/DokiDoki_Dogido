"""Build the native manager without starting Dogido or recording audio."""
import argparse
from pathlib import Path
import plistlib
import platform
import shutil
import subprocess
import tempfile

SOURCE = Path(__file__).resolve().parents[1]


def build(project, destination):
    project = project.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise SystemExit(f"出力先が既にあります。別の出力先を指定してください: {destination}")
    for path in ("dogido-rust/start_dialogue.command", "dogido-rust/start_voice.command",
                 "dogido-rust/src/server/utterance-view.js"):
        if not (project / path).is_file():
            raise SystemExit(f"本体のファイルがありません: {path}")
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
            str(SOURCE / "Sources/ServiceController.swift"), str(SOURCE / "Sources/main.swift"), "-o", str(executable),
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
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(resources / "DogidoServer.icns")], check=True)
        info = {
            "CFBundleName": "ドギド サーバー", "CFBundleDisplayName": "ドギド サーバー",
            "CFBundleIdentifier": "local.dogido.server-manager", "CFBundleExecutable": "DogidoServerManager",
            "CFBundlePackageType": "APPL", "CFBundleShortVersionString": "0.1.0", "CFBundleVersion": "1",
            "CFBundleIconFile": "DogidoServer.icns",
            "LSMinimumSystemVersion": "13.0", "NSHighResolutionCapable": True,
            "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
            "NSMicrophoneUsageDescription": "ドギドの音声入力で、プレイヤーの声を認識するためにマイクを使います。",
            "NSAudioCaptureUsageDescription": "ドギドの音声入力で、スピーカー音声の回り込みを除去するために再生音を参照します。",
            "DogidoProjectRoot": str(project),
        }
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
        subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True)
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(app, destination)
    print(f"作成しました: {destination}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=SOURCE.parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.project_root, args.output)
