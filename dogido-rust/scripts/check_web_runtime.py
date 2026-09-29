#!/usr/bin/env python3
"""実HTTPと模擬player/検索SDKだけで、Web開始の実再生境界を確認する。"""
import argparse
import json
from pathlib import Path
import tempfile
from check_dialogue import ROOT, dependencies, running, register, submit, row, wait_for, snapshot, request


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,default=ROOT/'target/debug/dogido-rust')
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='dogido-web-http-') as tmp, dependencies() as (dep, control, seen):
        folder=Path(tmp)
        adapter=folder/'web.py'
        adapter.write_text("""import json,os,sys
from pathlib import Path
log=Path(__file__).with_name('searches.jsonl')
for line in sys.stdin:
 v=json.loads(line)
 if v['op']=='close': break
 if v['op']=='search':
  with log.open('a') as f:f.write(json.dumps({'pid':os.getpid(),'proposal':v['proposal']},ensure_ascii=False)+'\\n')
  print(json.dumps({'request_id':v['request_id'],'result':{'status':'page_opened','child_status':'opened','search_url':'https://www.google.com/search?q=fixture'}}),flush=True)
""")
        def model(incoming):
            if incoming['max_tokens'] not in {850,350}:return None
            before,current=incoming['messages'][-1]['content'].split('\n以上は背景。今回応答する最新の発話はこちら：\n')
            current=json.loads(current)
            if incoming['max_tokens']==350:return {'intent':'acknowledge','evidence':current['text']}
            return {'dialogue_act':'information_request','question':current['text'],'target':'未収録試験語彙','facet':'meaning','topic':'language','relation':'new','target_status':'explicit','alternatives':[], 'evidence':[{'turn_id':current['turn_id'],'quote':current['text']}],'search_terms':['未収録試験語彙'],'clarification':'','lookup_requested':True,'web_query':'未収録試験語彙の意味'}
        control['structured_handler']=model
        def count():
            p=folder/'searches.jsonl'
            return len(p.read_text().splitlines()) if p.exists() else 0
        with running(args.binary.resolve(),folder,dep,haiku_settings={'llm_enabled':False},web_settings={'enabled':True,'available':True,'adapter':str(adapter)}) as (base,process,log):
            def web(sid):return next(s['web'] for s in snapshot(base)['sessions'] if s['session_id']==sid)
            def propose():
                sid=register(base)
                t=submit(base,sid,'知らないことわざを教えて')
                r=wait_for(lambda:row(base,t,{'completed'}))
                assert r['language_status']=='web_consent_requested',r
                return sid
            sid=propose();assert count()==0
            (folder/'player_mode').write_text('slow')
            t=submit(base,sid,'ええで')
            wait_for(lambda:row(base,t,{'started'}));assert count()==0
            wait_for(lambda:row(base,t,{'completed'}));wait_for(lambda:web(sid)['phase']=='reading')
            assert count()==1
            (folder/'player_mode').write_text('ok')
            t=submit(base,sid,'このページ https://example.com/private は閉じて')
            r=wait_for(lambda:row(base,t,{'completed'}));assert web(sid)['phase']=='none',r
            assert count()==1
            session=next(s for s in snapshot(base)['sessions'] if s['session_id']==sid)
            assert 'google.com' not in json.dumps(session['history'])
            assert 'example.com/private' not in json.dumps(session['history'])
            request(base,'/api/v1/adapter-sessions/'+sid,method='DELETE')
            sid=propose();(folder/'player_mode').write_text('fail')
            t=submit(base,sid,'はい');wait_for(lambda:row(base,t,{'failed'}));assert count()==1
            request(base,'/api/v1/adapter-sessions/'+sid,method='DELETE')
            (folder/'player_mode').write_text('ok');sid=propose();(folder/'player_mode').write_text('slow')
            t=submit(base,sid,'はい');wait_for(lambda:row(base,t,{'started'}))
            request(base,'/api/v1/adapter-sessions/'+sid,method='DELETE');assert count()==1
    print('web HTTP: 3 sequences passed; own server, adapter, player and WAV cleaned up')

if __name__=='__main__':main()
