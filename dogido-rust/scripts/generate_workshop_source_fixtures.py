"""Export canonical pure source/workshop projections; no models or SDK calls."""
from __future__ import annotations
import argparse
import copy
from dataclasses import asdict
import itertools
import json
from pathlib import Path
from types import SimpleNamespace
import sys
ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--canonical-root',type=Path,default=ROOT.parent)
    parser.add_argument('--output-root',type=Path,default=ROOT/'src');args=parser.parse_args()
    sys.path[:0]=[str(args.canonical_root),str(args.canonical_root/'dogido-rust/scripts')]
    from dogido_server.haiku import source_atoms as src
    from dogido_server.haiku import workshop as ws
    from dogido_server.haiku import workshop_agent as agent
    from dogido_server.haiku.workshop_context import workshop_context_details
    from dogido_server import tts_reading
    from dogido_server.entry_catalog import all_mob_entries,structure_entries
    import workshop_oracle as helper
    def forbidden():raise AssertionError('pure fixtures must not initialize a dictionary')
    tts_reading._get_unidic_tagger=forbidden
    out=args.output_root;atom_out=out/'haiku/source_atoms';projection_out=out/'workshop_projection'
    atom_out.mkdir(parents=True,exist_ok=True);projection_out.mkdir(parents=True,exist_ok=True)
    patterns={'meaning_ack':ws._MEANING_ACK_PATTERN.pattern,'close_accept':ws._CLOSE_CONFIRM_ACCEPT_PATTERN.pattern,
        'close_continue':ws._CLOSE_CONFIRM_CONTINUE_PATTERN.pattern,'combat_accept':ws._COMBAT_RESUME_ACCEPT_PATTERN.pattern,
        'combat_decline':ws._COMBAT_RESUME_DECLINE_PATTERN.pattern}
    (projection_out/'followup_patterns.json').write_text(json.dumps(patterns,ensure_ascii=False,indent=2)+'\n')
    def wire(v):return json.loads(json.dumps(v,ensure_ascii=False))
    rows=[]
    def put(op,inputs,expected):rows.append(dict(op=op,input=wire(inputs),expected=wire(expected)))
    sources=[]
    for typ,entries in [('mob',all_mob_entries()),('structure',structure_entries())]:
        for key,entry in entries.items():
            kw=dict(catalog_type=typ,catalog_id=key,entry=entry,observation_role='currently_observed',fallback_label='')
            s=src.catalog_source_snapshot(**kw)
            put('snapshot',kw,dict(snapshot=asdict(s) if s else None,projection=s.to_dict() if s else None))
            if s:sources.append(s)
    for entry in [None,[],False,{},dict(japanese=' ',label='fallback'),dict(japanese=False,label=True,note=17,reading=['a',None]),
        dict(label='猫',note=' 赤い。 茶色で、ひび割れている！？最後\nもある',poetic=dict(role=' 役\n割 ',visual_tags=['',None,False,17,{},[], '白い'],motion_tags='not list',scene_tags=['灰', '光'],reaction_tags=['驚く']))]:
        for key in ['', ' ', 'minecraft:CAT', ' minecraft:CAT ', 'minecraft:']:
            kw=dict(catalog_type=' mob ',catalog_id=key,entry=entry,observation_role=' 観測 ',fallback_label='補助')
            s=src.catalog_source_snapshot(**kw);put('snapshot',kw,dict(snapshot=asdict(s) if s else None,projection=s.to_dict() if s else None))
    for raw in ['', ' abc ', '甲、乙。 丙！丁??\n戊', '。！？!?', '長'*105, '🐈\u001c山。\u001f川']:
        put('split',raw,src.split_note_sentences(raw))
    for group in [sources[:4],sources[-3:],sources[:2]+sources[:2],[],[src.CatalogSourceSnapshot('type','id','猫','長'*99+' \n'+'a','', 'role', (('..path[a]', 'a'),('..path b','b'),('...','c'),('日本語','d'),('A__B','e')))]]:
        for notes,extra in itertools.product([-1,0,1,3,8],[-2,-1,0,1,5,8]):
            put('catalog_atoms',dict(sources=[asdict(s) for s in group],max_note_atoms=notes,max_extra_atoms_per_source=extra),[asdict(a) for a in src.atoms_from_catalog_sources(group,max_note_atoms=notes,max_extra_atoms_per_source=extra)])
        put('notes',[asdict(s) for s in group],src.catalog_notes_projection(group))
    features=[{},dict(source=' sky!-high ',key='time',label='夜'),dict(source='和文',key='time',label='夜'),dict(source=None,key='rain',label='雨'),dict(source=' ',key='x',label='不明'),dict(source='音',key=' ',label='音'),dict(source=False,key=True,label=17),dict(source='A__B',key='x',label='見えた')]
    for group in [[],features,features+features,features[::-1]]:
        put('observations',group,[asdict(a) for a in src.atoms_from_observations(SimpleNamespace(**f) for f in group)])
    primary=[src.HaikuSourceAtom('label','猫','mob:cat','japanese','current','catalog_label','factual',('identity_only',)),
        src.HaikuSourceAtom('note','白い猫','mob:cat','note[0]','current','catalog_fact','factual',('source_meaning',)),
        src.HaikuSourceAtom('field','素早い','mob:cat','poetic.role','current','catalog_field','interpretive',('source_meaning',)),
        src.HaikuSourceAtom('obs','夜','observation:time','observed_label','time','observation','factual',('observed_state',)),
        src.HaikuSourceAtom('dialogue','冒険した','dialogue:t','text','player','dialogue_material','factual',('player_reported_context',))]
    def clauses_case(raw,atoms=primary):
        c=src.preface_clauses_from_payload(raw,source_atoms=atoms)
        put('clauses',dict(raw=raw,atoms=[asdict(a) for a in atoms]),[asdict(v) for v in c] if c is not None else None)
        if c is not None:
            put('derived',[asdict(v) for v in c],dict(atoms=[asdict(a) for a in src.atoms_from_preface_clauses(c)],interpretation=asdict(src.atom_from_poetic_interpretation(c)) if src.atom_from_poetic_interpretation(c) else None))
    for raw in [None,False,{},[],[None],[{}],['x']]:clauses_case(raw)
    for sentence,ids,claim in itertools.product(['猫が白い','a','a'*36,'a'*37,'猫\n白い',' 「猫が白い。」 ', 'プレイヤーの猫','Y座標が違う','確率が高い','猫🐈',17],
        [['label'],['field'],['obs','note','label','dialogue'],['label','label'],['missing'],[],[''],[None],[' label ']],['factual','interpretive','invalid']):
        clauses_case([dict(text=sentence,basis_atom_ids=ids,claim_class=claim)])
    for raw in [
        [dict(text='猫が白い',basis_atom_ids=['label'],claim_class='factual')]*2,
        [dict(text=t,basis_atom_ids=['label'],claim_class='interpretive') for t in ['猫が 白い','猫が白い']],
        [dict(text=t,basis_atom_ids=['label'],claim_class='interpretive') for t in ['あ'*24,'い'*24,'う'*24]],
        [dict(text=t,basis_atom_ids=['label'],claim_class='interpretive') for t in ['あ'*25,'い'*24,'う'*24]],
        [dict(text='猫が白い',basis_atom_ids=['label'],claim_class='factual')]*4]:clauses_case(raw)
    validclauses=src.preface_clauses_from_payload([dict(text='白い猫',basis_atom_ids=['label','note'],claim_class='factual'),dict(text='夜を走るよう',basis_atom_ids=['obs','field'],claim_class='interpretive')],source_atoms=primary)
    derived=list(src.atoms_from_preface_clauses(validclauses));interpretation=src.atom_from_poetic_interpretation(validclauses)
    all_atoms=primary+derived+[interpretation]
    for clauses in [[],[src.PrefaceClause(' ',('label',),'factual',('identity_only',))], [src.PrefaceClause('猫が白い',(), 'factual',('identity_only',))],validclauses]:
        p=src.atom_from_poetic_interpretation(clauses);put('derived',[asdict(v) for v in clauses],dict(atoms=[asdict(a) for a in src.atoms_from_preface_clauses(clauses)],interpretation=asdict(p) if p else None))
    for groups in [[primary,derived,[interpretation]], [primary,primary[::-1]], [[primary[0]],[src.HaikuSourceAtom('copy','猫','mob:cat','','','catalog_label','factual',('identity_only',))],derived], [derived,primary], [], [all_atoms,all_atoms]]:
        put('merge',[[asdict(a) for a in g] for g in groups],[asdict(a) for a in src.merge_source_atoms(*groups)])
    valid=[wire(asdict(a)) for a in all_atoms]
    materials_list=[None,{},[],{'source_atoms':None},{'source_atoms':[]},{'source_atoms':valid},{'source_atoms':list(reversed(valid))}]
    values=[None,True,3,'', 'unknown', ' factual ',[],{},['identity_only'],['identity_only','identity_only'],['poetic_interpretation'],['source_meaning'],[' label '],['label',' label '],['label','label']]
    for i in range(len(valid)):
        for key in valid[i]:
            for value in values:
                atoms=copy.deepcopy(valid);atoms[i][key]=value;materials_list.append({'source_atoms':atoms})
    materials_list.extend([{'source_atoms':[None,{},False,*valid]}, {'source_atoms':[dict(valid[0],kind='wrong'),*valid]}, {'source_atoms':[valid[0],dict(valid[0],text='changed'),*valid[1:]]}])
    for material in materials_list:
        put('stored',material,[asdict(a) for a in src.source_atoms_from_materials(material)])
    verse=['さくらのは','くろいおのへと','あさのいろ']
    line_base=[dict(line_index=i,text=line,atom_ids=[['label'],['note'],['obs']][i]) for i,line in enumerate(verse)]
    for index,value in itertools.product(['line_index','text','atom_ids'],[None,True,False,3,0,1,2,-1,0.0,'さくらのは',' さくらのは ',[],['label'],['missing'],['label','label'],['preface:spoken:interpretation'],['field'],[' label ']]):
        lines=copy.deepcopy(line_base);lines[0][index]=value
        m=dict(source_atoms=valid,line_sources=lines);allowed={a.atom_id for a in all_atoms}
        put('line_sources',dict(materials=m,verse_lines=verse,allowed_atom_ids=sorted(allowed)),src.line_source_ids_from_materials(m,verse_lines=verse,allowed_atom_ids=allowed))
    for lines in [line_base+line_base, list(reversed(line_base)), [dict(r,atom_ids=['preface:spoken:interpretation']) for r in line_base], [dict(r,atom_ids=['label']) for r in line_base]]:
        for allowed in [set(),{'label'}, {a.atom_id for a in all_atoms}]:
            m=dict(source_atoms=valid,line_sources=lines);put('line_sources',dict(materials=m,verse_lines=verse,allowed_atom_ids=sorted(allowed)),src.line_source_ids_from_materials(m,verse_lines=verse,allowed_atom_ids=allowed))
    (atom_out/'fixtures.json').write_text(json.dumps(rows,ensure_ascii=False,separators=(',',':'))+'\n')
    # Workshop data is captured through the production snapshot builder; no edit
    # or whole-verse reading operation is called.
    def baseframe():
        lines=[dict(line_id=f'line_{i+1}',line_index=i,position=['upper','middle','lower'][i],canonical_name=['上五','中七','下五'][i],surface_text=['桜の葉','黒い斧へと','朝の色'][i],reading_text=line,source_atom_ids=[primary[i].atom_id],source_atoms=[asdict(primary[i])],provenance='generated') for i,line in enumerate(verse)]
        return dict(text='この句の意味を教えて',phase='decide',observation=None,turn_steps=[],allowed_actions=sorted(agent.WORKSHOP_AGENT_ACTIONS)+sorted(helper.FOLLOWUP_ACTIONS),workshop=dict(materials={'source_atoms':valid,'line_sources':line_base,'interpretation':'材料の解釈'},current_lines=lines,emission=dict(lines=copy.deepcopy(lines),created_at='2026-09-26T10:00:00+00:00',interpretation='句の解釈'),dialogue=[],agent_steps=[]))
    projection=[]
    for phase,pending,stage,semantic,candidate,idea in itertools.product(['decide','after_inspection','after_validation'],[False,True],['discussion','meaning_explained','close_confirmation','combat_resume_confirmation'],[False,True],[False,True],[False,True]):
        f=baseframe();f['phase']=phase;f['workshop']['followup']=stage
        if pending:
            lines=copy.deepcopy(f['workshop']['current_lines']);lines[0]['surface_text']='桜色';lines[0]['reading_text']='さくらいろ';lines[0]['source_atom_ids']=['obs'];f['workshop']['pending']=dict(lines=lines,base=f['workshop']['current_lines'],generated_basis={'kept':True})
        if semantic:f['interpreted_text']=' 句を教えて ';f['text']=' 認識原文を残す '
        if candidate:f['workshop']['conversation_candidate']={'proposal':{'line_index':0},'evidence':'今の案'}
        if idea:f['workshop']['current_player_idea']={'replacement':'桜色'}
        f['allowed_actions']+=['stage_conversation_candidate','confirm_close','confirm_close']
        f['workshop']['dialogue']=[dict(turn_id='t0',player_text='何の対比？',dogido_text='葉と斧。'),dict(turn_id='t1',player_text='読みを教えて',dogido_text='さくらのは')]
        f['turn_steps']=[dict(action='inspect',status='measured')];f['workshop']['agent_steps']=[dict(action='ask'),*f['turn_steps']]
        f['observation']={'kind':'inspection','lines':[{'line_index':0,'mora_count':5}]}
        projection.append(dict(op='details',input=f,expected=helper.details_for(f)))
        rf=dict(f,op='revision_input',findings=[dict(line_index=i,problem='reading') for i in [0,2,None,True,1,1]],max_tokens=777,grounding_max_tokens=512)
        projection.append(dict(op='revision',input=rf,expected=wire(helper.handle(rf))))
    variants=[]
    f=baseframe();f['workshop']['dialogue']=[dict(turn_id=str(i),player_text='語'*330,dogido_text='答\n\u001c'+'え'*330) for i in range(8)];variants.append(f)
    for dialogue in [[],[dict(turn_id='same',player_text='話',dogido_text='ハッ')]*4,[dict(turn_id='',player_text='同じ',dogido_text='同じ')]*4,[dict(turn_id=' x\n1 ',player_text=' \u001c',dogido_text='ハァハァ……'),dict(turn_id='x 1',player_text='新しい',dogido_text='答え')]]:
        f=baseframe();f['workshop']['dialogue']=dialogue;variants.append(f)
    f=baseframe();f['workshop']['emission']['interpretation']=None;f['workshop']['materials']['interpretation']='見'*1500;variants.append(f)
    f=baseframe();f['workshop']['agent_steps']=[dict(i=i) for i in range(20)];f['turn_steps']=[dict(i=i) for i in range(14,20)];variants.append(f)
    for steps in [[dict(i=18),dict(i=99)],[],[dict(i=i) for i in range(20)]]:
        f=baseframe();f['workshop']['agent_steps']=[dict(i=i) for i in range(20)];f['turn_steps']=steps;variants.append(f)
    f=baseframe();f['allowed_actions']=['stage_conversation_candidate','ask','acknowledge_meaning','ask','confirm_close'];f['workshop']['conversation_candidate']={};variants.append(f)
    f=baseframe();f['workshop']['materials']['source_atoms']=[dict(valid[0],atom_id=f'atom:{i}',text='語'*260) for i in range(30)];f['workshop']['current_lines'][0]['source_atom_ids']=['atom:29'];variants.append(f)
    for f in variants:projection.append(dict(op='details',input=f,expected=helper.details_for(f)))
    # Exercise the full pure Snapshot contract beyond the helper's omitted fields.
    for lastfinding,feedback,pending in itertools.product([[],[dict(i=i) for i in range(5)]],[{},dict(base_text='wrong',status='failed'),dict(base_text='\n'.join(verse),status='passed')],[False,True]):
        f=baseframe()
        if pending:f['workshop']['pending']=dict(lines=copy.deepcopy(f['workshop']['current_lines']),base=f['workshop']['current_lines'])
        w=helper.snapshot_for(f);w.last_findings=lastfinding;w.last_repair_feedback=feedback
        snapshot={k:getattr(w,k) for k in ['surface_text','pending_revision','pending_revision_surface_text','interpretation','materials','agent_steps','last_findings','last_repair_feedback','awaiting_meaning_ack','awaiting_close_confirmation']}
        snapshot['current_lines']=[asdict(l) for l in w.current_lines];snapshot['pending_revision_lines']=[asdict(l) for l in w.pending_revision_lines];snapshot['recent_dialogue']=w.dialogue.prompt_blocks()['conversation_history']
        projection.append(dict(op='context',input=snapshot,expected=workshop_context_details(w)))
    follows=[]
    texts=['なるほど','そうなんだ','ああ、そうなんやな','そういうことか','わかったよ','理解した','腑に落ちた','うん','はい','ええよ','いいよ','そうしよう','それでいい','ここまで','終了にしよう','いや、続けたい','もう少し','ここまでじゃない','OK','お願い','大丈夫、続けて','再開しよう','句の話を続けてください','もうやめよう','もういい','続けない','もう、終了','いいよとは言ってない','もし続けたら','「いいよ」','なるほど、直して','まだわからない','よかった','']
    for text,prefix,suffix,stage,pending in itertools.product(texts,['','\u001c ','うん、'],['','！','?','？','なら','って聞いた','\n'],['discussion','meaning_explained','close_confirmation','combat_resume_confirmation'],[False,True]):
        value=prefix+text+suffix;frame=dict(op='fixed_followup',text=value,stage=stage,pending=pending)
        follows.append(dict(text=value,stage=stage,pending=pending,expected=helper.handle(frame)['action']))
    for row in projection:
        row['expected_json']=json.dumps(row['expected'],ensure_ascii=False)
    (projection_out/'fixtures.json').write_text(json.dumps(dict(projection=projection,followup=follows),ensure_ascii=False,separators=(',',':'))+'\n')
    print(json.dumps(dict(source_cases=len(rows),projection_cases=len(projection),followup_cases=len(follows))))
if __name__=='__main__':main()
