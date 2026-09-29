#!/usr/bin/env python3
"""Canonical renderer, persistent synthetic observation memory, mock planner/leaf only.

No provider, socket, audio, model or filesystem-memory writes. The temporal oracle
is shared with generate_chat_observation_fixtures; a fresh helper is not the oracle.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
import hashlib
import json
import logging
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
from generate_chat_observation_fixtures import event, visual, sound, ambient, snapshot

ROOT = Path(__file__).resolve().parents[2]


def cases():
    yield {"name":"empty"}
    for text in ["", "  \n ", "何も知らん", "家に帰る"]:
        yield {"name":"planner-fallback-"+text,"input":{"semantic_text":text},"plan":{"source":"fallback","status":"unavailable"}}
    yield {"name":"voice-raw-semantic","input":{"raw_text":"県に変えてって言ってない","semantic_text":"剣に変えてって言ってない"}}
    yield {"name":"false-history-text","context":{"history":{"conversation_history":"","event_digest":"","conversation_turns":[{"role":"user","text":None},{"role":"user","text":0},{"role":"assistant","text":"ヤギ"},{"role":"user","text":"パンダ"}]}}}
    yield {"name":"knowledge-first", "input":{"knowledge_query_present":True,"semantic_text":"匂いって何"}}
    for text in ["何の匂い？","くさいね","臭いって言葉","なんか\n臭い","何\nの匂い", "このにおいは何？"]:
        for smell in [None,{"status":"none"},{"status":"suppressed","suppression_reason":"rain"}]:
            yield {"name":f"smell-{text}-{smell}","input":{"semantic_text":text},"fields":{"smell_observation":smell}}
    for action in ["continue_conversation","check_entity_presence","identify_entity","answer_observation","clarify_reference","correct_previous_reply","repair_conversation","clarify_repair"]:
        for seen in [False,True]:
            for soundq in [False,True]:
                yield {"name":f"action-{action}-{seen}-{soundq}","plan":{"action":action,"entity_query":"ゾンビ" if action in {"check_entity_presence","identify_entity","correct_previous_reply"} else ""},"input":{"semantic_text":"ゾンビはどこ？","asks_about_sound":soundq},"fields":{"visual_threats":[visual()] if seen else []}}
    for text in ["そうなんだ","これは何？","帰らなくちゃ","帰らないと思う","帰らんと考える","帰らんと言うけど戻らなきゃ","家に帰ろう","家に帰らないよ","戻ります！お家へ","帰る12345678家","帰る123456789家","帰る\u001c家", "ふむ"*100]:
        for weather,time,dim in [("clear","day","overworld"),("rain","evening","minecraft:overworld"),("thunder","day","overworld"),("clear","evening","minecraft:the_end")]:
            yield {"name":f"travel-{text}-{weather}-{time}-{dim}","input":{"semantic_text":text},"fields":{"world":{"time_phase":time,"weather":weather},"player":{"dimension":dim}},"seeds":[event(i*1000,world={"respawn_point_set":True,"respawn_distance":d},passive_mobs=[{"type":"cow"}],visual_threats=[] ) for i,d in enumerate([20,19,18])],"ms":3500}
    for biome in ["plains","dark_forest","minecraft:dark_forest","pale_garden","mushroom_fields","deep_dark","lush_caves","minecraft:lush_caves","the_end","nether_wastes","cherry_grove","snowy_plains","unknown"]:
        for sky in [True,False,None]:
            for extra in [{},{"is_submerged":True},{"nearby_door_count":1,"sky_light":0,"block_light":10}]:
                yield {"name":f"environment-{biome}-{sky}-{extra}","fields":{"world":{"biome":biome,"sky_visible":sky,"weather":"thunder","time_phase":"evening",**extra}}}
    for mode in ["normal","panic","suppressed_panic","alert","unknown"]:
        for score in [None,0.,0.71,0.72,1.]:
            yield {"name":f"mode-{mode}-{score}","context":{"mode":mode},"fields":{"world":{"danger_darkness_score":score}},"settings":{"default_call_name":" 自分 "}}
    rows=[{}, {"visual_threats":[visual()]}, {"visual_threats":[visual("skeleton","s",distance=None),visual("witch","w",distance=2.5),visual("zombie","z",distance=2.5)]}, {"visual_threats":[visual("minecraft:zombie",direction=None)]}, {"auditory_threats":[sound("unknown",sound_event="entity.skeleton.ambient")]}, {"auditory_threats":[sound("unknown",direction=None,band=None)]}, {"ambient_sounds":[ambient("block:lava")]}, {"passive_mobs":[{"type":v} for v in ["pig","cow","cat","sheep","wolf","chicken"]]}, {"world":{"rain_sound_recent_ms":0,"thunder_sound_recent_ms":4000}}]
    for i,fields in enumerate(rows):
        for action in ["continue_conversation","check_entity_presence","identify_entity"]:
            for q in [False,True]:
                yield {"name":f"observations-{i}-{action}-{q}","fields":fields,"input":{"semantic_text":"これなんだ？","asks_about_sound":q},"plan":{"action":action,"entity_query":"ゾンビ" if action!="continue_conversation" else ""}}
    seed=event(0,visual_threats=[visual()],passive_mobs=[{"type":"cat"},{"type":"cow"}],auditory_threats=[sound("skeleton")],ambient_sounds=[ambient("block:lava")])
    for ms in [-1000,1000,12000,12001,20000,20001,60000,60001]:
        for action in ["continue_conversation","check_entity_presence","correct_previous_reply"]:
            yield {"name":f"persistent-{ms}-{action}","ms":ms,"seeds":[seed],"plan":{"action":action,"entity_query":"ゾンビ" if action!="continue_conversation" else ""},"context":{"history":{"conversation_history":"USER: ネコだよ\nASSISTANT: ヤギがいるで","conversation_turns":[{"turn_id":"u1","role":"user","text":"ネコだよ"},{"turn_id":"u1:reply","role":"assistant","text":"ヤギがいるで"}],"event_digest":"Webから復帰: 敬語の使い方"}}}
    for query in ["白い建物","村","変わった家","牛","ネコ","クリーパー","青白いゾンビ","ゾンビ","ほわほわ","ないもの"]:
        for current in [None,"village","ocean_monument","minecraft:village"]:
            yield {"name":f"catalog-{query}-{current}","input":{"semantic_text":query},"plan":{"action":"identify_entity","entity_query":query},"context":{"current_structure":current},"fields":{"world":{"biome":"desert"}}}
    for open_ in [False,True]:
        for inventory in [False,True]:
            for hearing in [False,True]:
                for action in ["continue_conversation","identify_entity","check_entity_presence"]:
                    yield {"name":f"workshop-{open_}-{inventory}-{hearing}-{action}","context":{"workshop_open":open_,"workshop_details":{"haiku_workshop_open":"true","haiku_workshop_text":"あさのそら\nひかりがもれる\nあおいくさ","haiku_workshop_materials":"朝の草地"}},"input":{"semantic_text":"これ何？","asks_inventory":inventory,"asks_about_sound":hearing},"plan":{"action":action,"entity_query":"牛" if action!="continue_conversation" else ""},"fields":{"look_target":{"kind":"entity","name":"cow"},"inventory":{"torch":32,"diamond_sword":1},"player":{"held_item":"diamond_sword"},"ambient_sounds":[ambient("cow")],"passive_mobs":[{"type":"cow"}]}}
    for raw in ["cow","minecraft:cow"," horse ","minecart","oak_boat","unknown"]:
        for kind in ["entity","block","Entity"]:
            yield {"name":f"look-{kind}-{raw}","input":{"semantic_text":"これ何？"},"fields":{"look_target":{"kind":kind,"name":raw},"player":{"name":" 遊ぶ人 "}}}
    for entity in ["horse","oak_boat","minecart"]:
        yield {"name":f"vehicle-{entity}","input":{"semantic_text":"これ何？"},"fields":{"player":{"vehicle":{"vehicle_id":entity,"activity":"riding","controlling":True}},"look_target":{"kind":"entity","name":"cow"},"passive_mobs":[{"type":"pig"}],"visual_threats":[visual()],"auditory_threats":[sound()]}}


def main():
    p=argparse.ArgumentParser();p.add_argument("--source-root",type=Path,default=ROOT);p.add_argument("--output-dir",type=Path,default=ROOT/"dogido-rust/fixtures");a=p.parse_args()
    logging.disable(logging.CRITICAL)
    sys.path.insert(0,str(a.source_root))
    from dogido_server.config import Settings
    from dogido_server.models import GameEvent
    from dogido_server.state_machine.machine import DogidoStateMachine
    from dogido_server.dialogue.player_chat_planner import PlayerChatPlan,PlayerChatPlanEvidence
    from dogido_server.dialogue.conversation_repair import ConversationRepair
    from chat_validation_helper import leaf_input
    class Oracle(DogidoStateMachine):
        def _render_knowledge_reply(self): return "knowledge_routed"
        def _generate_leaf_text(self,**kwargs):
            self.leaf=kwargs
            return "うん、その話を聞いてるで。"
        def _mark_smell_announced(self,*args): self.announced=True
        def _player_chat_history_details(self): return self.context["history"]
        def _haiku_workshop_is_open(self): return self.context["workshop_open"]
        def _player_chat_haiku_workshop_details(self): return self.context.get("workshop_details") or {}
    a.output_dir.mkdir(parents=True,exist_ok=True)
    count=0; outcomes={}
    with (a.output_dir/"chat-materials.jsonl").open("w") as f:
        for spec in cases():
            settings={"darkness_alert_threshold":0.72,"home_bed_prompt_distance":10.,"default_call_name":"",**spec.get("settings",{})}
            config=Settings(_env_file=None,decision_policy="legacy",**settings)
            machine=Oracle(config,llm=None)
            context={"mode":"normal","current_structure":None,"history":{"conversation_history":"","conversation_turns":[],"event_digest":""},"workshop_open":False,"workshop_details":None,**spec.get("context",{})}
            machine.context=context
            for seed in spec.get("seeds",[]): machine.process(GameEvent.model_validate(seed))
            machine.state.mode=context["mode"];machine.state.current_structure=context["current_structure"]
            machine.llm=SimpleNamespace()
            raw_event=event(spec.get("ms",3000),**spec.get("fields",{}))
            try: ev=GameEvent.model_validate(raw_event)
            except Exception as ex: raise RuntimeError(spec["name"]) from ex
            inp={"raw_text":"こんにちは","semantic_text":"こんにちは","asks_inventory":False,"asks_about_sound":False,"knowledge_query_present":False,**spec.get("input",{})}
            if "raw_text" not in spec.get("input",{}): inp["raw_text"]=inp["semantic_text"]
            machine.player_input=SimpleNamespace(**{k:v for k,v in inp.items() if k!="knowledge_query_present"},knowledge_query=object() if inp["knowledge_query_present"] else None)
            plan_data={"action":"continue_conversation","focus":"今の会話","entity_query":"","evidence":[{"turn_id":"current","quote":inp["semantic_text"][:160]}],"confidence":.9,"source":"model","status":"accepted","repair":None,"presence_challenged":False,**spec.get("plan",{})}
            if plan_data["action"] in {"clarify_repair","repair_conversation"}:
                plan_data["repair"]={"action":plan_data["action"],"target_turn_id":"u1","target_quote":"ネコだよ","signal_quote":"違う","replacement_quote":"犬のこと" if plan_data["action"]=="repair_conversation" else "","current_text":"違う、犬のこと"}
            plan=PlayerChatPlan(**{**plan_data,"evidence":tuple(PlayerChatPlanEvidence(**r) for r in plan_data["evidence"]),"repair":ConversationRepair(**plan_data["repair"]) if plan_data["repair"] else None})
            captures=[]
            def choose(_llm,**kwargs): captures.append(kwargs);return plan
            machine.leaf=None;machine.announced=False
            snap=snapshot(machine,ev)
            with patch("dogido_server.dialogue.player_chat_planner.plan_player_chat",choose): reply=machine._render_player_chat_reply(ev)
            if inp["knowledge_query_present"]: expected={"kind":"knowledge"}
            elif machine.announced: expected={"kind":"fixed_before","text":reply,"reason":"current_smell"}
            elif machine.leaf is None:
                reason="clarify_repair" if plan.action=="clarify_repair" else "no_hearing_evidence" if inp["asks_about_sound"] and not any([snap["hearing"]["summary"],snap["hearing"]["named_mobs"],snap["hearing"]["source_labels"]]) else "grounded_reply"
                expected={"kind":"fixed_after","text":reply,"reason":reason}
            else:
                leaf=machine.leaf; req=SimpleNamespace(**leaf,max_tokens=None)
                expected={"kind":"leaf","details":leaf["details"],"fallback_text":leaf["fallback_text"],"input":leaf_input(req,"mock-chat",512)}
            expected["planner_input"]=captures[0] if captures else None
            json.dump({"name":spec["name"],"event":raw_event,"settings":settings,"context":context,"input":inp,"snapshot":snap,"plan":plan_data,"expected":expected},f,ensure_ascii=False,separators=(",",":"));f.write("\n")
            count+=1;outcomes[expected["kind"]]=outcomes.get(expected["kind"],0)+1
    sources=["dogido_server/state_machine/mixins/narration.py","dogido_server/state_machine/mixins/common.py","dogido_server/dialogue/player_plan.py","dogido_server/dialogue/player_chat_planner.py","dogido_server/dialogue/chat_policy.py","dogido_server/entry_catalog.py","dogido-rust/scripts/chat_prompt_helper.py","dogido-rust/scripts/chat_validation_helper.py"]
    meta={"cases":count,"outcomes":outcomes,"source_sha256":{v:hashlib.sha256((a.source_root/v).read_bytes()).hexdigest() for v in sources},"oracle":"canonical full _render_player_chat_reply with mock planner and capture leaf; persistent process before temporal snapshot; no model/service"}
    (a.output_dir/"chat-materials-meta.json").write_text(json.dumps(meta,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(meta["outcomes"]))
if __name__=="__main__":main()
