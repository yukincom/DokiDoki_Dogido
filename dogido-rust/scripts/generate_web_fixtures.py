#!/usr/bin/env python3
"""正本の限定Web promptと読書判断を採取。通信・モデル・ブラウザーは使わない。"""
from pathlib import Path
import json
import sys
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from dogido_server.language_dialogue.controller import LanguageDialogue
from dogido_server.language_dialogue.web_research import ResearchContext
from dogido_server.language_dialogue.prompts import build_web_consent_messages,build_research_intent_messages,build_research_reading_messages

class LLM:
    def __init__(self,intent,reading):self.intent=intent;self.reading=reading
    def generate_structured_json(self,r):return self.reading if r.kind=='language_research_reading' else self.intent

def main():
    out=ROOT/'dogido-rust/src/language/web'
    kinds={'language_web_consent':build_web_consent_messages,'language_research_intent':build_research_intent_messages,'language_research_reading':build_research_reading_messages}
    prompts={k:f(SimpleNamespace(details={}))[0]['content'] for k,f in kinds.items()}
    (out/'prompts.json').write_text(json.dumps(prompts,ensure_ascii=False,indent=2)+'\n')
    page={'id':'overview:test','text_ja':'狐の由来にはいくつもの説があり、はっきり決まっていません。','use':'google_ai_overview'}
    cases=[]
    def append(phase,intent,text,reading):
        i={'intent':intent,'evidence':text}
        c=ResearchContext('狐の由来','狐',[page],phase,[], 'https://www.google.com/search?q=fox')
        d=LanguageDialogue(LLM(i,reading));record={}
        result=d._research_reply({'current':{'text':text,'turn_id':'turn'},'research':c.snapshot()},record,c,0)
        cases.append({'context':c.snapshot(),'text':text,'intent':i,'reading':reading,'expected':{'status':record['status'],'text':record.get('reply',''),'phase':result.phase}})
    for phase in ['awaiting_report','discussing','return_offered','confirming_topic_change']:
        for intent,text in [('report','狐の名前の由来は決まっていない'),('discuss','どんな説があるの'),('uncertain','分からない'),('continue','まだ続けたい'),('return','冒険に戻る'),('new_question','狸の話も聞きたい'),('acknowledge','なるほど'),('other','ふふふ'),('other','今の続き'),('other','別の質問'),('return','ページ閉じて')]:
            append(phase,intent,text,{'perspective':'いくつか説があるんやな。','quotes':[{'page_id':'overview:test','quote':'狐の由来にはいくつもの説があり'}]})
    for bad in [{'perspective':'説明','quotes':[]},{'perspective':'説明','quotes':[{'page_id':'other','quote':'狐の由来にはいくつもの説があり'}]},{'perspective':'説明','quotes':[{'page_id':'overview:test','quote':'資料にはないものを引用した記録'}]}]:
        append('awaiting_report','report','そうなの',bad)
    (out/'research-fixtures.json').write_text(json.dumps(cases,ensure_ascii=False,indent=2)+'\n')
    print(f'web prompt {len(prompts)} / research {len(cases)} canonical cases')
if __name__=='__main__':main()
