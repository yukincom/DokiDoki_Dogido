"""Compare canonical Python grounding; no models, subprocesses or services."""
import json
from dataclasses import asdict
from pathlib import Path
from dogido_server.dialogue import player_chat_planner as p
from chat_grounding_helper import grounding_input

rows = [
    ([], []),
    ([{"entry_id":"goat", "label_ja":"ヤギ", "score":1}], []),
    ([{"entry_id":"goat", "label_ja":"ヤギ", "score":1}], [{"entity_id":"cat","label":"ネコ"}]),
    ([{"entry_id":"goat", "label_ja":"ヤギ", "score":1}], [{"entity_id":"goat","label":"ヤギ"}]),
    ([{"entry_id":"outpost","label_ja":"前哨基地","score":1},{"entry_id":"pillager","label":"ピリジャー","score":.7}], [{"entity_id":"pillager","label":"ピリジャー"}]),
    ([{"entry_id":"goat","label":"ヤギ","score":1},{"entry_id":"cat","label":"ネコ","score":1}], []),
    ([{"entry_id":"goat","label":"ヤギ","score":1},{"entry_id":"cat","label":"ネコ","score":1}], [{"entity_id":"cat","label":"ネコ"}]),
    ([{"entry_id":" minecraft:GOAT\n", "label_ja":" \u3000", "score":"broken"}], [{"entity_id":"minecraft:GOAT", "label":""}]),
    ([{"entry_id":"goat","label":"ヤギ","score":"nan"},{"entry_id":"cat","label":"ネコ","score":0}], []),
    ([{"entry_id":"goat","label":"ヤギ","score":None},{"entry_id":"cat","label":"ネコ","score":False}], []),
    ([{"entry_id":"goat","label":"ヤギ","score":.009},{"entry_id":"cat","label":"ネコ","score":0}], []),
    ([{"entry_id":"goat","label":"ヤギ","score":.011},{"entry_id":"cat","label":"ネコ","score":0}], []),
    ([{"entry_id":"goat","label":"ヤギ","score":1},{"entry_id":"goat","label":"別名","score":0}], [{"entity_id":"goat","label":"ヤギ"},{"entity_id":"goat","label":"最新名"}]),
    ([{"entry_id":f"id_{i}","label":f"動物{i}","score":10-i} for i in range(10)], [{"entity_id":f"id_{i}","label":f"動物{i}"} for i in range(10)]),
    ([{"entry_id":"id_20","label":"最後の観測","score":1}], [{"entity_id":f"id_{i}","label":f"動物{i}"} for i in range(24)]),
    ([{"entry_id":"goat","label":"長い名前"*30,"score":1}], [{"entity_id":"goat","label":"名前"*80}]),
    ([{"entry_id":"","label":"無効"},{"entry_id":"cat","label":"ネコ","score":1}], []),
]
cases=[]
for action in p.PLAYER_CHAT_PLAN_ACTIONS:
    for challenged in (False, True):
        for hits, observed in rows:
            plan=p.PlayerChatPlan(action, "現在の対象", "ヤギ" if action in p._ENTITY_ACTIONS else "",
                (p.PlayerChatPlanEvidence("current","ヤギいる？"),), .9,"model","accepted", presence_challenged=challenged)
            if not plan.requests_catalog: hits=[]
            g=p.ground_player_chat_entity(plan,topic_hits=hits,observed_entities=observed)
            cases.append({"input":grounding_input(plan,topic_hits=hits,observed_entities=observed),
                "prompt_observations":p._normalize_observed_entities(observed),
                "expected":{"grounding":asdict(g),"fixed_reply":p.fixed_grounded_player_chat_reply(plan,g)}})
# Compare model consumer parse and fallback routing too, preserving report-vs-question
# and assistant-history correction. Fallback still comes from canonical Python.
parsed=[]
for text,hist,inv,sound in [
    ("こんにちは",[],False,False), ("ヤギがいる",[],False,False),
    ("ヤギいる？",[],False,False), ("ヤギ見えない",[],False,False),
    ("いないじゃん",[{"turn_id":"old","role":"assistant","text":"ヤギがおるで。"}],False,False),
    ("前哨基地ある？",[],False,False), ("何持ってる？",[],True,False), ("何の音？",[],False,True),
    ("さっきヤギがいるって言ったけど",[{"turn_id":"old","role":"assistant","text":"ヤギがおるで。"}],False,False),
]:
    history=p._normalize_history(hist)
    fallback=p._fallback_plan(text,inventory_question=inv,sound_question=sound,history=history)
    details={"allowed_actions":[a for a in p.PLAYER_CHAT_PLAN_ACTIONS if a not in p.REPAIR_ACTIONS],"history":history,"pending_repair":{},
             "observations":{"observed_entities":[]},"routing_hints":{"inventory_question":inv,"sound_question":sound,
             "presence_question":fallback.action=="check_entity_presence","plain_presence_report":p._is_plain_presence_report(text) and fallback.action!="check_entity_presence"},
             "current":{"turn_id":"current","role":"user","text":text,"raw_text":text}}
    for action in p.PLAYER_CHAT_PLAN_ACTIONS:
      if action in p.REPAIR_ACTIONS: continue
      for confidence in (.61,.8,.9):
        evidence=[{"turn_id":"current","quote":text}]
        if history: evidence.append({"turn_id":"old","quote":history[-1]["text"]})
        payload={"action":action,"focus":"返答","entity_query":"ヤギ" if action in p._ENTITY_ACTIONS and "ヤギ" in text else text if action in p._ENTITY_ACTIONS else "", "evidence":evidence,"confidence":confidence,"repair":None}
        class Fake:
          def generate_structured_json(self,request): return dict(payload,__dogido_status="accepted")
        result=p.plan_player_chat(Fake(),user_text=text,raw_user_text=text,conversation_turns=history,observation_summary="",observed_entities=[],inventory_question=inv,sound_question=sound)
        parsed.append({"details":details,"fallback":asdict(fallback),"payload":payload,"expected":asdict(result)})
path=Path(__file__).resolve().parents[1]/'fixtures/planner/grounding.json'
path.write_text(json.dumps({"cases":cases,"parsed":parsed},ensure_ascii=False,indent=2)+'\n')
print(f'{len(cases)} grounding cases; {len(parsed)} parse/fallback cases')
