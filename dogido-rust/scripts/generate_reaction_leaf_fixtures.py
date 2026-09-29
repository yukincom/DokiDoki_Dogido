#!/usr/bin/env python3
"""Freeze the canonical Python reaction contracts; no model, audio or server calls.

Run with the project's Python, --source-root pointing at the Python checkout.
Outputs are prompt data plus deterministic comparison fixtures, used by Rust tests.
"""
from pathlib import Path
import argparse
import ast
import json
import sys

KINDS = ('death aftermath daylight_water_skeleton newly_burning_visual '
    'deep_dark_ominous_sound occluded_hostile_presence ambient weather_transition '
    'ender_eye_throw structure_entry light_source_gain darkness_escape '
    'occluded_entry_with_light occluded_entry_no_light dark_push_no_light '
    'dark_push_after_breath emergency_shelter_relief portal_appearance').split()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--source-root', type=Path, required=True)
    p.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'src/reaction_leaf')
    args = p.parse_args()
    sys.path.insert(0, str(args.source_root))
    from dogido_server.llm import character_mode as cm
    from dogido_server.llm.prompts import build_messages
    from dogido_server.llm.types import LeafGenerationRequest
    from dogido_server.llm.client import DogidoLLM
    from dogido_server.llm.sanitize import clean_output, usability_rejection_reason, is_style_acceptable
    from dogido_server.config import Settings
    from dogido_server.entry_catalog import all_mob_entries
    from dogido_server.state_machine import DogidoStateMachine
    tree = ast.parse((args.source_root / 'dogido_server/llm/reaction_prompts.py').read_text())
    templates = {}
    for kind in KINDS:
        fn = next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == f'_build_{kind}_messages')
        expr = next(x.value for x in fn.body if isinstance(x, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'user_prompt' for t in x.targets))
        parts = []
        for bit in expr.values:
            if isinstance(bit, ast.Constant): parts.append({'text': bit.value}); continue
            v = bit.value
            if isinstance(v, ast.Call) and ast.unparse(v.func) == 'details.get':
                parts.append({'detail': ast.literal_eval(v.args[0]), 'default': ast.literal_eval(v.args[1])})
            elif isinstance(v, ast.Name): parts.append({'field': v.id})
            elif ast.unparse(v) == "hostile or 'なし'": parts.append({'field': 'death_hostile'})
            else: raise ValueError(ast.unparse(v))
        templates[kind] = parts
    prompt_data = {'systems': {m: cm.system_prompt_for_mode(m) for m in ['peace', 'battle', 'tension', 'workshop']},
        'default_modes': {k: cm.character_mode_for_request(k, {}) for k in KINDS}, 'templates': templates}
    settings = Settings(_env_file=None, llm_enabled=True, llm_backend='chat_completions',
        llm_provider='local', llm_model='fixture-model', llm_max_tokens=72, audio_enabled=False, memory_enabled=False)
    machine = object.__new__(DogidoStateMachine)
    class Probe(DogidoLLM):
        def __init__(self, text, error=False):
            super().__init__(settings); self.text = text; self.error = error; self.calls = []
        def _generate_backend_text(self, request):
            self.calls.append({'kind': request.kind, 'temperature': request.temperature,
                'max_tokens': request.max_tokens or self.settings.llm_max_tokens,
                'enable_thinking': False, 'messages': build_messages(request)})
            if self.error: raise RuntimeError('fixture backend error')
            return self.text
    base = dict(player_name='Player_1', biome='草原', time_phase='day', hostiles=['ゾンビ','スケルトン'],
        hostile='ゾンビ', distance=7.5, local_light=3, count=2, craftable=True,
        mob='村人', direction='左', mob_count=3, health_state='少し消耗', portal_distance=4.0,
        ceiling_height=2, enclosure_score=0.8, fallback_candidates=['ほっ、助かるわ。']*6,
        mob_tags=['働く','近所','散歩','穏やか','休む','子供','あふれ'], reference_lines=['そうなんやな。']*6,
        structure_label='村', structure_note=' 村人たちが暮らしている。 ', forbidden_advice=[])
    variants = [{}, base, {**base,'character_mode':'workshop'}, {**base,'character_mode':'COMBAT'},
        {**base,'character_mode':'unknown'}, {**base,'character_mode':'平和'},
        {**base,'character_mode':'緊張'}]
    for outcome in ['player_kill','charged_creeper_detonated','creeper_detonated','explosion_death','hostile_defeated','disengaged','other','']:
        for clear in [True, False]: variants.append({**base,'combat_outcome':outcome,'hostile_clear_confirmed':clear})
    for slot in [-5, 0, 1, 2, 3, 8]: variants.append({**base,'variation_slot':slot, 'mob_profession':'農民', 'villager_schedule_ja':'仕事'})
    variants += [{**base,'mob_is_baby':True},{**base,'villager_schedule_ja':'散歩'},
        {**base,'mob_profession':'農民'}, {**base,'comment_action':'relief_after_darkness'},
        {**base,'time_phase':'evening'}, {**base,'time_phase':'night'}, {**base,'ominous_stage':3}]
    for portal in ['nether_portal','end_portal','end_gateway','unknown']:
        variants.append({**base,'portal_type':portal,'portal_label':'ゲート'})
    for cold in [False,True]:
        for dry in [False,True]:
            for thunder in [False,True]:
                for near in [False,True]: variants.append({**base,'cold_biome':cold,'dry_biome':dry,'thunder_reaction':thunder,'nearby_lightning':near})
    variants += [{k:None for k in ['player_name','biome','time_phase','craftable','distance','count']},
        {**base,'variation_slot':'2','ominous_stage':'2','mob_temperament':'  neutral  ','mob_role':'友達'},
        {**base,'player_name':'{field} {{player_name}}','mob':'{{placeholder}}'},
        {**base,'hostiles':[],'fallback_candidates':[],'reference_lines':[], 'mob_tags':[], 'hostile':''}]
    prompts=[]
    for kind in KINDS:
        for details in variants:
            req=LeafGenerationRequest(kind=kind, fallback_text='ひとまず落ち着いたな。', details=details, temperature=.65, route='chat')
            prompts.append({'kind':kind,'details':details,'messages':build_messages(req)})
    raw=['', 'うん', '助かったわ。', 'Player_1、ほっとしたわ。', '「ほっとしたわ。」',
        '<think>試案\nまだ検討</think>\nドギド: ほっとしたわ。<|im_end|>',
        "Here's a thinking process: First consider. Final answer: ほっとしたわ。",
        '1. **Role**\nPersona: helpful\nAnswer: ほっとしたわ。',
        '英語の説明です\nHere is a thinking process\n最後は安心やな。',
        'OK、ほっとしたわ。', 'NGやけど大丈夫やで。', 'OKAYな場所やで。',
        'assistant: ほっとしたわ。', '例2: ほっとしたわ。', 'ドギド：助かったわ。',
        '助かったああああ！','何とか何とか何とか助かったわ。',
        '怖い！！怖い…怖いやで。','やわ！！やん！！やろ。','怖いんかやんか。',
        'ゾンビがおる、その場で待とうや。', 'みんな動かないでな。',
        '倒したで、助かったわ。','敵が倒れたんやな。','爆発したんやな。',
        'これで回復できるな。','あと3体やで。', 'すごいだよね。',
        'ここは俺には無理や。','ここはもう無理です。', '陸に来てくれや。',
        '火をつけてくれへん？', 'やばい、助かったね。','敵が見えたで。',
        'クラフトできたんやな。','松明を四本持っとるな。','ランタンができたんやな。',
        '明かりは５個になったな。','作った明かりで安心やな。','準備が増えて安心やな。',
        'モンスター!!!', '1234ああああ', 'ＡＢＣはよかったな。', 'aあbいcうdえ',
        '「前はこうや」ってことやな。', '「怖いわ。', 'ほっとしたわ。\n少し安心やな。',
        'AAAA\n変な英語ですAB', 'Role: helper', '  ', '𠀀助かったわ。', '²なら助かるわ。']
    # Vary every guard with absent/explicit/catalogue-derived hostile context.
    contexts=[{}, {'player_name':'Player_1'}, {'has_visual_threats':True}, {'combat_active':True},
        {'mode':'alert'}, {'character_mode':'battle'}, {'mode':'panic'},
        {'nearby_hostile_types':['minecraft:creeper']}, {'nearby_mob_ids':['enderman']},
        {'hearing_summary':'敵の気配'}, {'event_digest':'視認中'}, {'forbidden_advice':['安心']},
        {'forbidden_advice':['']}, {'combat_outcome':'hostile_defeated'}, {'combat_outcome':'player_kill'},
        {'combat_outcome':'explosion_death'}, {'combat_outcome':'charged_creeper_detonated'},
        {'combat_outcome':'creeper_detonated'}, {'combat_outcome':'disengaged'}, {'combat_outcome':None}]
    sanitizers=[]
    for kind in KINDS:
        for details in contexts:
            for text in raw:
                cleaned=clean_output(text)
                reason=usability_rejection_reason(cleaned,details)
                style=is_style_acceptable(kind,cleaned,details)
                guard=(kind=='aftermath' and machine._aftermath_claim_conflicts(str(details.get('combat_outcome','disengaged')),cleaned)) or (kind=='light_source_gain' and machine._invalid_light_source_gain_claim(cleaned))
                sanitizers.append({'kind':kind,'details':details,'raw':text,'cleaned':cleaned,
                    'usable':reason is None,'style':style,'guard':bool(guard),
                    'selected':cleaned if reason is None and style and not guard else 'ひとまず落ち着いたな。'})
    # Exercise catalogue-derived advice, including the canonical namespace normalization.
    for mob,entry in all_mob_entries().items():
        for pattern in (entry.get('dogido_tactics') or {}).get('forbidden_advice',[]):
            for mob_id in [mob,'minecraft:'+mob,' MINECRAFT:'+mob+' ','minecraft:minecraft:'+mob]:
                kind='ambient'; details={'nearby_mob_ids':[mob_id]}; text=pattern+'のはちょっと怖いわ。'
                cleaned=clean_output(text); usable=usability_rejection_reason(cleaned,details) is None
                style=is_style_acceptable(kind,cleaned,details)
                sanitizers.append({'kind':kind,'details':details,'raw':text,'cleaned':cleaned,'usable':usable,'style':style,'guard':False,
                    'selected':cleaned if usable and style else 'ひとまず落ち着いたな。'})
    attempts=[]
    import logging
    logging.disable(logging.CRITICAL)
    # Temperature is still chosen by existing Rust state machines, passed through unchanged.
    temperatures=dict(zip(KINDS,[.2,.2,.6,.72,.6,.58,.48,.66,.4,.55,.48,.62,.5,.42,.58,.5,.5,.55]))
    for kind in KINDS:
        for temperature in ([.66,.72] if kind=='weather_transition' else [temperatures[kind]]):
            for limit in [72,512]:
                settings.llm_max_tokens=limit
                for text,error in [('助かったわ。',False),('English explanation',False),('',False),('',True)]:
                    details={'combat_outcome':'disengaged'}
                    request=LeafGenerationRequest(kind=kind, fallback_text='ひとまず落ち着いたな。', details=details, temperature=temperature, route='chat')
                    probe=Probe(text,error); output=probe.generate_leaf_text(request)
                    attempts.append({'kind':kind,'raw':text,'error':error,'expected':output,'calls':probe.calls})
    args.output.mkdir(parents=True,exist_ok=True)
    digit_ranges=[]
    for i in range(0x110000):
        if chr(i).isdigit():
            if digit_ranges and i == digit_ranges[-1][1]+1: digit_ranges[-1][1]=i
            else: digit_ranges.append([i,i])
    # Intern repeated fixture strings and details, keeping every comparison case.
    pool=[]; seen={}
    def intern(value):
        key=json.dumps(value,ensure_ascii=False,sort_keys=True)
        if key not in seen: seen[key]=len(pool); pool.append(value)
        return seen[key]
    packed={'schema_version':1,'pool':pool,
        'prompts':[[k,intern(r['details']),intern(r['messages'])] for r in prompts for k in [r['kind']]],
        'sanitizers':[[r['kind'],intern(r['details']),intern(r['raw']),intern(r['cleaned']),r['usable'],r['style'],r['guard'],intern(r['selected'])] for r in sanitizers],
        'attempts':attempts}
    for name,value in [('digit-ranges.json',digit_ranges),('prompts.json',prompt_data),('fixtures.json',packed)]:
        (args.output/name).write_text(json.dumps(value,ensure_ascii=False,separators=(',',':'))+'\n')
    print(f'prompt cases={len(prompts)}, sanitizer cases={len(sanitizers)}, attempts={len(attempts)}')

if __name__=='__main__': main()
