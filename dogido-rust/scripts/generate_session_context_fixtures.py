#!/usr/bin/env python3
"""Canonical workshop speech projection only; no model, I/O services, state mutations."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

def main():
    p=argparse.ArgumentParser();p.add_argument('--source-root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    sys.path.insert(0,str(a.source_root))
    from dogido_server.haiku.workshop import RecentHaikuWorkshop,workshop_prompt_details
    def lines(readings):
        return [dict(line_id=f'line_{i+1}',line_index=i,position=['upper','middle','lower'][i],canonical_name=['上五','中七','下五'][i],surface_text=text,reading_text=text,source_atom_ids=[],source_atoms=[],provenance='generated') for i,text in enumerate(readings)]
    readings=['あさのそら','ひかりがもれる','あおいくさ']
    pending=['ゆうのそら','ひかりがもれる','あおいくさ']
    materials=[{}, {'held_item':'石'}, {'held_item':'丸石'}, {'held_item':' 丸石。 '}, {'held_item':'なし'}, {'held_item':'いる'},
        {'motifs':['静寂','深い森'],'held_item':'鉄の斧'}, {'nearby_blocks':['葉っぱ'],'inventory_items':['たいまつ']},
        {'held_item':'静寂','motifs':['丸石']},{'motifs':['丸石'],'held_item':'丸石','nearby_blocks':['葉っぱ']},
        {'dropped_items':['丸石'],'passive_mobs':['ネコ']}, {'place_ja':'地下洞窟','structure_ja':'村'},
        {'biome':'minecraft:plains','structure':'minecraft:village'}, {'time_phase':'evening'},
        {'interpretation':'プレイヤーの横には森。木々が揺れている、黒い剣'},
        {'held_item':'とてもとても長くて長いプレイヤーのネザライトの剣'},
        {'inventory_items':['あり','かき','阿吽','伊勢'],'nearby_blocks':['葉っぱ','丸石']},
        {'material_visibility':{'biome':False,'sky':False}},
        {'biome':None,'time_phase':None,'interpretation':None}]
    for phase in ['morning','day','evening','night','unknown']:
        materials.append({'time_phase':phase,'interpretation':'朝の草地、静かな空'})
    for field in ['held_item','motifs','nearby_blocks','inventory_items','passive_mobs','dropped_items','biome_ja','structure_ja','place_ja','interpretation']:
        materials.append({field:['静寂','葉っぱ'] if field.endswith('s') else '葉っぱ'})
    rows=[]
    for m in materials:
        for has_pending in [False,True]:
            for metadata in [{},{'interpretation':'朝の草地','biome':'cherry_grove','structure':'village','time_phase':'evening'}]:
                w=RecentHaikuWorkshop(surface_text='\n'.join(readings),emitted_at=datetime(2026,9,29,tzinfo=timezone.utc),materials=deepcopy(m),pending_revision='\n'.join(pending) if has_pending else None,**metadata)
                view={'emission':metadata,'current_lines':lines(readings),'pending':{'lines':lines(pending)} if has_pending else None,'materials':deepcopy(m),'dialogue':[],'agent_steps':[]}
                rows.append({'view':view,'expected':workshop_prompt_details(w)})
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text('\n'.join(json.dumps(row,ensure_ascii=False) for row in rows)+'\n')
    paths=['dogido_server/haiku/workshop.py','dogido_server/haiku/materials.py']
    a.output.with_suffix('.meta.json').write_text(json.dumps({'cases':len(rows),'canonical':{str(path):hashlib.sha256((a.source_root/path).read_bytes()).hexdigest() for path in paths}},ensure_ascii=False,indent=2)+'\n')
    print(len(rows))
if __name__=='__main__':main()
