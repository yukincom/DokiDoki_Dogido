"""公式 Fabric の起動バージョンだけを配置。アカウント・既存ワールドは扱わない。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

VERSION = "fabric-loader-0.18.4-1.21.11"


def same_launch_profile(installed: dict, bundled: dict) -> bool:
    # Fabric Meta generates these timestamps on request; the installer records its
    # own dates. They do not change the loader, game version, libraries or arguments.
    def launch_fields(profile: dict) -> dict:
        return {key: value for key, value in profile.items()
                if key not in {"time", "releaseTime"}}
    return launch_fields(installed) == launch_fields(bundled)


def write_instructions(root: Path) -> Path:
    game_dir = root / "minecraft"
    game_dir.mkdir(exist_ok=True)
    instructions = (
        "これはランチャーへ入力する値の案内メモです。導入の成否とは別に作成します。\n\n"
        "Minecraft Launcher → Java Edition → 起動構成 → 新規作成\n"
        "名前: 息子のドギド（任意）\n"
        f"バージョン: release {VERSION}\n"
        f"ゲームディレクトリ: {game_dir}\n"
        "\nゲームディレクトリに指定するのは上記の minecraft フォルダです。\n"
        "このテキストファイル自体のパスは指定しません。\n"
        "同じ版のFabricが既にあれば、それを選んで構いません。\n"
        "既存の起動構成を使う場合は、同梱 minecraft/mods のMODをその構成の mods へ配置できます。\n"
        "バージョンが出ない場合はランチャーを終了して起動し直し、MOD入りを表示してください。\n"
        "この起動構成でシングルプレイのワールドを開きます。\n"
    )
    path = root / "Minecraft設定.txt"
    path.write_text(instructions, encoding="utf-8")
    print(f"ランチャー用の案内メモ: {path}")
    print(instructions)
    return path


def install(root: Path, minecraft_dir: Path) -> None:
    # Instructions must remain available even if Fabric setup needs manual handling.
    write_instructions(root)
    source = root / "fabric-version" / VERSION
    profile = json.loads((source / f"{VERSION}.json").read_text(encoding="utf-8"))
    if not isinstance(profile, dict) or profile.get("id") != VERSION or profile.get("inheritsFrom") != "1.21.11":
        raise ValueError("同梱 Fabric のバージョンが一致しません")
    if not minecraft_dir.is_dir():
        raise ValueError("Minecraft Java のランチャーを一度起動してから再実行してください")
    destination = minecraft_dir / "versions" / VERSION
    installed_profile = destination / f"{VERSION}.json"
    installed_jar = destination / f"{VERSION}.jar"
    if installed_profile.exists():
        installed = json.loads(installed_profile.read_text(encoding="utf-8"))
        if not isinstance(installed, dict) or not same_launch_profile(installed, profile):
            raise ValueError(
                f"同じ名前の Fabric に異なる起動設定があります。既存設定は保持しました: {installed_profile}"
            )
        # The official Meta ZIP contains a zero-byte dummy JAR; existing installers
        # may retain a full game JAR. This profile inherits the game from 1.21.11.
        # Keep that existing JAR verbatim instead of comparing it with the dummy.
        print(f"インストール済みの Fabric をそのまま使います: {VERSION}")
    elif installed_jar.exists():
        raise ValueError(f"Fabric の JAR だけが存在します。既存ファイルは保持しました: {installed_jar}")
    destination.mkdir(parents=True, exist_ok=True)
    for name in (f"{VERSION}.json", f"{VERSION}.jar"):
        target = destination / name
        if not target.exists():
            with target.open("xb") as handle:
                handle.write((source / name).read_bytes())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--minecraft-dir", type=Path,
                        default=Path.home() / "Library/Application Support/minecraft")
    parser.add_argument("--instructions-only", action="store_true",
                        help="案内メモだけ作成し、Minecraftのインストール先を変更しない")
    args = parser.parse_args()
    try:
        root = Path(__file__).resolve().parents[1]
        if args.instructions_only:
            write_instructions(root)
        else:
            install(root, args.minecraft_dir)
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error
