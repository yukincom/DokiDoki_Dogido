import argparse
import json
from pathlib import Path

from .core import Lab, read_json


def main():
    parser = argparse.ArgumentParser(description="Dogido Prompt Lab: buildなしのプロンプト比較")
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--directory", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    ui = commands.add_parser("serve"); ui.add_argument("--port", type=int, default=8791)
    commands.add_parser("list")
    prepare = commands.add_parser("prepare"); prepare.add_argument("spec", type=Path)
    run = commands.add_parser("run"); run.add_argument("batch_id")
    worker = commands.add_parser("worker"); worker.add_argument("run_id"); worker.add_argument("--cancel", type=Path, required=True)
    result = commands.add_parser("result"); result.add_argument("run_id")
    export = commands.add_parser("export"); export.add_argument("run_id"); export.add_argument("--output", type=Path, required=True)
    note = commands.add_parser("note"); note.add_argument("--author", default="User"); note.add_argument("--run-id"); note.add_argument("body")
    args = parser.parse_args()
    lab = Lab(args.repo, args.directory)
    if args.command == "serve":
        from .server import serve
        serve(lab, args.port)
    elif args.command == "list":
        print(json.dumps({"candidates": lab.candidates(), "models": lab.models(), "suites": lab.suites()}, ensure_ascii=False, indent=2))
    elif args.command == "prepare":
        print(json.dumps(lab.prepare(read_json(args.spec)), ensure_ascii=False, indent=2))
    elif args.command == "run":
        from .runner import execute_batch
        execute_batch(lab, args.batch_id)
    elif args.command == "worker":
        from .worker import execute_run, watch_parent
        watch_parent()
        execute_run(lab, args.run_id, args.cancel)
    elif args.command == "result":
        print(json.dumps(lab.run(args.run_id), ensure_ascii=False, indent=2))
    elif args.command == "export":
        args.output.write_text(lab.export_html(args.run_id), encoding="utf-8")
        print(args.output)
    elif args.command == "note":
        print(json.dumps(lab.add_note({"author": args.author, "run_id": args.run_id, "body": args.body}), ensure_ascii=False))


if __name__ == "__main__":
    main()
