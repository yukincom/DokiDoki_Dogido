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

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--sync-research", action="store_true", help="研究用SSD保全だけを一回実行")
    parser.add_argument("--research-root", type=Path, help="研究用保存先を明示設定（--sync-researchと併用）")
    args = parser.parse_args()
    os.chdir(ROOT)
    settings = get_settings()
    binary = ROOT / "dogido-rust/target/release/dogido-rust"
    if not binary.is_file():
        raise SystemExit("本棚用の実行ファイルがありません。先に本体をビルドしてください。")
    config = {"memory": str(settings.memory_dir.resolve()), "memory_enabled": settings.memory_enabled,
              "photos": str(Path.home() / "Pictures/ドギド"), "ui": str(ROOT / "tools/dogido-library")}
    if settings.capture_dir is not None:
        config["capture_dir"] = str(settings.capture_dir.expanduser().resolve())
    if settings.minecraft_screenshots_dir is not None:
        config["screenshots"] = str(settings.minecraft_screenshots_dir.expanduser().resolve())
    if args.research_root is not None and not args.sync_research:
        parser.error("--research-root は --sync-research と併用してください")
    command = [str(binary), "sync-bookshelf-research" if args.sync_research else "serve-bookshelf", "--config", json.dumps(config, ensure_ascii=False)]
    if args.sync_research:
        if args.research_root is not None:
            command += ["--root", str(args.research_root.absolute())]
    else:
        command += ["--port", str(args.port)]
    os.execv(str(binary), command)

if __name__ == "__main__":
    main()
