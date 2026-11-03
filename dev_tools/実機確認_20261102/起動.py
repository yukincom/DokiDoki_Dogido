"""2026-11-02の実機テスト専用。書類側の設定で既存ランチャーを実行する。"""
import hashlib
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT.parent.parent  # 書類側の .dogido_tools/abcdef-playtest-20261102
JAR = "dogido-fabric-client-0.1.0.jar"
NORMAL = "aef328603fc9d27a70323f4b92e4b50b4900ae78d30bab4085f76f3aa64f0106"
PREVIOUS_TRIAL = "789c388735f3f6db680e072ba0842fbcec7da4dce9b9df1d710bee4af7921716"
TRIAL = "97e7bbdab93758030fbee0a15b7d049ff314a531057f604f63a637d6230a6187"


def read_known(path, allowed):
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest not in allowed:
        raise ValueError(f"確認済みのModと異なるため変更しませんでした: {path}")
    return data, digest


def switch_mod(game, restore=False):
    installed = game / "mods" / JAR
    _, current = read_known(installed, {NORMAL, PREVIOUS_TRIAL, TRIAL})
    target = NORMAL if restore else TRIAL
    if current == target:
        return
    # 通常版が更新されたら別のjarを上書きしない。復元用には控えてある同一ビルドだけを使う。
    normal, _ = read_known(ROOT / ".dogido_tools/通常版Mod" / JAR, {NORMAL})
    data = normal if restore else read_known(ROOT / "adapter/minecraft-fabric/build/libs" / JAR, {TRIAL})[0]
    with tempfile.NamedTemporaryFile(dir=installed.parent, suffix=".tmp", delete=False) as f:
        tmp = Path(f.name)
    try:
        tmp.write_bytes(data)
        tmp.chmod(installed.stat().st_mode & 0o777)
        os.replace(tmp, installed)
    finally:
        tmp.unlink(missing_ok=True)


def main(mode, args):
    python = MAIN / "dogido-llm/bin/python"
    if not python.is_file() or ROOT.parent.name != ".dogido_tools":
        raise ValueError("書類側のDokiDoki-Dogidoに配置した実機テスト版から起動してください。")
    game = Path.home() / "Library/Application Support/minecraft"
    if mode == "restore":
        switch_mod(game, restore=True)
        print("通常版のModです。次のMinecraft起動から反映します。")
        return
    if mode not in {"server", "no-image", "voice"}:
        raise ValueError("起動の種類を確認してください。")
    if mode != "voice":
        if "--check" not in args:
            switch_mod(game)
            print("テスト版のModを選びました。Minecraftはこの後で起動してください。", flush=True)
        os.environ["DOGIDO_CHAT_IMAGE_ENABLED"] = "false" if mode == "no-image" else "true"
    launch = ROOT / "dogido-rust/scripts/launch_dialogue.py"
    os.chdir(MAIN)
    os.execv(python, [str(python), str(launch), "--settings-dir", str(MAIN),
                      *(["--voice"] if mode == "voice" else []), *args])


if __name__ == "__main__":
    try:
        main(sys.argv[1], sys.argv[2:])
    except (OSError, ValueError) as error:
        sys.exit(str(error))
