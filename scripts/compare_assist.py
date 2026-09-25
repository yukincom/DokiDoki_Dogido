#!/usr/bin/env python3
"""Offline same-input Python/Rust assist parity. No service/model/audio/network starts.

Run with the existing Python environment and --python-root pointing at the Python
reference checkout. The probe executable can be supplied with --binary.
Intentional changes are asserted separately, never rewritten into parity PASS:
- submit retires expired commands; no unconfirmed success claim;
- unknown command-result IDs are ACKed but never generate failure speech.
"""
from __future__ import annotations
import argparse
import dataclasses
from datetime import datetime, timedelta, timezone
import itertools
import json
import logging
from pathlib import Path
import random
import subprocess
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
BASE = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)
def stamp(seconds): return (BASE + timedelta(seconds=seconds)).isoformat()
def payload(text, **changes): return dict(intent='select_weapon', weapon_kind='sword', is_request=True, evidence=text, confidence=.99, **changes)
def slot(index=0, kind='sword', attack=5, damage=0, maximum=131):
    return dict(slot=index, item_id=f'minecraft:test_{kind}_{index}', weapon_kind=kind, count=1, attack_damage=attack, damage=damage, max_damage=maximum)
def event(slots=None):
    return dict(schema_version='2026-05-24', game='minecraft-java', adapter='test', observed_at=stamp(0), sequence=1,
        event=dict(name='status_snapshot',source_kind='system',priority_hint='background',certainty='high'),
        player=dict(name='test',position=dict(x=0,y=64,z=0),dimension='minecraft:overworld',hotbar=dict(selected_slot=8,slots=[slot()] if slots is None else slots)),
        world=dict(time_phase='day',weather='clear',biome='plains',local_light=15,sky_visible=True,danger_darkness_score=0),combat=dict(combat_active_hint=False))
def inp(at=0,text='剣',busy=False,slots=None,**context):
    return dict(type='input',now=stamp(at),input=dict(raw_text=text,**context),event=event(slots),busy=busy)
def result(at=1,status='succeeded',id='$0',slot_index=0,executed=None,detail=''):
    return dict(command_id=id,command_type='select_hotbar',status=status,executed_at=stamp(at if executed is None else executed),selected_slot=slot_index,selected_item_id='minecraft:test_sword_0',detail_code=detail)
