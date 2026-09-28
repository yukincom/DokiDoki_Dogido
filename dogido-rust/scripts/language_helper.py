"""国語対話の正本検索補助。入口候補・prompt・回答・検査・計算・状態はRustが所有する。"""
from dataclasses import asdict


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
