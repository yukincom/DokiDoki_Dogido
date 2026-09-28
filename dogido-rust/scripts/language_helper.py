"""国語対話の入口・正本検索補助。prompt・回答・検査・計算・状態はRustが所有する。"""
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
    if op == "lookup":
        from dogido_server.language_dialogue.contracts import Interpretation
        from dogido_server.language_dialogue.retrieval import LocalDialogueSearch

        i = Interpretation.model_validate(command["interpretation"], strict=True)
        lookup = LocalDialogueSearch().search(i.search_terms, facet=i.facet, target=i.target)
        return {"stage":"lookup", "lookup":asdict(lookup)}
    raise ValueError("unsupported language helper command")


def run(exchange):
    command = exchange({"op":"language", "stage":"start"})
    while command["command"] != "done":
        command = exchange({"op":"language", **handle(command)})
    return command
