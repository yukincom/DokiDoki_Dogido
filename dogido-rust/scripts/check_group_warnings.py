#!/usr/bin/env python3
"""群れの実HTTP配送。モデル/TTS/playerは模擬、実マイク・音声出力なし。"""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import time
from check_dialogue import dependencies, running, register, request, snapshot, wait_for, row
from compare_threats import event

ROOT=Path(__file__).resolve().parents[1]

def main():
    passed=[]
    with tempfile.TemporaryDirectory(prefix='dogido-group-warnings-') as temp, dependencies() as (dep,control,seen):
        folder=Path(temp); cues=folder/'cues'
        # 実音声を読まないplayerで順序と取消を確認する。
        for name in ['mob/zombie.mp3','mob/skeleton.mp3','common/counts/1.mp3','common/counts/2.mp3','common/counts/3.mp3','common/phrases/orude.mp3','panic/freesound_community-male-gasp-1-7183.mp3']:
            p=cues/name; p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(b'fixture')
        player=folder/'player'
        player.write_text(f'''#!{sys.executable}
from pathlib import Path
import sys,time
root=Path(__file__).parent
with (root/'played').open('a') as f:f.write(sys.argv[1]+'\\n')
p=root/'slow'
time.sleep(2 if p.exists() else .08)
'''); player.chmod(0o700)
        with running(ROOT/'target/debug/dogido-rust',folder,dep,player=player,warning_settings={'cue_dir':str(cues)}) as (base,process,log):
            seq=0
            def send(sid,types,reverse=False,direction='left',fuse=False,distance=8.2,directions=None):
                nonlocal seq
                seq+=1; e=event(0); e['sequence']=seq; e['observed_at']=datetime.now(timezone.utc).isoformat()
                e['visual_threats']=[event(0,kind=t,entity=f'{t}{i}',distance=distance,direction=directions[i] if directions else direction,fuse=fuse if t=='creeper' else None)['visual_threats'][0] for i,t in enumerate(types)]
                if reverse:e['visual_threats'].reverse()
                request(base,'/api/v1/game-events',e,sid=sid)
            def warnings(sid):return [r for r in snapshot(base)['utterances'] if r['session_id']==sid and r['category']=='callout']
            def fresh():return register(base,preview=False)
            def close(sid):request(base,'/api/v1/adapter-sessions/'+sid,method='DELETE')
            def played():return (folder/'played').read_text().splitlines() if (folder/'played').exists() else []

            sid=fresh(); before=len(played()); calls=len(seen)
            send(sid,['zombie','zombie','skeleton'])
            w=wait_for(lambda:warnings(sid))[-1]; wt=w['turn_id']
            assert w['warning']['text']=='ゾンビ2体、スケルトン1体おるで。',w
            wait_for(lambda:row(base,wt,{'started'}))
            for _ in range(4):send(sid,['zombie','zombie','skeleton'],reverse=True,direction='right',distance=18)
            wait_for(lambda:row(base,wt,{'completed'}))
            assert len(warnings(sid))==1 and len(seen)==calls
            assert [str(Path(p).relative_to(cues)) for p in played()[before:]]==[
                'panic/freesound_community-male-gasp-1-7183.mp3','mob/zombie.mp3','common/counts/2.mp3',
                'mob/skeleton.mp3','common/counts/1.mp3','common/phrases/orude.mp3']
            passed.append('group_fragments_order_no_tts_llm_reorder_or_direction_restart');close(sid)

            # 一片不足なら前半断片を鳴らさず、本文すべてをTTSに戻す。
            missing=cues/'common/counts/3.mp3'; missing.unlink()
            sid=fresh(); before=len(played()); calls=len(seen); send(sid,['zombie']*3)
            wt=wait_for(lambda:warnings(sid))[-1]['turn_id']; wait_for(lambda:row(base,wt,{'completed'}))
            assert not any('/mob/' in p or '/common/' in p for p in played()[before:])
            assert any(r['path'].startswith('/synthesis') and r['body'].get('test_text')=='ゾンビ3体おるで。' for r in seen[calls:])
            passed.append('missing_fragment_uses_whole_body_tts');close(sid);missing.write_bytes(b'fixture')

            # 再生途中の3体→2体は更新する。古い3体clipが後ろから出てこない。
            sid=fresh(); (folder/'slow').touch();before=len(played()); send(sid,['zombie']*3)
            wt=wait_for(lambda:warnings(sid))[-1]['turn_id'];wait_for(lambda:row(base,wt,{'started'}))
            (folder/'slow').unlink();send(sid,['zombie']*2)
            assert wait_for(lambda:row(base,wt,{'cancelled'}))['cancel_reason']=='target_changed_or_gone'
            new=wait_for(lambda:next((r for r in warnings(sid) if r['turn_id']!=wt),None))
            wait_for(lambda:row(base,new['turn_id'],{'completed'}));assert new['warning']['text']=='ゾンビ2体おるで。'
            assert not any(p.endswith('/counts/3.mp3') for p in played()[before:])
            passed.append('decrease_replaces_stale_count_without_old_fragments');close(sid)

            sid=fresh(); send(sid,['zombie']);wt=wait_for(lambda:warnings(sid))[-1]['turn_id']
            wait_for(lambda:row(base,wt,{'completed'})); send(sid,['zombie']*2)
            new=wait_for(lambda:next((r for r in warnings(sid) if r['turn_id']!=wt),None))
            wait_for(lambda:row(base,new['turn_id'],{'completed'}));assert new['warning']['text']=='ゾンビが増えたで！'
            for _ in range(3):send(sid,['zombie']*2)
            assert len(warnings(sid))==2
            passed.append('single_to_multi_bypasses_global_cooldown_once');close(sid)

            sid=fresh();(folder/'slow').touch(); send(sid,['zombie']*2+['creeper'])
            wt=wait_for(lambda:warnings(sid))[-1]['turn_id'];wait_for(lambda:row(base,wt,{'started'}))
            (folder/'slow').unlink();send(sid,['zombie']*2+['creeper'],fuse=True)
            assert wait_for(lambda:row(base,wt,{'cancelled'}))['cancel_reason']=='higher_priority_warning'
            new=wait_for(lambda:next((r for r in warnings(sid) if r['turn_id']!=wt),None))
            wait_for(lambda:row(base,new['turn_id'],{'completed'}));assert new['warning']['kind']=='creeper_fuse'
            passed.append('fuse_preempts_group_with_same_members');close(sid)

            sid=fresh();(folder/'slow').touch();send(sid,['zombie']*2)
            wt=wait_for(lambda:warnings(sid))[-1]['turn_id'];wait_for(lambda:row(base,wt,{'started'}))
            send(sid,[]);wait_for(lambda:row(base,wt,{'cancelled'}));time.sleep(.15)
            assert len(warnings(sid))==1
            passed.append('empty_observation_cancels_whole_group')
            (folder/'slow').unlink(); send(sid,['zombie']*2)
            new=wait_for(lambda:next((r for r in warnings(sid) if r['turn_id']!=wt),None))
            wait_for(lambda:row(base,new['turn_id'],{'completed'}))
            assert new['warning']['kind']=='hostile_count'
            passed.append('reappearance_after_empty_observation_starts_new_group');close(sid)

            sid=fresh(); (folder/'slow').touch()
            types=['zombie','zombie','skeleton','skeleton']; directions=['front','front','front','left']
            send(sid,types,directions=directions)
            wt=wait_for(lambda:warnings(sid))[-1]['turn_id'];wait_for(lambda:row(base,wt,{'started'}))
            (folder/'slow').unlink()
            for _ in range(3):send(sid,types,directions=directions,reverse=True)
            wait_for(lambda:row(base,wt,{'completed'}))
            assert len(warnings(sid))==1
            passed.append('overwhelmed_support_tie_reordering_keeps_audio');close(sid)
            assert not any(r['path']=='/v1/chat/completions' for r in seen)
        assert 'warning_fragments_fallback' in log.read_text()
    report=ROOT/'reports/group-warning-check.json';report.parent.mkdir(exist_ok=True)
    report.write_text(json.dumps({'passed':passed,'models':'mock','tts':'mock','playback':'mock','all_owned_processes_stopped':True},ensure_ascii=False,indent=2)+'\n')
    print(f'PASS {len(passed)} group warning checks; all owned processes stopped')

if __name__=='__main__':main()
