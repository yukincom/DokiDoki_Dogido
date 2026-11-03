"""Macの実バンドル・署名・Finderエイリアスを一時領域で確認する。GUI、LAN、Minecraft、VOICEVOXは起動しない。"""
import importlib.util
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "scripts/lan_apps"
spec = importlib.util.spec_from_file_location("lan_apps_install", SOURCE / "install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS application bundles and aliases")


@pytest.fixture(scope="module")
def binary(tmp_path_factory):
    path = tmp_path_factory.mktemp("launcher") / installer.LAUNCHER
    installer.build_launcher(path)
    return path


@pytest.fixture()
def prepared(tmp_path, monkeypatch, binary):
    root = tmp_path / "source/scripts"
    here = root / "lan_apps"
    here.mkdir(parents=True)
    shutil.copy(binary, here / installer.LAUNCHER)
    (here / "icons").mkdir()
    for role in ("host", "client"):
        shutil.copy(SOURCE / "icons" / f"{role}.icns", here / "icons")
    monkeypatch.setattr(installer, "HERE", here)
    for name in ("lan/connection.py", "lan/transport.py", "lan/discovery.py", "family-link/gateway.py",
                 "family-link/log_relay.py", "family-link/minecraft_log_relay.py",
                 "family-link/start_client.py", "family-client/check.py", "family-client/family_runtime.py"):
        helper = root / name
        helper.parent.mkdir(exist_ok=True)
        helper.write_text("# pretend runtime module")
    apps, desktop = tmp_path / "Applications", tmp_path / "Desktop"
    apps.mkdir()
    desktop.mkdir()
    return apps, desktop


# 見込み: 共有Macでは本体アプリとFinderエイリアスだけで起動場所が固定される。
# 合格: 一時領域で生成・署名したappをエイリアスが解決し、繰返し導入でも両者の実体を保持する。共有サービス・GUIは起動しない。
def test_host_alias_and_idempotent_install(prepared):
    app, alias = installer.install("host", *prepared)
    initial = (app.stat().st_ino, alias.stat().st_ino)
    assert app.parent == prepared[0] and alias.parent == prepared[1] and not alias.is_symlink()
    subprocess.run([str(app / "Contents/MacOS/DogidoLauncher"), "--check-alias", alias, app], check=True)
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    assert info["NSBonjourServices"] == ["_dogido._tcp"]
    assert info["CFBundleIconFile"] == "host.icns"
    assert (app / "Contents/Resources/host.icns").read_bytes() == (SOURCE / "icons/host.icns").read_bytes()
    installer.install("host", *prepared)
    assert (app.stat().st_ino, alias.stat().st_ino) == initial


# 見込み: 必須部品をそろえたappで更新すれば新しい通信処理とエイリアスの宛先が食い違わない。
# 合格: 二回目に変えた通信部品をapp内に確認し、Finderエイリアスが更新後appを解決する。一時ファイルのみ。
def test_owned_update_refreshes_alias(prepared):
    app, alias = installer.install("host", *prepared)
    (installer.HERE.parent / "lan/transport.py").write_text("# newer module")
    installer.install("host", *prepared)
    assert (app / "Contents/Resources/lan/transport.py").read_text() == "# newer module"
    subprocess.run([str(app / "Contents/MacOS/DogidoLauncher"), "--check-alias", alias, app], check=True)


# 見込み: 他のアプリやデスクトップの同名ファイルを識別すれば、既存データを保ったまま導入を止められる。
# 合格: 配置衝突を明示してbeforeと同じ内容を保ち、未完成のappを残さない。一時ディレクトリで確認。
@pytest.mark.parametrize("occupied", ["app", "alias"])
def test_unrelated_destination_is_preserved(prepared, occupied):
    apps, desktop = prepared
    app, alias = apps / "ドギド共有.app", desktop / "ドギド共有"
    existing = app if occupied == "app" else alias
    existing.write_bytes(b"independent")
    with pytest.raises(ValueError, match="同名"):
        installer.install("host", apps, desktop)
    assert existing.read_bytes() == b"independent"
    assert not (alias if occupied == "app" else app).exists()


# 見込み: 遊ぶMacのappには実行本体を内蔵し、個人のワールド・記憶と設定は準備済みkitで継続して使う。
# 合格: 模擬kitから本体を内蔵し、署名済みappの設定がkitのPythonを指す。VOICEVOX・本体の起動は別試験。
def test_client_embeds_runtime_and_targets_kit_python(tmp_path, prepared):
    kit = tmp_path / "test client"
    runtime = kit / "dogido-rust/target/release/dogido-rust"
    python = kit / "dogido-llm/bin/python"
    helper = kit / "client_tools/family_runtime.py"
    for path in (runtime, python, helper):
        path.parent.mkdir(parents=True)
        path.write_text("# test-only")
    helper.write_text("def validate_runtime(root): pass")
    python.chmod(0o755)
    app, _ = installer.install("client", *prepared, kit)
    resources = app / "Contents/Resources"
    assert plistlib.loads((app / "Contents/Info.plist").read_bytes())["CFBundleIconFile"] == "client.icns"
    assert (resources / "client.icns").read_bytes() == (SOURCE / "icons/client.icns").read_bytes()
    config = plistlib.loads((resources / "launch.plist").read_bytes())
    assert config == {"kit": str(kit.resolve()), "python": str(kit.resolve() / "dogido-llm/bin/python")}
    assert (resources / "runtime/dogido-rust").read_bytes() == runtime.read_bytes()
    assert (resources / "runtime/dogido-rust").stat().st_mode & 0o111
    assert (resources / "client_tools/start_client.py").is_file()
