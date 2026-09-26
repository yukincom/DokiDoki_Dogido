#!/usr/bin/env python3
"""Explicit live model; observation/initial poem/audio are fixtures, isolated memory only.

Does not start/stop MLX. Caller owns that lifecycle. All test runtime/helper/player
processes are reaped by fixture, including assertion/error paths.
"""
import argparse
import json
from pathlib import Path
import re
import threading
import time
import urllib.request
from check_haiku_runtime import fixture
from check_workshop_runtime import ready
from check_workshop_edits import revisions
from check_dialogue import row, submit, wait_for


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-url",required=True);p.add_argument("--model",required=True)
    p.add_argument("--output",required=True,type=Path)
    args=p.parse_args()
    report={"scope":"live consultation/editor/checker with simulated observation, initial poem and audio", "requested_model":args.model,"turns":[]}
    with fixture() as (base,process,log,control,seen,gate,drafting,checks,send,hud,rows,stored,folder):
        original=control["structured_handler"]
        def initial(incoming):
            # Give the fixed poem plausible, distinct recorded evidence instead of
            # the generic transport fixture's arbitrary first eligible atom.
            if incoming["max_tokens"]==512:
                prompt="\n".join(m["content"] for m in incoming["messages"])
                indices=[int(i) for i in re.findall(r"^- ([012]):",prompt,re.M)]
                atoms=re.findall(r"^- \[(\d+)\] ([^\n]+)",prompt,re.M)
                terms=["サクラの葉 /","ネザライトの斧 /","朝 /"]
                choices={i:next((int(n) for n,t in atoms if terms[i] in t),None) for i in indices}
                if any(v is None for v in choices.values()):
                    raise AssertionError((indices,atoms))
                return {"verdicts":{str(i):"pass" for i in indices},"assessments":[{"line_index":i,"atom_ids":[choices[i]]} for i in indices],"failure_reasons":{}}
            return original(incoming)
        control["structured_handler"]=initial
        sid=ready(base,send,rows)
        wait_for(lambda:stored(sid))
        report["initial_sources"]=stored(sid)[0]["materials_snapshot"]["line_sources"]
        def actual(incoming):
            if incoming["max_tokens"] not in (420,512) and incoming.get("temperature")!=.30:return None
            payload={**incoming,"model":args.model}
            req=urllib.request.Request(args.base_url.rstrip('/')+'/chat/completions',json.dumps(payload).encode(),{'Content-Type':'application/json'})
            with urllib.request.urlopen(req,timeout=35) as response:return json.load(response)
        control["completion_override"]=actual
        stop=threading.Event()
        def heartbeat():
            while not stop.wait(.25):send(sid)
        thread=threading.Thread(target=heartbeat);thread.start()
        try:
            texts=["この句はどういう意味？","中七のくろいおのへとの、へとが不自然なので直して"]
            for text in texts:
                started=time.monotonic();turn=submit(base,sid,text)
                result=wait_for(lambda:row(base,turn,{"completed","failed","cancelled"}),timeout=100)
                entry={"input":text,"text":result["text"],"status":result["playback_status"],"action":result.get("workshop_action"),"outcome":result.get("workshop_outcome"),"reason":result.get("workshop_reason"),"steps":result.get("workshop_steps"),"elapsed_ms_with_mock_audio":round((time.monotonic()-started)*1000),"calls":result.get("llm_reports",[]),"hud":hud(sid)}
                report["turns"].append(entry)
                print(json.dumps({k:v for k,v in entry.items() if k not in {"calls","hud"}},ensure_ascii=False),flush=True)
            assert hud(sid)["pending_lines"], report["turns"][-1]
            assert not revisions(folder,sid)
            turn=submit(base,sid,"その案で")
            result=wait_for(lambda:row(base,turn,{"completed","failed","cancelled"}))
            assert result.get("workshop_outcome")=="pending_saved",result
            report["accepted_revision"]=revisions(folder,sid)
            report["passed"]=True
        finally:
            stop.set();thread.join(timeout=3)
            report["runtime_log"]=log.read_text()
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')

if __name__=='__main__':main()
