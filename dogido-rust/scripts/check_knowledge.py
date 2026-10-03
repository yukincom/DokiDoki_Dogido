#!/usr/bin/env python3
"""固定した知識回答契約と模擬HTTP配送。実LLM・実音声は使用しない。"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from check_dialogue import ROOT, dependencies, register, request, row, running, snapshot, submit, wait_for

FIXTURES = ROOT / "fixtures/knowledge-render-contract.json"


def contracts():
    payload = json.loads(FIXTURES.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    return payload


def renderer_contracts(binary, directory):
    path = directory / "lookup.json"
    cases = contracts()["cases"]
    for case in cases:
        path.write_text(json.dumps(case["input"], ensure_ascii=False), encoding="utf-8")
        actual = json.loads(subprocess.check_output([str(binary), "render-knowledge", str(path)], text=True))
        assert actual == case["expected"], (case["name"], actual, case["expected"])
    return len(cases)


def integration(binary, directory):
    passed = []
    with dependencies() as (dep, control, seen), running(binary, directory, dep) as (base, process, log):
        sid = register(base)
        question = "枕詞って何？"
        expected = contracts()["integration"]["makurakotoba"]
        turn = submit(base, sid, question)
        reply = wait_for(lambda: row(base, turn, {"completed"}))
        assert reply["text"] == expected["text"] and reply["category"] == "knowledge", reply
        assert reply["llm_reports"] == [] and reply["knowledge_status"] == "found", reply
        assert not any(r["path"] == "/v1/chat/completions" for r in seen), seen
        view = request(base, f"/api/v1/display/snapshot?session_id={sid}")
        assert view["references"][0]["url"] == expected["reference_url"], view
        assert view["references"][0]["utterance_ids"] == [reply["utterance_id"]], view
        assert reply["reference_ids"] == [r["reference_id"] for r in view["references"]]
        synthesized = [r["body"]["test_text"] for r in seen if r["path"].startswith("/synthesis")]
        assert synthesized and all("https://" not in text and "文部科学省" not in text for text in synthesized)
        passed.append("canonical_knowledge_without_model_and_separate_references")

        other = register(base)
        assert request(base, f"/api/v1/display/snapshot?session_id={other}")["references"] == []
        turn = submit(base, sid, question)
        repeat = wait_for(lambda: row(base, turn, {"completed"}))
        view = request(base, f"/api/v1/display/snapshot?session_id={sid}")
        assert len(view["references"]) == 1
        assert view["references"][0]["utterance_ids"] == [reply["utterance_id"], repeat["utterance_id"]]
        passed.append("references_deduplicate_and_follow_selected_session")

        for question, expected_text in contracts()["integration"]["not_found_questions"].items():
            turn = submit(base, sid, question)
            reply = wait_for(lambda: row(base, turn, {"completed"}))
            assert reply["text"] == expected_text, reply
            assert reply["reference_ids"] == [] and reply["knowledge_status"] == "not_found", reply
        assert not any(r["path"] == "/v1/chat/completions" for r in seen)
        passed.append("ambiguous_kanji_wrong_version_missing_fact_never_generate")

        question = "ダイヤモンドの剣のIDは？"
        turn = submit(base, sid, question)
        reply = wait_for(lambda: row(base, turn, {"completed"}))
        status = reply["knowledge_status"]
        assert status in {"found", "not_found", "unavailable"}, reply
        if status == "found":
            assert "minecraft:diamond_sword" in reply["text"], reply
            references = request(base, f"/api/v1/display/snapshot?session_id={sid}")["references"]
            selected = [r for r in references if r["reference_id"] in reply["reference_ids"]]
            assert selected and all(r["source_id"].startswith("minecraft:official_") for r in selected), selected
        else:
            assert reply["text"] == contracts()["integration"][status] and reply["reference_ids"] == [], reply
        assert not any(r["path"] == "/v1/chat/completions" for r in seen)
        passed.append("minecraft_reader_or_missing_database_reply_without_model")

        control["fail_tts"] = True
        turn = submit(base, sid, "川柳の決まりを教えて")
        wait_for(lambda: row(base, turn, {"failed"}))
        history = next(s["history"] for s in snapshot(base)["sessions"] if s["session_id"] == sid)
        assert not any(r["turn_id"] == turn + ":reply" and r["role"] == "assistant" for r in history)
        control["fail_tts"] = False
        passed.append("failed_audio_does_not_commit_assistant_history")

        # 文ごとの既存cacheを避け、未再生の知識回答を途中で止める。
        (directory / "player_mode").write_text("slow")
        turn = submit(base, sid, "ソネットの形式は？")
        wait_for(lambda: row(base, turn, {"started"}))
        request(base, "/api/v1/rust-dialogue/interrupt", {"session_id": sid})
        wait_for(lambda: row(base, turn, {"cancelled"}))
        history = next(s["history"] for s in snapshot(base)["sessions"] if s["session_id"] == sid)
        assert not any(r["turn_id"] == turn + ":reply" and r["role"] == "assistant" for r in history)
        (directory / "player_mode").write_text("ok")
        passed.append("cancelled_knowledge_stops_owned_audio_without_history")

        # 学習の続きではない別sessionの雑談に、国語分類を増やさない。
        turn = submit(base, other, "こんにちは")
        wait_for(lambda: row(base, turn, {"completed"}))
        assert len([r for r in seen if r["path"] == "/v1/chat/completions"]) == 2, seen
        passed.append("ordinary_chat_still_one_planner_one_leaf")
    return passed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/dogido-rust")
    parser.add_argument("--renderer-only", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="dogido-knowledge-") as temp:
        directory = Path(temp)
        count = renderer_contracts(args.binary.resolve(), directory)
        passed = [] if args.renderer_only else integration(args.binary.resolve(), directory)
    print(json.dumps({"renderer_contracts": count, "http_checks": passed}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