def results(at=1,rows=None,busy=False): return dict(type='results',now=stamp(at),results=[result(at)] if rows is None else rows,busy=busy)
def session(steps,capable=True):return dict(op='session',capabilities=['client.hotbar.select.v1'] if capable else [],steps=steps)
def normalize(value):
    if isinstance(value,dict): return {k:(datetime.fromisoformat(v.replace('Z','+00:00')).isoformat() if k in {'issued_at','expires_at'} else normalize(v)) for k,v in value.items()}
    if isinstance(value,list): return [normalize(v) for v in value]
    return value

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--python-root',type=Path,default=ROOT);p.add_argument('--binary',type=Path,default=ROOT/'dogido-rust/target/debug/examples/assist_batch');args=p.parse_args()
    sys.path.insert(0,str(args.python_root.resolve()))
    from dogido_server.assist import select_sword as py
    from dogido_server.assist.registry import build_assist_registry
    from dogido_server.llm.assist_prompts import build_select_sword_intent_messages
    from dogido_server.llm.structured_contracts import validate_structured_payload
    from dogido_server.models import GameEvent, HotbarSlot, AdapterCommandResult
    from dogido_server.service import DogidoService, SessionInfo
    from dogido_server.state_machine import AudioAction
    logging.disable(logging.CRITICAL)
    cases=[]
    def add(name,row):cases.append((name,row))
    # Positive combinations plus negation/quotation/past/workshop/observed ASR boundaries.
    texts=set(['','けん','剣','そーど','県','ドギド、県に変えてください','時と県に変えて','剣の耐久値は？','上五を剣にして','最初のパートを剣にして'])
    for prefix,target,verb,suffix in itertools.product(['','ドギド、','今の武器を','斧から','ちょっと','明日は','例えば','時と'],['剣','けん','ソード','つるぎ','そーど'],['にして','に持ち替えて','を選んで','へ変更をして','装備','に構え'],['','ください','ほしいんだけど','くれませんかね']):
        texts.add(prefix+target+verb+suffix)
    for text in ['剣にして','剣に持ち替えて','剣を変更して','剣の方がよくない？','今は剣のほうがええん？']:
        for before,after in [('',''),('「','」'),('','という意味ではない'),('','と言った'),('','場合'),('','って何'),('','とは'),('','の意図'),('','という表現'),('','ないで'),('','ほしくない')]: texts.add(before+text+after)
    for alias,action in itertools.product(['県','件','券','腱','チェン','ケン'],['に変えて','を変えて','にハインコをしてください','に装備して','に持ち替えて','の話','に変えた','に変えてという意味ではない']):
        texts.add(alias+action);texts.add('ドギド、'+alias+action);texts.add('時と'+alias+action)
    for verb in ['持ち替え','もちかえ','切り替え','きりかえ','装備し','選ん','変更し','変え','にし']:
        for ending in ['た','たんだ','ました','たよね','たんですけど','たので','たところ']:
            texts.add('剣'+verb+ending)
    for text in sorted(texts):add('rule:'+text,dict(op='rule',text=text))
    for c in [False,True,-.1,0,.89,.90,1,1.1,'0.99','nan',None]:
        v=payload('剣にして');v['confidence']=c;add('confidence:'+str(c),dict(op='rule',text='剣にして',payload=v))
    for change in [dict(intent='other'),dict(weapon_kind='unknown'),dict(is_request='true'),dict(evidence='剣'),dict(evidence='剣にしてほしい'),dict(extra='x')]:
        v=payload('剣にして');v.update(change);add('shape:'+repr(change),dict(op='rule',text='剣にして',payload=v))
    random.seed(1407)
    for n in range(300):
        slots=[slot(i,random.choice(['sword','trident','axe','bow','tool','other','empty']),random.choice([None,0,1,5,7,10]),random.randrange(200),random.choice([0,100,131,1561])) for i in range(9)]
        for s in slots:
            if random.random()<.15:s['count']=0
            if random.random()<.15:s['item_id']=None
        add('select:'+str(n),dict(op='select',slots=slots))
    for text in ['剣の方がよくない？','県に持ち替えて',' 剣\nにして？ ']:add('messages:'+text,dict(op='messages',text=text))
    seqs={
      'success':[inp(),dict(type='pending',now=stamp(.2)),inp(.3),results(.5),inp(1),results(1),inp(3)],
      'expired':[inp(),results(2,[]),inp(2)],
      'boundary_result':[inp(),results(2,[result(2)]),inp(2)],
      'before_issue':[inp(),results(1,[result(1,executed=-1)]),inp(1)],
      'mismatch':[inp(),results(1,[result(1,slot_index=2)]),inp(1)],
      'late_received_in_time':[inp(),results(5,[result(1)])],
      'reject':[inp(),results(1,[result(1,status='rejected',detail='expected_item_mismatch')]),inp(1)],
      'busy':[inp(busy=True),inp(.2,busy=True),results(1,[result(1,status='failed')],busy=True)],
      'ack_duplicate':[inp(),results(1,[result(.1),result(.1)]),results(1,[result(.1)])],
      'unknown_success':[results(0,[result(0,id='unknown')]),inp(0)],
      'no_candidate':[inp(slots=[])],
      'fallback':[inp(slots=[slot(0,'bow',8),slot(1,'axe',7),slot(2,'trident',6)])],
      'voice':[inp(text='県に変えて',source='voice')],
      'voice_prefix':[inp(text='時と県に変えて',source='voice')],
      'typed_alias':[inp(text='県に変えて')],
      'workshop_bare':[inp(workshop_open=True)],
      'workshop_explicit':[inp(text='剣に装備して',workshop_open=True)],
      'workshop_voice':[inp(text='県に変えて',source='voice',workshop_open=True)],
      'workshop_paused':[inp(workshop_open=True,workshop_combat_paused=True)],
      'natural':[dict(**inp(text='剣の方がよくない？'),payload=payload('剣の方がよくない？'))],
      'negation':[dict(**inp(text='剣にしてという意味ではない'),payload=payload('剣にして'))],
      'slash':[inp(text='/剣にして')],
    }
    for name,steps in seqs.items():add('session:'+name,session(steps))
    add('session:capability',session([inp()],False))
    intentional=[('expire_at_submit',session([inp(),inp(2),results(2,[])])),('unknown_failure',session([results(0,[result(0,status='failed',id='unknown')])]))]
    def py_run(row):
        op=row['op']
        if op=='rule':
            t=row['text'];v=row.get('payload',payload(t))
            return dict(explicit=py.is_explicit_select_sword_request(t),voice_explicit=py.is_explicit_voice_select_sword_request(t),unambiguous=py.is_unambiguous_select_sword_request(t),voice_unambiguous=py.is_unambiguous_voice_select_sword_request(t),repair=py.interpret_voice_select_sword_request(t),requested=py.finalize_select_sword_intent_payload(v,player_text=t).requested,contract=validate_structured_payload('assist_select_sword_intent',v).accepted,workshop=py.has_workshop_edit_context(t))
        if op=='select':
            selected=py.select_weapon_slot([HotbarSlot.model_validate(s) for s in row['slots']]);return dataclasses.asdict(selected) if selected else None
        if op=='messages':return build_select_sword_intent_messages(dict(player_text=row['text']))
        # __new__ avoids service initialization and its I/O. Only pure methods below are called.
        service=DogidoService.__new__(DogidoService);service.assist=build_assist_registry()
        sess=SessionInfo(session_id='offline',schema_version='2026-05-24',adapter_name='test',adapter_version='1',game='minecraft-java',player_name='test',profile_name=None,call_name=None,capabilities=[],execution_capabilities=row['capabilities'],created_at=BASE,machine=SimpleNamespace())
        ids=[];outputs=[]
        def command(c):return {k:v for k,v in c.model_dump(mode='json').items() if k not in {'command_id','type'}}
        def alias(id):return '$'+str(ids.index(id)) if id in ids else id
        for step in row['steps']:
            now=datetime.fromisoformat(step['now']);speech=[AudioAction(layer='speech',text='combat',interrupt=False)] if step.get('busy') else []
            if step['type']=='input':
                context=step['input'];raw=context['raw_text'];source=context.get('source','text')
                interpreted=context.get('interpreted_text')
                if source=='voice' and not interpreted:interpreted=py.interpret_voice_select_sword_request(raw)
                sess.haiku_workshop=SimpleNamespace(open=True,combat_paused=context.get('workshop_combat_paused',False)) if context.get('workshop_open') else None
                calls=[]
                def llm(request):calls.append(request);return step.get('payload',dict(intent='other',weapon_kind='unknown',is_request=False,evidence='',confidence=0.0))
                service.llm=SimpleNamespace(route_enabled=lambda route:True,generate_structured_json=llm)
                routed=service._route_assist_player_input(sess,raw,interpreted_player_text=interpreted,input_source=source)
                sess.machine.player_input=routed
                if not routed.requests_sword:
                    outputs.append(dict(handled=False,needs_intent=bool(calls and 'payload' not in step)));continue
                commands,actions=service._assist_actions(sess,GameEvent.model_validate(step['event']),speech,now=now)
                if commands:ids.append(commands[0].command_id)
                outputs.append(dict(handled=True,command=command(commands[0]) if commands else None,feedback=actions[0].text if actions else None))
            elif step['type']=='results':
                rows=[]
                for v in step['results']:
                    v=v.copy()
                    if v['command_id'].startswith('$'):v['command_id']=ids[int(v['command_id'][1:])]
                    rows.append(AdapterCommandResult.model_validate(v))
                ack,observed=service._consume_command_results(sess,rows,now=now)
                actions=service._command_result_feedback_actions(observed,speech)
                outputs.append(dict(acked=[alias(id) for id in ack],observed=[dict(id=alias(r.command_id),status=r.status,detail=r.detail_code) for r in observed],feedback=actions[0].text if actions else None))
            else:outputs.append([command(c) for c in service._pending_commands_for_response(sess,now)])
        return outputs
    all_cases=cases+intentional
    process=subprocess.run([str(args.binary.resolve())],input=''.join(json.dumps(row,ensure_ascii=False)+'\n' for _,row in all_cases),text=True,capture_output=True,check=True)
    rust=[json.loads(line) for line in process.stdout.splitlines()]
    assert len(rust)==len(all_cases)
    failed=[]
    for (name,row),actual in zip(cases,rust):
        expected=normalize(py_run(row));actual=normalize(actual)
        if expected!=actual:failed.append(dict(case=name,python=expected,rust=actual))
    print(json.dumps(dict(parity_cases=len(cases),failures=len(failed),intentional_cases=len(intentional)),ensure_ascii=False))
    for failure in failed[:20]:print(json.dumps(failure,ensure_ascii=False))
    intentional_results=rust[len(cases):]
    assert intentional_results[0][1]['command'] is not None
    assert intentional_results[0][1]['feedback']!='もう持ち替えたで！'
    assert intentional_results[0][2]['observed'][0]['detail']=='server_result_timeout'
    assert intentional_results[1][0]['acked']==['unknown'] and intentional_results[1][0]['feedback'] is None
    for (name,row),actual in zip(intentional,intentional_results):
        assert normalize(py_run(row))!=normalize(actual),name
        print(json.dumps(dict(intentional=name,reason='期限はsubmitでも消費し、成功未確認を断定しない' if name=='expire_at_submit' else '未知resultはACKのみ、失敗案内をしない'),ensure_ascii=False))
    return bool(failed)
if __name__=='__main__':raise SystemExit(main())
