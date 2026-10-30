"""学習用CSV dropletを秘密鍵のいらないad-hoc署名でビルドする。"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import tempfile

SOURCE = Path(__file__).resolve().parents[1]
REPO = SOURCE.parents[1]


def prepare_assets(project, target):
    target.mkdir(parents=True)
    classifier = project / "dev_tools/request_classifier"
    releases = {"normal": "2026-10-24-normal-dialogue-four-system", "senryu": "2026-10-24-senryu-only-424"}
    for mode, release in releases.items():
        folder = target / "baseline" / mode
        folder.mkdir(parents=True)
        for name in ("labels.json", "train.jsonl", "dev.jsonl", "test.jsonl"):
            shutil.copyfile(classifier / "releases" / release / name, folder / name)
    (target / "shared").mkdir()
    for name in ("dataset.py", "evaluation.py", "train.py", "labels.json"):
        shutil.copyfile(classifier / name, target / "shared" / name)
    (target / "evidence").mkdir()
    for day in ("2026-10-28", "2026-10-30"):
        shutil.copyfile(classifier / "curation" / (day + "-live-feedback") / "evidence.jsonl",
                        target / "evidence" / (day + ".jsonl"))
    shutil.copyfile(SOURCE / "train_colab.py", target / "train_colab.py")
    hashes = {str(p.relative_to(target)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(target.rglob("*")) if p.is_file()}
    (target / "baseline-sha256.json").write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")


def build(project, destination):
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise SystemExit("出力先が既にあります: " + str(destination))
    identity = "-"  # ローカルでビルドしたアプリ用。キーチェーンの秘密鍵を使用しない。
    with tempfile.TemporaryDirectory(prefix="dogido-training-build-") as temporary:
        work = Path(temporary)
        app = work / "ドギド 学習教材.app"
        executable = app / "Contents/MacOS/DogidoTrainingBuilder"
        executable.parent.mkdir(parents=True)
        resources = app / "Contents/Resources"
        resources.mkdir()
        prepare_assets(project, resources / "assets")
        shutil.copyfile(SOURCE / "builder.py", resources / "builder.py")
        subprocess.run(["xcrun", "swiftc", "-O", "-module-cache-path", str(work / "swift-cache"),
                        "-target", f"{platform.machine()}-apple-macos13.0", "-framework", "AppKit",
                        str(SOURCE / "Sources/main.swift"), "-o", str(executable)], check=True)
        iconset = work / "Training.iconset"
        iconset.mkdir()
        # macOS標準の図形・文字で教材用アイコンを描く。外部画像・モデルを取得しない。
        icon_source = work / "icon.swift"
        icon_source.write_text('''import AppKit
let image = NSImage(size: NSSize(width: 1024, height: 1024))
image.lockFocus()
NSColor(calibratedRed: 0.12, green: 0.32, blue: 0.38, alpha: 1).setFill()
NSBezierPath(roundedRect: NSRect(x: 30, y: 30, width: 964, height: 964), xRadius: 200, yRadius: 200).fill()
let paragraph = NSMutableParagraphStyle(); paragraph.alignment = .center
for (text, y, size) in [("CSV", CGFloat(545), CGFloat(230)), ("↓ T4", CGFloat(225), CGFloat(205))] {
    (text as NSString).draw(in: NSRect(x: 50, y: y, width: 924, height: 300), withAttributes: [.font: NSFont.boldSystemFont(ofSize: size), .foregroundColor: NSColor.white, .paragraphStyle: paragraph])
}
image.unlockFocus()
let bitmap = NSBitmapImageRep(data: image.tiffRepresentation!)!
try bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
''', encoding="utf-8")
        subprocess.run(["xcrun", "swift", "-module-cache-path", str(work / "icon-cache"), str(icon_source), str(work / "icon.png")], check=True)
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                filename = f"icon_{size}x{size}" + ("@2x" if scale == 2 else "") + ".png"
                subprocess.run(["sips", "-z", str(size * scale), str(size * scale), str(work / "icon.png"),
                                "--out", str(iconset / filename)], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(resources / "Training.icns")], check=True)
        info = {"CFBundleName": "ドギド 学習教材", "CFBundleDisplayName": "ドギド 学習教材",
                "CFBundleIdentifier": "local.dogido.training-builder", "CFBundleExecutable": executable.name,
                "CFBundlePackageType": "APPL", "CFBundleShortVersionString": "0.1.0", "CFBundleVersion": "1",
                "CFBundleIconFile": "Training.icns", "LSMinimumSystemVersion": "13.0", "NSHighResolutionCapable": True,
                "DogidoTrainingWorkspace": str(project / "dev_tools/request_classifier/教材作成"),
                "CFBundleDocumentTypes": [{"CFBundleTypeName": "学習用CSV", "CFBundleTypeRole": "Viewer",
                    "LSHandlerRank": "Alternate", "LSItemContentTypes": ["public.comma-separated-values-text"],
                    "CFBundleTypeExtensions": ["csv"]}]}
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
        subprocess.run(["codesign", "--force", "--sign", identity, "--timestamp=none", str(app)], check=True)
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(app, destination)
    print(destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=REPO)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.project_root.resolve(), args.output)
