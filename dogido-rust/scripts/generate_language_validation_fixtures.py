#!/usr/bin/env python3
"""移植直前のhelperとPython正本から検査fixtureを採取。モデル・サーバーは起動しない。"""
import copy
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
BASE = "b39e7ddbdde3491ac9712c4b031cde09810d863e"


def main():
    # 本番に旧検査を二重実装せず、移植直前のGit checkpointを比較元として固定する。
    source = subprocess.check_output(["git", "show", f"{BASE}:dogido-rust/scripts/language_helper.py"], cwd=ROOT, text=True)
    reference = {}
    exec(compile(source, "checkpoint/language_helper.py", "exec"), reference)
    handle = reference["handle"]
    current = {"turn_id":"current", "text":"きょうの音数を教えて", "source":"voice"}
    payload = {"dialogue_act":"information_request", "question":current["text"], "target":"きょう",
        "facet":"mora_count", "topic":"language", "relation":"new", "target_status":"explicit",
        "alternatives":[], "evidence":[{"turn_id":"current", "quote":current["text"]}],
        "search_terms":[], "clarification":""}
    details = {"current":current, "history":[], "focus":{"question":"","target":"","clarification":"","alternatives":[]}}
    state = {"focus":copy.deepcopy(details["focus"]), "kanji_scope_confirmed":False}
    cases = []

    def add(name, p=None, d=None, s=None, raw=None, finish="stop"):
        cmd = {"command":"interpretation", "details":copy.deepcopy(d or details), "state":copy.deepcopy(s or state),
               "generated":{"text":raw if raw is not None else json.dumps(payload if p is None else p, ensure_ascii=False), "finish_reason":finish}}
        expected = handle(cmd); expected.pop("stage")
        cases.append({"name":name, "command":cmd, "expected":expected})

    add("default")
    for key, values in {
        "dialogue_act":["casual","other","unknown"],
        "facet":["grade","reading","meaning","spelling","grammar","usage","etymology","translation","comparison","classification","other","unknown"],
        "topic":["minecraft","general","unclear","unknown"],
        "relation":["continue","correct","switch","end","resume","unknown"],
        "target_status":["contextual","ambiguous","unknown"],
        "lookup_requested":[True,False,0,1,"true",None],
        "evidence":[[], [{"turn_id":"absent","quote":"きょう"}], [{"turn_id":"current","quote":"あした"}],
            [{"turn_id":"current","quote":""}], [{"turn_id":"current","quote":"きょう","extra":1}],
            [{"turn_id":1,"quote":"きょう"}], [{"turn_id":"current","quote":1}]],
        "__dogido_status":["accepted","anything"], "close_workshop":[True],
    }.items():
        for n, value in enumerate(values): add(f"{key}:{n}", {**payload,key:value})
    for key in payload:
        for value in [None, True, 5, {}, ["wrong"]]: add(f"type:{key}:{value}", {**payload,key:value})
        missing = copy.deepcopy(payload); missing.pop(key); add(f"missing:{key}", missing)
    for key, limit in [("question",500),("target",100),("clarification",160),("web_query",140)]:
        for n in [limit,limit+1]: add(f"length:{key}:{n}", {**payload,key:"🐕"*n})
    for key, limit in [("alternatives",3),("search_terms",4),("evidence",4)]:
        for n in [limit,limit+1]: add(f"items:{key}:{n}", {**payload,key:([payload["evidence"][0]] if key=="evidence" else ["語"])*n})
    for n in [500,501]:
        d=copy.deepcopy(details); d["current"]["text"]="犬"*n
        add(f"quote-length:{n}", {**payload,"evidence":[{"turn_id":"current","quote":"犬"*n}]}, d)
    encoded=json.dumps(payload,ensure_ascii=False)
    for raw in ["", "oops", "{}", "[]", "null", encoded[:-2], "```json\n"+encoded+"\n```", "説明:"+encoded+"終わり", "["+encoded+"]", "{bad:"+encoded, "{\"wrapper\":"+encoded+"}"]:
        add("json:"+raw[:12], raw=raw)
    for finish in ["length","max_tokens","MAX_TOKENS",None,"error"]: add(f"finish:{finish}", finish=finish)
    for text in ["こんにちは","どういうこと","何","数字がある","漢字","誰？","読み","まだかな","なぜ","教えて"]:
        d=copy.deepcopy(details); d["current"]["text"]=text
        add("fallback:"+text,d=d,raw="invalid")
    for text,target in [("Ａの意味を教えて","A"),("㌔の意味を教えて","キロ"),("ガの意味を教えて","ガ")]:
        d=copy.deepcopy(details);d["current"]["text"]=text
        p={**payload,"facet":"meaning","target":target,"target_status":"contextual","relation":"continue",
            "evidence":[{"turn_id":"current","quote":target}]}
        add("nfkc:"+text,p,d)
        add("unknown-context:"+text,{**p,"target":"未出現の言葉"},d)
    for relation in ["new","continue","correct","switch","end","resume"]:
        for historical in [False,True]:
            d=copy.deepcopy(details); d["history"]=[{"turn_id":"old","role":"assistant","text":"漢字の三のこと？"}]
            p={**payload,"facet":"other","target":"未出現の言葉","target_status":"contextual","relation":relation}
            if historical: p["evidence"]=[*payload["evidence"],{"turn_id":"old","quote":"三"}]
            add(f"context-switch:{relation}:{historical}",p,d)
    for text in ["3は何年生？","数字の3は？","字の3は？","文字の3は？","漢字の3は？","かんじの3は？"]:
        for target_status in ["explicit","contextual"]:
            for confirmed in [False,True]:
                d=copy.deepcopy(details); d["current"]["text"]=text
                s={**state,"kanji_scope_confirmed":confirmed}
                p={**payload,"target":"3","facet":"grade","relation":"continue","target_status":target_status,
                    "evidence":[{"turn_id":"current","quote":text}]}
                add(f"grade:{text}:{target_status}:{confirmed}",p,d,s)
    for writing in ["それって、漢字を習う学年のこと？","別の問い"]:
        d=copy.deepcopy(details); d["current"]["text"]="3のこと";d["focus"]["clarification"]=writing
        p={**payload,"target":"3","facet":"grade","target_status":"contextual","relation":"continue",
           "evidence":[{"turn_id":"current","quote":"3のこと"}]}
        add("grade-focus:"+writing,p,d)
    for quote in ["漢字の三","数字の3"]:
        d=copy.deepcopy(details); d["history"]=[{"turn_id":"old","text":quote}];d["current"]["text"]="それは？"
        p={**payload,"target":"三","facet":"grade","target_status":"contextual","relation":"continue",
           "evidence":[{"turn_id":"current","quote":"それは？"},{"turn_id":"old","quote":quote}]}
        add("grade-history:"+quote,p,d)
    for kana in ["きょう","がっこう","ヴァイオリン","ｷｮｳ","㌔","きゃー","ティー","が","っ","ん","ぃ","ーあ","ゐ","ゑ","くゎ","ヶ","ヵ","ゞ","ゝ","かな？","かな かな","春","犬🐕","\x1cキョウ\x1f","","キョー",*map(chr, range(0x3041,0x3097))]:
        d=copy.deepcopy(details); d["current"]["text"]=f"音数を教えて。{kana}"
        add("kana:"+kana,{**payload,"target":kana,"evidence":[{"turn_id":"current","quote":d["current"]["text"]}]},d)
    d=copy.deepcopy(details);d["history"]=[{"turn_id":"current","text":"きょう"}];d["current"]["text"]="別の発話"
    add("duplicate-turn-current-wins",d=d)

    replies=[]
    reply={"status":"answer","text":"資料に基づく説明。","fact_ids":["local:1"],"application":"資料を使う。","missing":""}
    def add_reply(name,p=None,raw=None,finish="stop"):
        generated={"text":raw if raw is not None else json.dumps(reply if p is None else p,ensure_ascii=False),"finish_reason":finish}
        replies.append({"name":name,"generated":generated,"expected":handle({"command":"reply","generated":generated})["payload"]})
    add_reply("default")
    for key in reply:
        for value in [None,True,5,{},["wrong"]]:add_reply(f"type:{key}:{value}",{**reply,key:value})
        missing=copy.deepcopy(reply);missing.pop(key);add_reply("missing:"+key,missing)
    for key,values in {"status":["partial","unsupported","invalid"],"fact_ids":[[],["x"]*6,["x"]*7,[1]],
        "missing_kind":["none","evidence","context","invalid",None],"clarification":["",None],
        "__dogido_status":["accepted"],"references":[[]],"text":[""]}.items():
        for n,v in enumerate(values):add_reply(f"{key}:{n}",{**reply,key:v})
    for key,limit in [("text",420),("application",300),("missing",200),("clarification",160)]:
        for n in [limit,limit+1]:add_reply(f"length:{key}:{n}",{**reply,key:"🐕"*n})
    for finish in ["length","max_tokens","MAX_TOKENS",None]:add_reply("finish:"+str(finish),finish=finish)
    for raw in ["bad","{}","[]","null", "```json\n"+json.dumps(reply)+"\n```"]:add_reply("json:"+raw[:10],raw=raw)
    result={"reference_commit":BASE,"interpretations":cases,"replies":replies}
    path=ROOT/"dogido-rust/fixtures/language-validation.json"
    path.write_text("{\n\"reference_commit\":"+json.dumps(BASE)+",\n"+",\n".join(
        json.dumps(key)+":[\n"+",\n".join(json.dumps(row,ensure_ascii=False,separators=(",",":")) for row in result[key])+"\n]"
        for key in ["interpretations","replies"])+"\n}\n")
    print(f"{len(cases)} interpretations / {len(replies)} replies from {BASE[:7]}")


if __name__ == "__main__":main()
