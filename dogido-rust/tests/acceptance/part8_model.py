#!/usr/bin/env python3
"""パート8の実モデル測定。実Rustの入力・native tool・保存を通し、生の要求応答を残す。自動検査だけでは意味の改善を合格にせず、結果の内容レビューを別に要求する。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import urllib.request

CASES = Path(__file__).with_name("part8_model_cases.json")


def read_rows(path):
    return ([json.loads(line) for line in path.read_text().splitlines(keepends=True)
             if line.endswith("\n")] if path.is_file() else [])


def shared_context(request):
    """最終要求中の共有文脈をJSONとして読む。属性名の単語一致だけを合格にしない。"""
    found = []
    decoder = json.JSONDecoder()
    for message in request["messages"]:
        for line in (message.get("content") or "").splitlines():
            if not line.startswith("{"): continue
            try: value, _ = decoder.raw_decode(line)
            except json.JSONDecodeError: continue
            if isinstance(value, dict) and {"saved_line_sources", "current_verse", "source_atoms"} <= value.keys():
                found.append(value)
    assert len(found) == 1, "共有文脈が欠落または重複"
    return found[0]


def verify_materials(request, entry):
    context = shared_context(request)
    materials = entry["materials_snapshot"]
    expected = materials["source_atoms"]
    assert expected and materials["source_attribution"] == context["source_attribution"] == "provided_materials_unverified"
    assert context["line_sources_for"] == "current_verse"
    assert len(context["saved_line_sources"]) == len(entry["lines"]) == 3
    for saved, shown in zip(entry["lines"], context["saved_line_sources"]):
        assert shown["line_index"] == saved["line_index"] and shown["text"] == saved["surface_text"]
        assert shown["atom_ids"] == saved["source_atom_ids"] == [a["atom_id"] for a in expected]
        assert shown["provenance"] == saved["provenance"] == "generated_material_assignment"
    assert {a["atom_id"]: a["text"] for a in context["source_atoms"]} == {a["atom_id"]: a["text"] for a in expected}
    return context


def verify(step, record):
    """状態・保存・呼出しの実測だけを照合する。本文の意味は合格判定しない。"""
    before, after, result = record["before"], record["after"], record["result"]
    calls, trace = record["model_calls"], record["trace"]
    old, new = trace["state_before"], trace["state_after"]
    source_case = step["expect"] == "source_explanation"
    assert len(calls) == record["request_count"] and len(calls) in ([1, 2] if source_case else [1]), "追加生成またはfixtureへの迂回"
    assert all(c.get("response") and not c.get("error") for c in calls), "実モデル応答なし"
    if source_case:
        for call in calls: verify_materials(call["request"], before["entries"][0])
        if len(calls) == 2:
            native = calls[0]["response"]["choices"][0]["message"]["tool_calls"]
            assert len(native) == 1 and native[0]["function"]["name"] == "inspect_verse"
            tools = [m for m in calls[1]["request"]["messages"] if m["role"] == "tool"]
            assert len(tools) == 1 and tools[0]["tool_call_id"] == native[0]["id"]
            observation = json.loads(tools[0]["content"])
            assert "source" in observation["checks"] and len(observation["lines"]) == 3
            for observed, saved in zip(observation["lines"], before["entries"][0]["lines"]):
                assert observed["line_index"] == saved["line_index"]
                assert observed["source_status"] == "recorded" and observed["source_atom_ids"] == saved["source_atom_ids"]
    assert record["classifier_requests"] and len(record["classifier_requests"]) == 1
    classified = record["classifier_requests"][0]
    assert classified["mode"] == "senryu" and classified["text"].split("現在のユーザー発話:\n")[-1] == step["text"], "指定分類の前提不成立"
    assert result["playback_status"] == "completed" and trace["player_text"] == step["text"]
    assert old["workshop_id"] == new["workshop_id"] and new["open"]
    assert before["entries"] == after["entries"] and len(before["entries"]) == 1
    assert after["hud"]["canonical_lines"] == step["lines"] and not after["hud"]["pending_lines"]
    assert new["canonical"] == "\n".join(step["lines"]) and new["pending"] is None
    message = calls[-1]["response"]["choices"][0]["message"]
    if step["expect"] == "save":
        assert result["workshop_outcome"] == "player_edit_saved" and new["version"] == old["version"] + 1
        assert len(after["revisions"]) == len(before["revisions"]) + 1 and after["revisions"][:-1] == before["revisions"]
        saved = after["revisions"][-1]
        assert saved["id"] == result["workshop_revision_id"] and saved["revised_text"] == "\n".join(step["lines"])
        assert saved["haiku_id"] == before["entries"][0]["id"]
        assert saved["base_text"] == "\n".join(before["hud"]["canonical_lines"])
        assert saved["parent_revision_id"] == (before["revisions"][-1]["id"] if before["revisions"] else None)
        assert len(message.get("tool_calls", [])) == 1 and message["tool_calls"][0]["function"]["name"] == "stage_player_edit"
    else:
        assert new["version"] == old["version"] and before["revisions"] == after["revisions"]
        assert old["canonical"] == new["canonical"] and not message.get("tool_calls")
        if step["expect"] == "ask":
            assert json.loads(message["content"])["action"] == result["workshop_action"] == "ask"
            assert result["workshop_reason"] == "accepted" and new["player_idea"] is None
        elif source_case:
            assert json.loads(message["content"])["action"] == result["workshop_action"]
            assert result["workshop_action"] in {"respond", "explain"} and result["workshop_reason"] == "accepted"
            assert new["player_idea"] == old["player_idea"]
        else:
            assert new["player_idea"]["proposal"] == {"line_index": 0, "target_fragment": "さくらのは", "replacement_text": "さくらいろ"}


def main():
    spec = json.loads(CASES.read_text())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="ケース表示だけ。モデル・サービスは起動しない")
    parser.add_argument("--binary", type=Path, help="今回ビルドしたRust実行ファイル")
    parser.add_argument("--base-url", help="指定推論APIの /v1 までのURL")
    parser.add_argument("--model")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--case", choices=[c["id"] for c in spec["cases"]], action="append")
    args = parser.parse_args()
    if args.list:
        print(json.dumps(spec, ensure_ascii=False, indent=2)); return 0
    if not all([args.binary, args.base_url, args.model, args.output]):
        parser.error("測定には --binary --base-url --model --output を明示する")
    binary = args.binary.resolve()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        parser.error("--binary は実行可能なRustバイナリを指定する")
    os.environ["DOGIDO_RUST_BINARY"] = str(binary)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
    from check_dialogue import row, submit, wait_for
    from check_haiku_runtime import fixture
    from check_workshop_runtime import ready
    from request_classifier_fixture import mock_request_classifier
    failed = False
    with args.output.open("x") as output:
        def emit(value):
            output.write(json.dumps(value, ensure_ascii=False) + "\n"); output.flush()
        emit({"kind": "configuration", "model": args.model, "base_url": args.base_url,
              "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(), "cases_sha256": hashlib.sha256(CASES.read_bytes()).hexdigest(),
              "scope": "分類を固定した指定推論APIとRust保存経路。STT・BERT精度・Minecraft・実音声は未評価"})
        for case in spec["cases"]:
            if args.case and case["id"] not in args.case:
                continue
            calls = []
            try:
                labels = {s["text"]: s["label"] for s in case["steps"]}
                with mock_request_classifier(labels) as (bert_args, bert_dir), fixture(extra_args=bert_args,
                        initial_lines=spec["initial_lines"], workshop_idle_ms=300000) as state:
                    base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder = state
                    sid = ready(base, send, rows)
                    memory = folder / "memory/sessions" / sid / "long_term"
                    def capture():
                        return {"hud": hud(sid), "entries": stored(sid), "revisions": read_rows(memory / "haiku_revisions.jsonl")}
                    prepared = capture()
                    assert prepared["hud"]["canonical_lines"] == spec["initial_lines"]
                    entry = prepared["entries"][0]
                    atoms = entry["materials_snapshot"]["source_atoms"]
                    assert atoms and entry["materials_snapshot"]["source_attribution"] == "provided_materials_unverified"
                    assert all(line["source_atom_ids"] == [a["atom_id"] for a in atoms] for line in entry["lines"])
                    emit({"kind":"prepared_materials","case":case["id"],"entry":entry})
                    def forward(incoming):
                        outgoing = {**incoming, "model": args.model}
                        record = {"request": outgoing}; calls.append(record)
                        try:
                            assert not outgoing.get("stream"), "川柳以外のstream生成が混入"
                            req = urllib.request.Request(args.base_url.rstrip("/") + "/chat/completions",
                                json.dumps(outgoing, ensure_ascii=False).encode(), {"Content-Type": "application/json"})
                            with urllib.request.urlopen(req, timeout=75) as response:
                                record["raw_response"] = response.read().decode()
                                record["response"] = json.loads(record["raw_response"])
                            return record["response"]
                        except Exception as error:
                            record["error"] = str(error); raise  # Noneを返してfixtureへ戻さない。
                    control["structured_handler"] = lambda incoming: None
                    control["completion_override"] = forward
                    for step in case["steps"]:
                        first, first_seen = len(calls), len(seen)
                        classified_before = len(read_rows(bert_dir / "requests.jsonl"))
                        before = capture()
                        stop = threading.Event()
                        heartbeat_errors = []
                        def heartbeat():
                            while not stop.wait(.25):
                                try: send(sid)
                                except Exception as error: heartbeat_errors.append(str(error)); return
                        thread = threading.Thread(target=heartbeat); thread.start()
                        try:
                            turn = submit(base, sid, step["text"], source="voice")
                            result = wait_for(lambda: row(base, turn, {"completed", "failed", "cancelled"}), timeout=100)
                            trace = wait_for(lambda: next((r for r in read_rows(memory / "haiku_workshop_turns.jsonl")
                                if r["event_kind"] == "turn_result" and r["turn_id"] == turn), None), timeout=5)
                            record = {"kind": "step", "case": case["id"], "step": step, "before": before, "after": capture(),
                                "result": result, "trace": trace, "model_calls": calls[first:],
                                "request_count": sum(r["path"] == "/v1/chat/completions" for r in seen[first_seen:]),
                                "classifier_requests": read_rows(bert_dir / "requests.jsonl")[classified_before:]}
                            emit(record)
                            verify(step, record)
                            assert not heartbeat_errors, heartbeat_errors
                        finally:
                            stop.set(); thread.join(timeout=3); assert not thread.is_alive()
                emit({"kind": "result", "case": case["id"], "automatic_checks": "pass", "acceptance": "review_required", "review": case["review"]})
            except Exception as error:
                failed = True
                emit({"kind": "result", "case": case["id"], "automatic_checks": "fail", "error": f"{type(error).__name__}: {error}", "model_calls": calls})
    print("機械的な確認のみ。生の要求・応答とreview項目の内容確認が済むまで、補作の改善は未判定。")
    return 1 if failed else 2


if __name__ == "__main__":
    sys.exit(main())
