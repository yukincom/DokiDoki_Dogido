#!/usr/bin/env python3
"""本体の詩の基礎知識と、専用国語対話へ分岐しない境界を模擬HTTPで確認する。"""
import json
import os
from pathlib import Path
import tempfile

from check_dialogue import ROOT, dependencies, register, row, running, snapshot, submit, wait_for


def main():
    passed = []
    with tempfile.TemporaryDirectory(prefix="dogido-poetry-scope-") as temp, dependencies() as (dep, control, seen):
        # 詩の基礎は出典付きの即答、一般的な質問は通常の一回生成。
        with running(Path(os.environ.get("DOGIDO_RUST_BINARY", ROOT / "target/debug/dogido-rust")), Path(temp), dep) as (base, _, log):
            sid = register(base)
            def calls():
                return [r["body"] for r in seen if r["path"] == "/v1/chat/completions"]
            def say(text):
                turn = submit(base, sid, text)
                return wait_for(lambda: row(base, turn, {"completed"}))
            def focus():
                return next(s["foreground"] for s in snapshot(base)["sessions"] if s["session_id"] == sid)

            for question in ("枕詞って何？", "川柳の決まりを教えて", "ソネットの形式は？"):
                count = len(calls())
                reply = say(question)
                assert reply["category"] == "knowledge" and reply["knowledge_status"] == "found", reply
                assert len(calls()) == count and reply["reference_ids"], reply
            passed.append("basic_poetry_uses_saved_sources_without_model")

            for question in ("こんにちは", "漢字の3は何年生で習うの？", "国語で活用とは？",
                             "音数を教えて。きょう", "漢字の3のことだよ"):
                count = len(calls())
                reply = say(question)
                assert len(calls()) == count + 1, reply
                assert not reply.get("reference_ids"), reply
                assert focus()["route"] == "casual" and not focus()["blocks_new_haiku"], focus()
            passed.append("ordinary_and_general_questions_use_one_chat_generation")
    print(json.dumps({"checks": passed, "owned_processes_stopped": True}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
