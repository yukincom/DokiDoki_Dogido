#!/usr/bin/env python3
"""移植直前のprompt・確定回答を採取。モデル・音声・サーバーを起動しない。"""
import copy
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
BASE = "91eb1d9"


def main():
    source = subprocess.check_output(
        ["git", "show", f"{BASE}:dogido-rust/scripts/language_helper.py"], cwd=ROOT, text=True)
    reference = {}
    exec(compile(source, "checkpoint/language_helper.py", "exec"), reference)
    from dogido_server.language_dialogue.contracts import Interpretation
    from dogido_server.language_dialogue.verified_answers import verified_reply

    prompts = []
    assets = {}
    variants = [
        {},
        {"current":{"turn_id":"t1","text":"音数を教えて。きょう","source":"voice"},
         "history":[],"mode":"normal","focus":{},"situation_history":""},
        {"history":[{"turn_id":"past","role":"assistant","text":"どの字のこと？"}],
         "current":{"turn_id":"t2","text":"『三』やで\n\"3\" \\ 🐕","source":"typed"},
         "focus":{"target":"三","clarification":"漢字のこと？","alternatives":["数","漢字"]},
         "situation_history":"敵の接近で中断", "mode":"language"},
        {"current":{"text":"枕詞の意味を教えて","turn_id":"t3"},
         "interpretation":{"target":"枕詞","facet":"meaning","lookup_requested":False},
         "facts":[{"id":"local:1","text_ja":"入力の資料","sources":[]}],"search_status":"searched"},
        {"focus":{"target":"ことば"}, "facts":[], "search_status":"unavailable"},
    ]
    for kind in ["language_dialogue_interpretation", "language_dialogue_reply"]:
        for details in variants:
            messages = reference["handle"]({"command":"prompt","kind":kind,"details":details})["messages"]
            assets[kind] = messages[0]["content"]
            prompts.append({"kind":kind,"details":details,"expected":messages})
    # 表や計算の値を答える範囲だけを比較し、意味説明には広げない。
    replies = []
    base = {"dialogue_act":"information_request","question":"対象のことを教えて", "target":"三",
        "facet":"grade","topic":"language","relation":"new","target_status":"explicit",
        "alternatives":[],"evidence":[{"turn_id":"t1","quote":"三"}],"search_terms":["三"],"clarification":""}
    def add(name, target, facet, facts):
        interpretation = {**base,"target":target,"facet":facet}
        result = verified_reply(Interpretation.model_validate(interpretation,strict=True), facts)
        replies.append({"name":name,"interpretation":interpretation,"facts":facts,
                        "expected":result.model_dump() if result else None})
    fact = {"id":"table:3","sources":[],"allocation":{"character":"三","school_grade":1,"scope":"character_only"}}
    for target in ["三","3","３","三年"," 3","3 ","㊂","Ⅲ","","零","🐕"]:
        add("grade-target:"+target,target,"grade",[fact])
    for grade in [None,False,True,0,1,2,3,4,5,6,7,-1,1.0,"1"]:
        f=copy.deepcopy(fact);f["allocation"]["school_grade"]=grade
        add("grade-value:"+repr(grade),"三","grade",[f])
    for field,values in {"character":["二","三年",None],"scope":["word","reading",None]}.items():
        for value in values:
            f=copy.deepcopy(fact);f["allocation"][field]=value
            add(f"allocation:{field}:{value}","三","grade",[f])
    for field in ["character","school_grade","scope"]:
        f=copy.deepcopy(fact);f["allocation"].pop(field)
        add("allocation-missing:"+field,"三","grade",[f])
    add("no-facts","三","grade",[])
    add("no-allocation","三","grade",[{"id":"vocabulary:3"}])
    add("first-matching-table","三","grade",[{"id":"unrelated"},fact,{**fact,"id":"second"}])
    for facet in ["reading","meaning","comparison","classification","other"]:
        add("not-grade:"+facet,"三",facet,[fact])
    for target,surface in [("きょう","きょう"),(" キョウ ","キョウ"),("ｷｮｳ","キョウ"),("\x1cきょう\x1f","きょう"),("きょう","きのう")]:
        f={"id":"calculation:test","calculation":{"operation":"mora_count","surface":surface,"value":2}}
        add("mora:"+repr(target)+":"+surface,target,"mora_count",[f])
    add("wrong-operation","きょう","mora_count",[{"id":"other","calculation":{"operation":"other","surface":"きょう","value":2}}])
    # 比較factを追加する既存helperも固定checkpointから採取する。
    comparisons=[]
    for target,text in [("三","三と三は同じ？"),("三","三と三と三"),("三","三と二"),("３","３と3"),("a","aaa"),("🐕","🐕と🐕"),("三三","三三と三三"),("","同じ？")]:
        i={**base,"facet":"comparison","target":target,"search_terms":[]}
        result=reference["handle"]({"command":"lookup","interpretation":i,"text":text})
        facts=[f for f in result["lookup"]["facts"] if f.get("claim_status")=="input_character_comparison"]
        comparisons.append({"target":target,"text":text,"expected":facts})
    (ROOT/"dogido-rust/src/language/prompts.json").write_text(json.dumps(assets,ensure_ascii=False,indent=2)+"\n")
    # 差分で各ケースを一件ずつ確認できる形にする。
    sections = [f'  "checkpoint": "{BASE}"']
    for name, cases in [("prompts",prompts),("replies",replies),("comparisons",comparisons)]:
        sections.append(f'  "{name}": [\n' + ",\n".join(
            "    "+json.dumps(case,ensure_ascii=False) for case in cases) + "\n  ]")
    (ROOT/"dogido-rust/fixtures/language-preparation.json").write_text("{\n"+",\n".join(sections)+"\n}\n")
    print(f"Captured {len(prompts)} prompts, {len(replies)} fixed replies, {len(comparisons)} comparisons")


if __name__ == "__main__":
    main()
