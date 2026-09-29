"""Canonical pure topic-policy comparison data; no runtime/model calls."""
from dataclasses import asdict
import itertools
import json
from pathlib import Path
from dogido_server.dialogue import chat_policy as p
from dogido_server.dialogue import player_chat_planner as planner

out=Path(__file__).resolve().parents[1]/'src/chat_topics'
out.mkdir(exist_ok=True)
(out/'rules.json').write_text(json.dumps({'generic_terms':sorted(p.GENERIC_TOPIC_TERMS),
    'specific_short_terms':sorted(p._SPECIFIC_SHORT_TERMS),'identify_markers':list(p._IDENTIFY_INTENT_MARKERS),
    'identify_min_score':p._IDENTIFY_MIN_SCORE},ensure_ascii=False,indent=2)+'\n')
terms=['',' ','\u001c\u3000','旗','猫','🐈','🐈🐈','旗印','前哨基地','ババア','青い旗','みどり','とても大きい',*sorted(p.GENERIC_TOPIC_TERMS)]
texts=['',' ','今日はきれいだね','何？','何もない','なにもの？','あいつ','どういうもの','いる？','おる?','居る?','いない','いなくなった',
    'ラバがいる','前哨基地ある？','とても遠くに大勢いる','今もいるんかな','まだいそう','気配する','ついてくる','追いかけてくる','声が聞こえる？',
    '声はしない','音がする','音楽いいね','聞こえへん','きこえたか','🐈'*7+'いる','🐈'*11+'いる','\u001cまだいる？\u001f']
hitsets=[[],[{'entry_id':'cat','label_ja':'ネコ','score':8,'matched_terms':[]}],
    [{'entry_id':'cat','label_ja':'ネコ','score':8,'matched_terms':['白い','小さい']}],
    [{'entry_id':'pillager','label_ja':'ピリジャー','score':6,'matched_terms':['旗']}],
    [{'entry_id':'goat','label_ja':'ヤギ','score':5.99,'matched_terms':['ヤギ']}],
    [{'entry_id':'goat','label_ja':'ヤギ','score':6,'matched_terms':['ヤギ']}],
    [{'entry_id':'goat','label_ja':' ヤギ ','score':8,'matched_terms':[' ヤギ ' ]},{'entry_id':'cat','label_ja':' ネコ ','score':8.009,'matched_terms':['ネコ']}],
    [{'entry_id':'goat','label_ja':'ヤギ','score':8,'matched_terms':['ヤギ']},{'entry_id':'cat','label_ja':'ネコ','score':8.011,'matched_terms':['ネコ']}],
    [{'entry_id':'outpost','label_ja':'前哨基地','score':10,'matched_terms':['前哨基地']},{'entry_id':'pillager','label_ja':'ピリジャー','score':8,'matched_terms':['旗']}],
    [{'entry_id':'minecraft:goat','label_ja':'ヤギ','score':10,'matched_terms':['ヤギ']}],
    [{'entry_id':' minecraft:goat ','label_ja':'ヤギ','score':10,'matched_terms':['ヤギ']}],
    [{'entry_id':'goat','label_ja':'','score':10,'matched_terms':['ヤギ']}],
    [{'entry_id':'goat','label_ja':'  ','score':10,'matched_terms':['ヤギ']},{'entry_id':'cat','label_ja':'ネコ','score':10,'matched_terms':['ネコ']}],
    [{'entry_id':'','label_ja':'謎','score':10,'matched_terms':['謎の旗']}],
    [{'entry_id':f'id{i}','label_ja':f'対象{i}','score':10-i,'matched_terms':['旗']} for i in range(10)],
]
observations=[[],['goat'],['cat'],['pillager'],['minecraft:goat'],[' minecraft:goat '],['GOAT'],['outpost']]
# Intern repeated rows/policy strings to keep the canonical corpus reviewable.
pool=[];indices={}
def intern(value):
 key=json.dumps(value,ensure_ascii=False,sort_keys=True)
 if key not in indices:indices[key]=len(pool);pool.append(value)
 return indices[key]
cases=[]
for n,(hits,text,observed,visual,threat) in enumerate(itertools.product(hitsets,texts,observations,[False,True],['',' 視認 ゾンビ','音メモ:ヤギ','気配','交戦中っぽい'])):
    # Full small axes for stance; representative identify post-ground paths every row.
    usable=p.filter_usable_topic_hits(hits)
    stance=p.resolve_reply_stance(has_visual_threats=visual,topic_hits=hits,threat_summary=threat,user_text=text,observed_ids=observed)
    action=('identify_entity','check_entity_presence','continue_conversation','correct_previous_reply')[n%4]
    plan=planner.PlayerChatPlan(action,'照合',text if action in planner._ENTITY_ACTIONS else '',(),.9,'model','accepted')
    rows=[{'entity_id':mid,'label':mid} for mid in observed]
    ground=planner.ground_player_chat_entity(plan,topic_hits=usable,observed_entities=rows)
    selected=[]
    if action=='identify_entity' and stance=='hypothesis':
      ids=set(ground.observed_ids)
      selected=[h for h in usable if str(h.get('entry_id') or '') in ids] if ids else usable
    skeleton=p.build_identify_skeleton(stance=stance,topic_hits=selected)
    if ground.status=='observed':skeleton=None
    projection={'usable_indices':[i for i,h in enumerate(hits) if any(h is u for u in usable)],'reply_stance':stance,'reply_policy':p.reply_policy_line(stance),
      'topic_for_identify_indices':[i for i,h in enumerate(hits) if any(h is u for u in selected)],'identify_skeleton':skeleton}
    data={'has_visual_threats':visual,'topic_hits':hits,'threat_summary':threat,'user_text':text,'observed_ids':observed}
    cases.append([intern(hits),intern(text),intern(observed),visual,intern(threat),intern(asdict(plan)),intern(asdict(ground)),intern(projection)])
# Generic term and standalone intent checks supplement the real catalog-shaped API.
small={'terms':[[t,p.is_generic_topic_term(t)] for t in terms],
       'intents':[[t,p.has_identify_intent(t),p.has_threat_presence_query(t)] for t in texts],
       'policies':[[s,p.reply_policy_line(s)] for s in ['none','saw','hypothesis','clarify',' SAW ','UNKNOWN','\u001cHYPOTHESIS\u001f','']]}
(out/'fixtures.json').write_text(json.dumps({'pool':pool,'cases':cases,**small},ensure_ascii=False,separators=(',',':'))+'\n')
print(f'{len(cases)} projections; {len(terms)} terms; {len(texts)} intents')
