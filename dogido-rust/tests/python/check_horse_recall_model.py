#!/usr/bin/env python3
"""採用した二段階native APIを固定corpusで検証。実モデルを呼ぶがMinecraft操作は行わない。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import time
import urllib.request

def probe(binary, row):
    result = subprocess.run([str(binary)], input=json.dumps(row, ensure_ascii=False) + "\n",
                            text=True, capture_output=True, check=True)
    value = json.loads(result.stdout)
    if "error" in value and "kind" not in value:
        raise RuntimeError(value)
    return value

def summary(rows):
    rows = [r for r in rows if not r["warmup"]]
    positive = [r for r in rows if r["expected"] is not None]
    negative = [r for r in rows if r["expected"] is None]
    times = sorted(r["elapsed_ms"] for r in rows)
    return {"trials":len(rows), "correct":sum(r["correct"] for r in rows),
            "positive_trials":len(positive), "positive_correct":sum(r["correct"] for r in positive),
            "no_action_trials":len(negative), "no_action_correct":sum(r["correct"] for r in negative),
            "false_dispatch":sum(r["outcome"].get("kind")=="recall" for r in negative),
            "wrong_target":sum(r["outcome"].get("kind")=="recall" and not r["correct"] for r in positive),
            "rejected":sum(r["outcome"].get("kind")=="rejected" for r in rows),
            "median_ms":round(statistics.median(times),2), "p95_ms":times[int((len(times)-1)*.95+.5)],
            "positive_median_ms":round(statistics.median(r["elapsed_ms"] for r in positive),2),
            "llm_calls":sum(len(r["calls"]) for r in rows),
            "prompt_tokens":sum(c["response"].get("usage",{}).get("prompt_tokens",0) for r in rows for c in r["calls"]),
            "completion_tokens":sum(c["response"].get("usage",{}).get("completion_tokens",0) for r in rows for c in r["calls"]),
            "failures":[{"case":r["case_id"],"seed":r["seed"],"outcome":r["outcome"]} for r in rows if not r["correct"]]}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--probe",type=Path,required=True)
    p.add_argument("--corpus",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--base-url",default="http://127.0.0.1:8080/v1")
    p.add_argument("--model",default="lmstudio-community/gemma-4-26B-A4B-it-QAT-MLX-4bit")
    args=p.parse_args()
    raw=args.corpus.read_bytes()
    corpus=[json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    args.output.mkdir(parents=True,exist_ok=False)
    seeds=[101,202,303]
    manifest={"model":args.model,"base_url":args.base_url,"seeds":seeds,"case_count":len(corpus),
        "corpus":args.corpus.name,"corpus_sha256":hashlib.sha256(raw).hexdigest(),
        "probe_sha256":hashlib.sha256(args.probe.read_bytes()).hexdigest(),
        "temperature":.65,"max_tokens_per_call":192,"thinking":False,"transport":"native",
        "retry":False,"max_calls_per_trial":2,"first_call_candidates":False,
        "execution":"decode_and_validate_only","started_at":datetime.now(timezone.utc).isoformat()}
    (args.output/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n")
    (args.output/"cases.json").write_text(json.dumps(corpus,ensure_ascii=False,indent=2)+"\n")
    rows=[]

    def one(case,seed,warmup=False):
        base={**case,"model":args.model}
        calls=[]
        total_start=time.monotonic()
        for stage in ("intent","selection"):
            request=probe(args.probe,{**base,"op":"request","stage":stage})
            request["seed"]=seed
            start=time.monotonic()
            req=urllib.request.Request(args.base_url.rstrip("/")+"/chat/completions",
                json.dumps(request,ensure_ascii=False).encode(),{"Content-Type":"application/json"})
            try:
                with urllib.request.urlopen(req,timeout=40) as response: body=json.load(response)
                elapsed=round((time.monotonic()-start)*1000,2)
                outcome=probe(args.probe,{**base,"op":"evaluate","stage":stage,"response":body})
            except Exception as error:
                outcome={"kind":"error","error":str(error)}
                body={}
                elapsed=round((time.monotonic()-start)*1000,2)
            calls.append({"stage":stage,"request":request,"response":body,"outcome":outcome,"elapsed_ms":elapsed})
            if outcome.get("kind")!="list":break
        expected=case["expected"]
        correct=(outcome.get("kind")=="recall" and outcome.get("entity_id")==expected if expected
                 else outcome.get("kind")=="conversation" and outcome.get("valid") and bool(outcome.get("text")))
        row={"case_id":case["id"],"seed":seed,"warmup":warmup,"expected":expected,
            "correct":bool(correct),"elapsed_ms":round(sum(c["elapsed_ms"] for c in calls),2),
            "wall_ms":round((time.monotonic()-total_start)*1000,2),"outcome":outcome,"calls":calls}
        rows.append(row)
        with (args.output/"wire.jsonl").open("a") as f:f.write(json.dumps(row,ensure_ascii=False)+"\n")
        print(json.dumps({k:row[k] for k in ("case_id","seed","correct","elapsed_ms","outcome")},ensure_ascii=False),flush=True)
        if outcome["kind"]=="error":raise RuntimeError("upstream failed; records retained")
    one(corpus[0],7,True)
    for case in corpus:
        for seed in seeds:
            one(case,seed)
    result=summary(rows)
    (args.output/"summary.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(result,ensure_ascii=False),flush=True)
if __name__=="__main__": main()
