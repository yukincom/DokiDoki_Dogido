"""Minecraft・録音・TTSを使わない、既存ローカル会話モデルのテキスト評価。"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys


DEFAULT_CASES = Path(__file__).resolve().parents[2] / "tests/fixtures/language_dialogue/cases.json"


def read_cases(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data["cases"]
    if data["schema_version"] != 1 or len({c["id"] for c in cases}) != len(cases):
        raise ValueError("試験データの版またはIDが不正")
    for case in cases:
        for turn in case["turns"]:
            if "control" in turn:
                if turn["control"] not in {"interrupt", "release"}:
                    raise ValueError("未定義の制御入力")
            elif not isinstance(turn.get("text"), str) or not turn["text"].strip():
                raise ValueError("空の試験発話")
    return cases


def run_cases(cases, llm, emit):
    from .controller import LanguageDialogue

    for case in cases:
        dialogue = LanguageDialogue(llm)
        for index, turn in enumerate(case["turns"]):
            if "control" in turn:
                result = getattr(dialogue, turn["control"])()
            else:
                # 問題ID・採点基準・将来発話はモデル入力から分離。
                result = dialogue.turn(
                    turn["text"], turn_id=f"t{index + 1}", source=turn.get("source", "text")
                )
                expected = turn.get("expect_status")
                result["automatic_check"] = (
                    "not_specified"
                    if expected is None
                    else "pass"
                    if result["status"] in expected
                    else "fail"
                )
                result["expected_status"] = expected
            emit({"case_id": case["id"], "turn_index": index, "human_review": "pending", **result})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--list", action="store_true", help="モデルを読み込まず試験を列挙")
    parser.add_argument("--output", type=Path, help="新規の結果ディレクトリ（既存は上書きしない）")
    args = parser.parse_args(argv)
    cases = read_cases(args.cases)
    unknown = set(args.case) - {c["id"] for c in cases}
    if unknown:
        parser.error("未定義のcase: " + ",".join(sorted(unknown)))
    cases = [c for c in cases if not args.case or c["id"] in args.case]
    if args.list:
        for case in cases:
            print(case["id"], len(case["turns"]), case["origin"])
        return 0
    if args.output is None:
        parser.error("試験記録用に --output 新規ディレクトリ を指定してください")

    # キャッシュ済みモデルだけ。外部API・自動ダウンロードへ切り替えない。
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    from dogido_server.config import Settings
    from dogido_server.llm.client import DogidoLLM

    settings = Settings().llm_route_settings("chat")
    if settings.llm_backend != "mlx":
        parser.error("この試験CLIは既存mlx会話モデル専用です。設定ファイルは変更しません。")
    llm = DogidoLLM(settings)
    print(f"会話モデル: {settings.mlx_model_id}（音声・Minecraft・外部通信なし）", flush=True)
    if not llm.preload():
        print(f"モデル利用不可: {llm.disabled_reason()}", file=sys.stderr)
        return 2
    args.output.mkdir(parents=True, exist_ok=False)
    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": settings.mlx_model_id,
        "case_ids": [c["id"] for c in cases],
        "kind": "local_model_text_test",
        "human_review": "pending",
        "note": "機械検査は状態のみ。返答内容の正しさを保証しない。",
    }
    (args.output / "run.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    failures = []
    with (
        (args.output / "turns.jsonl").open("w", encoding="utf-8") as logs,
        (args.output / "transcript.md").open("w", encoding="utf-8") as transcript,
    ):
        transcript.write(
            "# 国語対話テキスト試験\n\n自動合否は状態遷移の検査のみ。内容は人手確認待ち。\n\n"
        )

        def emit(row):
            logs.write(json.dumps(row, ensure_ascii=False) + "\n")
            logs.flush()
            if row.get("automatic_check") == "fail":
                failures.append((row["case_id"], row["turn_index"]))
            header = f"{row['case_id']} / {row['turn_index'] + 1}"
            text = row.get("raw_text", row.get("control", ""))
            reply = row.get("reply") or f"[{row.get('status', '制御入力')}]"
            passage = f"## {header}\n\n> 入力：{text}\n\n> ドギド：{reply}\n\n"
            passage += f"状態：{row.get('status', '')} / {row.get('duration_ms', 0)} ms\n\n"
            if row.get("interpretation"):
                i = row["interpretation"]
                passage += f"解釈：{i['question']}（{i['target_status']}）\n\n"
            for fact in row.get("references", []):
                passage += f"- 資料 `{fact['id']}`：{fact['text_ja']}\n"
                for source in fact.get("sources", []):
                    passage += f"  - [{source['title_ja']}]({source['url']})\n"
            transcript.write(passage + "\n")
            transcript.flush()
            print(header, text, "→", reply, f"({row.get('duration_ms', 0)}ms)", flush=True)

        run_cases(cases, llm, emit)
    print(
        f"完了: {len(cases)}会話、状態検査の不一致 {len(failures)} 件。回答内容は別途確認。",
        flush=True,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
