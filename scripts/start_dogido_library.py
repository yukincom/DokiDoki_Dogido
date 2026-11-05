#!/usr/bin/env python3
"""既存設定だけを読み、本棚専用Rustプロセスへ引き渡す。ゲーム・音声・LLMは起動しない。"""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dogido_server.config import get_settings


def parse_bootstrap(data):
    """Rustの既存bootstrapのJSONだけを読み、JavaScriptを実行しない。"""
    prefix = "window.DOGIDO_BOOTSTRAP="
    text = data.decode("utf-8")
    if not text.startswith(prefix):
        raise ValueError("本棚の応答を確認できませんでした。")
    value, _ = json.JSONDecoder().raw_decode(text[len(prefix):])
    return value


def child_session(command, environment, admin_token):
    """このアプリの本棚と家庭内接続だけを所有し、終了時に両方を止める。"""
    import signal
    import subprocess
    import threading
    from urllib.request import urlopen
    sys.path.insert(0, str(ROOT / "scripts/lan"))
    from bookshelf_bridge import serve_child
    stop = threading.Event()
    child = subprocess.Popen(command, env=environment, stdout=subprocess.PIPE, text=True)
    def finish(*_):
        stop.set()
        if child.poll() is None:
            child.send_signal(signal.SIGINT)
    signal.signal(signal.SIGTERM, finish)
    signal.signal(signal.SIGINT, finish)
    worker = None
    try:
        for line in child.stdout:
            if line.startswith("DOGIDO_LIBRARY_URL="):
                url = line.strip().split("=", 1)[1]
                with urlopen(url + "bootstrap.js", timeout=15) as response:
                    boot = parse_bootstrap(response.read())
                worker = threading.Thread(target=serve_child, args=(stop, url, admin_token, boot["csrf"]), daemon=True)
                worker.start()
            print(line, end="", flush=True)
        return child.wait()
    finally:
        finish()
        # 保存中の原本writerを時間だけで強制終了しない。SIGINT後のHTTP処理完了を待つ。
        child.wait()
        if worker:
            worker.join(timeout=3)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--profile", choices=("self", "son", "child"), default="self")
    parser.add_argument("--device", help="親の本棚が開く登録済みの遊ぶMac ID")
    parser.add_argument("--sync-research", action="store_true", help="研究用SSD保全だけを一回実行")
    parser.add_argument("--research-root", type=Path, help="研究用保存先を明示設定（--sync-researchと併用）")
    args = parser.parse_args()
    os.chdir(ROOT)
    if args.profile == "son":
        if args.sync_research or args.research_root:
            parser.error("息子用の本棚は研究データを複製しません。")
        from bookshelf_remote import run
        run(ROOT, args.port, args.device)
        return
    if args.profile == "child":
        if args.sync_research or args.research_root:
            parser.error("この本棚から研究データの保存先は変えられへんねん。")
        os.environ["DOGIDO_ENV_PROFILE"] = "shared"
    settings = get_settings()
    # 設定先がない場合に空の別本棚を作って、既存の句を見失わせない。
    if not settings.memory_dir.is_dir():
        raise SystemExit("句の保存場所が見つからへんねん。本棚を閉じて、保存場所を確認してな。" if args.profile == "child"
                         else "川柳の保存先が見つかりません。既存の保存先を接続してください。")
    primary_env = settings.primary_storage_env()
    binary = ROOT / "dogido-rust/target/release/dogido-rust"
    if not binary.is_file():
        raise SystemExit("本棚用の実行ファイルがありません。先に本体をビルドしてください。")
    config = {"memory": str(settings.memory_dir.resolve()), "memory_enabled": settings.memory_enabled,
              "game_port": settings.bind_port,
              "photos": str(settings.memory_dir / "library-photos" if primary_env else Path.home() / "Pictures/ドギド"), "ui": str(ROOT / "tools/dogido-library")}
    readout_assets = ROOT / ".dogido_tools/j-lens/assets.json"
    if readout_assets.is_file():
        assets = json.loads(readout_assets.read_text())
        config["j_lens"] = {"python": str(Path(assets.get("python", sys.executable)).expanduser()),
                            "worker": str(ROOT / "dev_tools/haiku_internal_probe/gguf_jlens.py"),
                            "assets": str(readout_assets)}
    publish_settings = ROOT / ".dogido_tools/research-repository.json"
    # 公開提供は保留中。既存の明示設定がある環境だけ接続し、従来設定はprivateを維持する。
    if publish_settings.is_file():
        publish = json.loads(publish_settings.read_text())
        config["data_repository"] = {"python": str(Path(publish.get("python", sys.executable)).expanduser()),
            "worker": str(ROOT / "scripts/publish_bookshelf_data.py"),
            "checkout": str(Path(publish["checkout"]).expanduser().absolute()), "mode": publish.get("mode", "private")}
    if settings.capture_dir is not None:
        config["capture_dir"] = str(settings.capture_dir.expanduser().resolve())
    if settings.minecraft_screenshots_dir is not None:
        config["screenshots"] = str(settings.minecraft_screenshots_dir.expanduser().resolve())
    if args.profile == "child":
        import secrets
        config["child_mode"] = True
        config["admin_token"] = secrets.token_urlsafe(32)
        config.pop("data_repository", None)
        config.pop("j_lens", None)
    if args.research_root is not None and not args.sync_research:
        parser.error("--research-root は --sync-research と併用してください")
    command = [str(binary), "sync-bookshelf-research" if args.sync_research else "serve-bookshelf", "--config", json.dumps(config, ensure_ascii=False)]
    if args.sync_research:
        if args.research_root is not None:
            command += ["--root", str(args.research_root.absolute())]
    else:
        command += ["--port", str(args.port)]
    environment = {**os.environ, **primary_env}
    if args.profile == "child":
        raise SystemExit(child_session(command, environment, config["admin_token"]))
    os.execve(str(binary), command, environment)

if __name__ == "__main__":
    main()
