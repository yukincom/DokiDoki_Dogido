"""国語対話のprompt・正本検索補助。解釈・回答検査・計算・状態はRustが所有する。"""
from dataclasses import asdict


def explicit_request(text):
    text = " ".join(text.split())
    if any(p in text for p in ("ってどういう意味", "って何て読む", "ってなんて読む", "の読み方",
                              "言葉の意味", "ことばの意味", "何年生で習", "何年生の漢字")):
        return True
    return any(p in text for p in ("漢字", "短歌", "俳句", "川柳", "音数", "文法", "熟語", "ことわざ", "枕詞", "語句")) and any(
        p in text for p in ("?", "？", "教えて", "知りたい", "調べて", "勉強", "習う", "習った", "話をしよう", "話しよう", "話そう"))


def handle(command):
    op = command["command"]
    if op == "prompt":
        # 通常雑談の入口判定では、国語用コンポーネントを読み込まない。
        from dogido_server.language_dialogue.prompts import (
            build_interpretation_messages, build_grounded_reply_messages,
        )
        from dogido_server.llm.types import StructuredGenerationRequest

        kind = command["kind"]
        build = {"language_dialogue_interpretation": build_interpretation_messages,
                 "language_dialogue_reply": build_grounded_reply_messages}[kind]
        request = StructuredGenerationRequest(kind=kind, details=command["details"], fallback_value={}, route="chat")
        return {"stage":"prompt", "messages":build(request)}
    if op == "lookup":
        from dogido_server.language_dialogue.contracts import Interpretation
        from dogido_server.language_dialogue.retrieval import LocalDialogueSearch, SearchResult
        from dogido_server.language_dialogue.verified_answers import verified_reply

        i = Interpretation.model_validate(command["interpretation"], strict=True)
        computed = command.get("computed_fact")
        lookup = SearchResult([], [computed], "computed") if computed else LocalDialogueSearch().search(i.search_terms, facet=i.facet, target=i.target)
        text = command["text"]
        if i.facet == "comparison" and len(i.target) == 1 and text.count(i.target) >= 2:
            lookup.facts.append({"id":f"input-character:{ord(i.target):x}", "title_ja":"入力文字の比較",
                "text_ja":f"今回の入力に同一文字『{i.target}』が{text.count(i.target)}回ある。文字列比較の結果。",
                "claim_status":"input_character_comparison", "sources":[]})
        fixed = verified_reply(i, lookup.facts)
        return {"stage":"lookup", "lookup":asdict(lookup), "fixed_reply":fixed.model_dump() if fixed else None}
    raise ValueError("unsupported language helper command")


def run(exchange):
    command = exchange({"op":"language", "stage":"start"})
    while command["command"] != "done":
        command = exchange({"op":"language", **handle(command)})
    return command
