#!/usr/bin/env python3
"""Copy canonical mutation guards and paused-input prompts into Rust assets."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dogido_server.haiku import workshop_agent as w
from dogido_server.llm.workshop_prompts import build_haiku_workshop_combat_input_messages
from combat_input_oracle import safe, ACTIONS
ROOT=Path(__file__).resolve().parents[1]/'src/workshop_input_guard'
ROOT.mkdir(exist_ok=True)
patterns={k:getattr(w, '_STATE_CHANGE_'+k.upper()).pattern for k in ('report','conditional','uncertain')}
patterns['contradictions']={k:v.pattern for k,v in w._STATE_CHANGE_CONTRADICTIONS.items()}
messages=build_haiku_workshop_combat_input_messages({'verse':'__VERSE__','player_text':'__PLAYER__','allowed_actions':ACTIONS})
# Store literal pieces, so source text containing a slot marker is never substituted twice.
parts=messages[1]['content'].split('__VERSE__'); tail=parts[1].split('__PLAYER__')
assets={'patterns':patterns,'prompt':{'system':messages[0]['content'],'parts':[parts[0],*tail]}}
(ROOT/'assets.json').write_text(json.dumps(assets,ensure_ascii=False,indent=2)+'\n')
texts=['', 'うん','はい。','ええよ','OK!','句の続きを話そう','中七について教えて','今日はここまで',
       '上五を春にして','その案でいい','採用して','却下して','終了にして','終了はしない','続けないで','再開するな',
       '意味は何？','句に戻るつもりはない','終了ではなく続きを','それにして','もし句に戻ったらどう','句に戻るかな',
       '句に戻るかな\n','句に戻るかな\n\n','句に戻るかな\x1c','句に戻ると言われた','終わりにしない',
       'しろい','終わりにするつもりはない','句に戻る！敵はどう？','句に戻ると聞いた。',
       '「上五」を変えて','「句に戻る」「句に戻る」','句に戻る。それは「句に戻る」という話','𠮷が言った句に戻る']
base=list(texts)
for s in base:
 for prefix,suffix in [('「','」'),('『','』'),('"','"'),("'","'"),('前の話は。','？'),('これは！','。別の話？'),('もし','ならいい'),('','って言われた'),('','とは言ってない'),('\x1c','\x1f')]:
  texts.append(prefix+s+suffix)
cases=[]
for text in texts:
 evidence=[text]+[s for s in ['句に戻る','終了にして','上五','採用して','それにして','ない','別の根拠'] if s!=text]
 for quote in evidence:
  cases.append({'text':text,'evidence':quote,'state':{a:w._state_change_evidence_is_safe(a,player_text=text,evidence=quote) for a in ['stage_player_edit',*patterns['contradictions']]},'combat':{a:safe(a,text,quote) for a in ACTIONS}})
(ROOT/'fixtures.json').write_text(json.dumps(cases,ensure_ascii=False,separators=(',',':'))+'\n')
prompts=[]
for verse in ['', 'くさちのひ\nくろきつるぎの\nかげのさむさ',' __PLAYER__ \n', '\x1c朝\x1f']:
 for text in texts[:35]:
  prompts.append({'verse':verse,'text':text,'messages':build_haiku_workshop_combat_input_messages({'verse':verse,'player_text':text,'allowed_actions':ACTIONS})})
(ROOT/'prompts.json').write_text(json.dumps(prompts,ensure_ascii=False,separators=(',',':'))+'\n')
print(f'{len(cases)} guard inputs, {len(prompts)} prompts')
