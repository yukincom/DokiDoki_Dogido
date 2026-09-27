#!/usr/bin/env python3
"""知識回答のPython等値比較と模擬HTTP配送。実LLM・実音声は使用しない。"""
from dataclasses import asdict, replace
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from check_dialogue import ROOT, dependencies, register, request, row, running, snapshot, submit, wait_for

sys.path.insert(0, str(ROOT.parent))
from dogido_server.knowledge_query import (
    LocalKnowledgeProvider, KnowledgeLookupResult, extract_explicit_knowledge_query,
    render_knowledge_reply_plan,
)


def lookup(text):
    query = extract_explicit_knowledge_query(text)
    assert query is not None, text
    return LocalKnowledgeProvider().lookup(query)


def parity(binary, directory):
    cases = [lookup(text) for text in (
        "枕詞って何？", "川柳の決まりを教えて", "ソネットの形式は？", "ソネットとは？",
        "一二三の一は何年生で習うの？", "漢字の3は何年生で習うの？", "一の読みを教えて",
        "～間の接続は？", "国語で幻の枕詞って何？", "Minecraft 1.20.1のダイヤモンドの剣のIDは？",
        "ダイヤモンドの剣のIDは？", "ダイヤモンドの剣の耐久値は？",
    )]
    base = cases[0]
    assert base.status == "found", base
    fact = base.facts[0]
    for status in ("not_found", "unavailable"):
        cases.append(KnowledgeLookupResult(base.query, status))
    # 出典・外形の不正、句読点・Unicode長、220/420字境界を旧rendererと比較する。
    for body in ("短い。" + "長" * 250, "あ" * 219, "あ" * 220, "あ" * 221,
                 "あ" * 190, "！？" + "あ" * 300, " 一文。 二文！ 三文？", "\n説明\n  の本文\t"):
        for count in (1, 2, 3):
            cases.append(replace(base, facts=tuple(replace(fact, text_ja=body, dialogue_text_ja="",
                sources=tuple(replace(source, locator=f"{i}頁") for source in fact.sources)) for i in range(count))))
    cases.extend([
        replace(base, facts=()), replace(base, status="not_found"),
        replace(base, facts=(replace(fact, claim_status="fabricated"),)),
        replace(base, facts=(replace(fact, sources=(replace(fact.sources[0], url="http://untrusted.test"),)),)),
        replace(base, facts=(replace(fact, text_ja="あ" * 1001),)),
        replace(base, facts=(replace(fact, dialogue_text_ja="  "),)),
    ])
    # 技術DBの生成キャッシュがなくても、公式Web/配布物の出典契約を比較する。
    minecraft_query = replace(base.query, domain="minecraft", subject="ダイヤモンドの剣",
                              intent="identifier", evidence="ダイヤモンドの剣のIDは？")
    for kind in ("official_web_page", "official_artifact"):
        source = replace(fact.sources[0], source_id="minecraft:official_java", source_kind=kind,
                         citation_label_ja="Minecraft公式リリースノート", locator="registry/item",
                         url="https://www.minecraft.net/example")
        minecraft_fact = replace(fact, dialogue_text_ja="", text_ja="minecraft:diamond_sword",
                                 claim_status="official_artifact", sources=(source,))
        candidate = replace(base, query=minecraft_query, facts=(minecraft_fact,))
        cases.append(candidate)
        bad_source = replace(source, url="https://www.minecraft.net.invalid/example") if kind == "official_web_page" else replace(source, locator="")
        cases.append(replace(candidate, facts=(replace(minecraft_fact, sources=(bad_source,)),)))
    path = directory / "lookup.json"
    for case in cases:
        path.write_text(json.dumps(asdict(case), ensure_ascii=False))
        actual = json.loads(subprocess.check_output([str(binary), "render-knowledge", str(path)], text=True))
        expected = json.loads(json.dumps(asdict(render_knowledge_reply_plan(case)), ensure_ascii=False))
        assert {key: actual[key] for key in expected} == expected, (case, actual, expected)
    return len(cases)


def integration(binary, directory):
    passed = []
    with dependencies() as (dep, control, seen), running(binary, directory, dep) as (base, process, log):
        sid = register(base)
        question = "枕詞って何？"
        expected = render_knowledge_reply_plan(lookup(question))
        turn = submit(base, sid, question)
        reply = wait_for(lambda: row(base, turn, {"completed"}))
        assert reply["text"] == expected.text and reply["category"] == "knowledge", reply
        assert reply["llm_reports"] == [] and reply["knowledge_status"] == "found", reply
        assert not any(r["path"] == "/v1/chat/completions" for r in seen), seen
        view = request(base, f"/api/v1/display/snapshot?session_id={sid}")
        assert view["references"][0]["url"] == expected.references[0].url, view
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

        for question in ("漢字の3は何年生で習うの？", "Minecraft 1.20.1のダイヤモンドの剣のIDは？",
                         "国語で幻の枕詞って何？"):
            turn = submit(base, sid, question)
            reply = wait_for(lambda: row(base, turn, {"completed"}))
            assert reply["text"] == render_knowledge_reply_plan(lookup(question)).text, reply
            assert reply["reference_ids"] == [] and reply["knowledge_status"] == "not_found", reply
        assert not any(r["path"] == "/v1/chat/completions" for r in seen)
        passed.append("ambiguous_kanji_wrong_version_missing_fact_never_generate")

        question = "ダイヤモンドの剣のIDは？"
        turn = submit(base, sid, question)
        reply = wait_for(lambda: row(base, turn, {"completed"}))
        record = lookup(question)
        assert reply["text"] == render_knowledge_reply_plan(record).text
        assert reply["knowledge_status"] == record.status
        assert not any(r["path"] == "/v1/chat/completions" for r in seen)
        passed.append("minecraft_reader_or_missing_database_reply_without_model")

        control["fail_tts"] = True
        turn = submit(base, sid, "川柳の決まりを教えて")
        wait_for(lambda: row(base, turn, {"failed"}))
        history = next(s["history"] for s in snapshot(base)["sessions"] if s["session_id"] == sid)
        assert not any(r["turn_id"] == turn and r["role"] == "assistant" for r in history)
        control["fail_tts"] = False
        passed.append("failed_audio_does_not_commit_assistant_history")

        # 文ごとの既存cacheを避け、未再生の知識回答を途中で止める。
        (directory / "player_mode").write_text("slow")
        turn = submit(base, sid, "ソネットの形式は？")
        wait_for(lambda: row(base, turn, {"started"}))
        request(base, "/api/v1/rust-dialogue/interrupt", {"session_id": sid})
        wait_for(lambda: row(base, turn, {"cancelled"}))
        history = next(s["history"] for s in snapshot(base)["sessions"] if s["session_id"] == sid)
        assert not any(r["turn_id"] == turn and r["role"] == "assistant" for r in history)
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
    parser.add_argument("--parity-only", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="dogido-knowledge-") as temp:
        directory = Path(temp)
        count = parity(args.binary.resolve(), directory)
        passed = [] if args.parity_only else integration(args.binary.resolve(), directory)
    print(json.dumps({"parity": count, "http_checks": passed}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
