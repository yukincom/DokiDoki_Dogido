"""Minecraft・録音・TTSを使わない、既存ローカル会話モデルのテキスト評価。"""

import argparse
from contextlib import nullcontext
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys


DEFAULT_CASES = Path(__file__).resolve().parents[2] / "tests/fixtures/language_dialogue/cases.json"
DEFAULT_VIRTUAL_WEB = DEFAULT_CASES.with_name("virtual_overview.json")
FOCUS_CONTROLS = {"minecraft_away": False, "minecraft_active": True}
PLAYBACK_CONTROLS = {"speech_completed": "completed", "speech_cancelled": "cancelled", "speech_failed": "failed"}


def read_cases(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data["cases"]
    if data["schema_version"] != 1 or len({c["id"] for c in cases}) != len(cases):
        raise ValueError("試験データの版またはIDが不正")
    for case in cases:
        for turn in case["turns"]:
            if "control" in turn:
                if turn["control"] not in {"interrupt", "release", "cancel", *FOCUS_CONTROLS, *PLAYBACK_CONTROLS}:
                    raise ValueError("未定義の制御入力")
            elif not isinstance(turn.get("text"), str) or not turn["text"].strip():
                raise ValueError("空の試験発話")
    return cases


def apply_control(dialogue, control, *, event_id, utterance_id=""):
    if control in FOCUS_CONTROLS:
        return dialogue.observe_minecraft_focus(FOCUS_CONTROLS[control], event_id=event_id)
    if control in PLAYBACK_CONTROLS:
        if not utterance_id:
            return {"control": control, "status": "no_speech_to_finish", "reply": "", "simulation": True}
        return {**dialogue.on_speech_playback_result(utterance_id, status=PLAYBACK_CONTROLS[control],
                                                   event_id=event_id), "simulation": True}
    return getattr(dialogue, control)()


def check_status(result, expected):
    result["expected_status"] = expected
    result["automatic_check"] = (
        "not_specified" if expected is None else "pass" if result.get("status") in expected else "fail"
    )


def check_expectations(result, turn):
    check_status(result, turn.get("expect_status"))
    if turn.get("expect_web_read"):
        web = result.get("web", {})
        pages = web.get("pages", [])
        valid = (web.get("status") == "read" and bool(pages)
                 and web.get("context_page_ids") == [p["id"] for p in pages])
        result["web_read_check"] = "pass" if valid else "fail"
        if not valid:
            result["automatic_check"] = "fail"
    if turn.get("expect_context") and not result.get("context_page_ids"):
        result["automatic_check"] = "fail"
    if turn.get("expect_mode") and result.get("mode_after") != turn["expect_mode"]:
        result["automatic_check"] = "fail"
    if "expect_web_trigger" in turn:
        actual = result.get("web", result.get("web_proposal", {})).get("trigger_reason", "")
        matched = actual == turn["expect_web_trigger"]
        result["web_trigger_check"] = "pass" if matched else "fail"
        if not matched:
            result["automatic_check"] = "fail"


def run_cases(cases, llm, emit, *, dialogue_factory=None):
    from .controller import LanguageDialogue

    for case in cases:
        dialogue = dialogue_factory() if dialogue_factory else LanguageDialogue(llm)
        utterance_id = ""
        for index, turn in enumerate(case["turns"]):
            if "control" in turn:
                result = apply_control(dialogue, turn["control"], event_id=f"event{index + 1}",
                                       utterance_id=utterance_id)
                check_expectations(result, turn)
                emit({"case_id": case["id"], "turn_index": index, **result})
                # 先に歓迎を配送する。取得待ちで「おかえり」を遅らせない。
                if result.get("refresh_token"):
                    refreshed = dialogue.refresh_after_return(result["refresh_token"])
                    check_status(refreshed, turn.get("expect_refresh_status"))
                    emit({"case_id": case["id"], "turn_index": index, "substep": "refresh", **refreshed})
                continue
            else:
                # 問題ID・採点基準・将来発話はモデル入力から分離。
                result = dialogue.turn(
                    turn["text"], turn_id=f"t{index + 1}", source=turn.get("source", "text")
                )
                if result.get("speech"):
                    utterance_id = result["speech"]["utterance_id"]
                check_expectations(result, turn)
            emit({"case_id": case["id"], "turn_index": index, "human_review": "pending", **result})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--list", action="store_true", help="モデルを読み込まず試験を列挙")
    web_options = parser.add_mutually_exclusive_group()
    web_options.add_argument("--web", action="store_true", help="専用Chromeで公開資料を検索・取得する（外部通信あり）")
    web_options.add_argument("--virtual-web", type=Path, nargs="?", const=DEFAULT_VIRTUAL_WEB,
                             help="外部通信なし。明示したfixtureを復帰後に仮想再生（省略時は取得済み金床概要）")
    parser.add_argument("--interactive", action="store_true", help="端末で対話する。/quitで終了、/cancelで話題を打切り")
    parser.add_argument("--school-grade", type=int, choices=range(1, 7), default=3,
                        help="既知資料の診断モードで使う教材の対象学年。既定は3年生")
    parser.add_argument("--known-sources-only", action="store_true", help="診断用: Googleを呼ばず、既知の公式出典の本文取得だけ試す")
    parser.add_argument("--output", type=Path, help="新規の結果ディレクトリ（既存は上書きしない）")
    args = parser.parse_args(argv)
    if args.known_sources_only and not args.web:
        parser.error("--known-sources-only は --web と併用してください")
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
    if args.virtual_web:
        from .virtual_overview import VirtualOverviewResearch
        VirtualOverviewResearch(args.virtual_web)  # モデル初期化前にfixtureを検査。

    # キャッシュ済みモデルだけ。外部API・自動ダウンロードへ切り替えない。
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    from dogido_server.config import Settings
    from dogido_server.llm.client import DogidoLLM

    settings = Settings().llm_route_settings("chat")
    if settings.llm_backend != "mlx":
        parser.error("この試験CLIは既存mlx会話モデル専用です。設定ファイルは変更しません。")
    llm = DogidoLLM(settings)
    communication = (
        "外部通信あり・既知資料の診断モード" if args.known_sources_only else
        "外部通信あり・Chromeの検索ページとAI概要を共有" if args.web else
        "外部通信なし・仮想Webと模擬Minecraft復帰" if args.virtual_web else "外部通信なし"
    )
    print(f"会話モデル: {settings.mlx_model_id}（音声・Minecraftなし、{communication}）", flush=True)
    if not llm.preload():
        print(f"モデル利用不可: {llm.disabled_reason()}", file=sys.stderr)
        return 2
    args.output.mkdir(parents=True, exist_ok=False)
    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": settings.mlx_model_id,
        "case_ids": [] if args.interactive else [c["id"] for c in cases],
        "kind": "local_model_text_test",
        "human_review": "pending",
        "note": "機械検査は状態のみ。返答内容の正しさを保証しない。",
        "web_enabled": args.web,
        "virtual_web": str(args.virtual_web) if args.virtual_web else None,
        "focus_events": "simulated_not_os_observed",
        "playback_events": "simulated_not_device_observed",
        "known_sources_only": args.known_sources_only,
        "school_grade": args.school_grade,
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
        if args.virtual_web:
            transcript.write("仮想Webの概要再生と模擬復帰イベント。実ブラウザー・Minecraft・音声は使用していません。\n\n")

        def emit(row):
            logs.write(json.dumps(row, ensure_ascii=False) + "\n")
            logs.flush()
            if row.get("automatic_check") == "fail":
                failures.append((row["case_id"], row["turn_index"]))
            header = f"{row['case_id']} / {row['turn_index'] + 1}"
            text = row.get("raw_text", row.get("control", ""))
            reply = row.get("reply") or f"[{row.get('status', '制御入力')}]"
            passage = f"## {header}\n\n> 入力：{text}\n\n> ドギド：{reply}\n\n"
            duration = f"{row['duration_ms']} ms" if "duration_ms" in row else "未計測（制御入力）"
            passage += f"状態：{row.get('status', '')} / {duration}\n\n"
            if row.get("interpretation"):
                i = row["interpretation"]
                passage += f"解釈：{i['question']}（{i['target_status']}）\n\n"
            if row.get("web"):
                web = row["web"]
                passage += f"Web：{web['trigger_reason']} / {web['status']} / 文脈へ渡したページ {web.get('context_page_ids', [])}\n\n"
            if row.get("context_page_ids"):
                passage += f"返答の入力にあるページ：{row['context_page_ids']}\n\n"
            if row.get("web_refresh"):
                reread = row["web_refresh"]
                label = "仮想概要の再生" if args.virtual_web else "同じタブの再読"
                passage += f"{label}：{reread['status']} / {reread['search_status']}（再検索なし）\n\n"
            for fact in row.get("references", []):
                passage += f"- 資料 `{fact['id']}`：{fact['text_ja']}\n"
                for source in fact.get("sources", []):
                    passage += f"  - [{source['title_ja']}]({source['url']})\n"
            transcript.write(passage + "\n")
            transcript.flush()
            print(header, text, "→", reply, f"({duration})", flush=True)

        from .controller import LanguageDialogue
        from .chrome_web import ChromeWebClient
        from .web_research import WebResearch
        from .google_overview import GoogleOverviewResearch

        def web_event(event):
            print("WEB " + json.dumps(event, ensure_ascii=False), flush=True)

        with (
            ChromeWebClient() if args.known_sources_only else nullcontext() as client,
            ChromeWebClient(child_view=True, overview_search=not args.known_sources_only)
            if args.web else nullcontext() as child_client,
        ):
            web = (
                WebResearch(client, known_sources_only=True,
                            child_client=child_client, school_grade=args.school_grade)
                if args.known_sources_only else GoogleOverviewResearch(child_client) if args.web else None
            )
            factory = lambda: LanguageDialogue(
                llm, web=VirtualOverviewResearch(args.virtual_web) if args.virtual_web else web,
                on_event=web_event,
            )
            if args.interactive:
                dialogue = factory()
                print("入力してください。/quitで終了、/cancelで話題を打切り。", flush=True)
                print("模擬復帰: /minecraft-away → /minecraft-active（実ゲームの前面検知ではありません）", flush=True)
                print("音声は再生しません。案内の模擬完了は /speech-completed、模擬失敗は /speech-failed。", flush=True)
                index = 0
                utterance_id = ""
                while True:
                    try:
                        text = input("あなた > ").strip()
                    except (EOFError, KeyboardInterrupt):
                        break
                    if text == "/quit":
                        break
                    if not text:
                        continue
                    control = text[1:].replace("-", "_") if text.startswith("/") else ""
                    if control in {"cancel", "interrupt", "release", *FOCUS_CONTROLS, *PLAYBACK_CONTROLS}:
                        row = apply_control(dialogue, control, event_id=f"event{index + 1}",
                                            utterance_id=utterance_id)
                    else:
                        row = dialogue.turn(text, turn_id=f"t{index + 1}")
                        if row.get("speech"):
                            utterance_id = row["speech"]["utterance_id"]
                    emit({"case_id": "interactive", "turn_index": index, **row})
                    if row.get("refresh_token"):
                        refreshed = dialogue.refresh_after_return(row["refresh_token"])
                        emit({"case_id": "interactive", "turn_index": index, "substep": "refresh", **refreshed})
                    index += 1
            else:
                run_cases(cases, llm, emit, dialogue_factory=factory)
    count = f"対話入力 {index} 件" if args.interactive else f"{len(cases)}会話"
    print(
        f"完了: {count}、状態検査の不一致 {len(failures)} 件。回答内容は別途確認。",
        flush=True,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
