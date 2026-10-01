#!/usr/bin/env python3
"""実HTTPで警告の割込み・鮮度・後始末を確認。モデル/TTS/playerは模擬。"""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
import re
import sys
import tempfile
import threading
import time
from check_dialogue import dependencies, running, register, request, snapshot, wait_for, row, submit
from compare_threats import event

ROOT=Path(__file__).resolve().parents[1]

def main():
    passed=[]
    with tempfile.TemporaryDirectory(prefix='dogido-warnings-') as temp, dependencies() as (dep,control,seen):
        folder=Path(temp)
        player=folder/'player'
        player.write_text(f'#!{sys.executable}\nimport time\nfrom pathlib import Path\np=Path(__file__).with_name("mode")\ntime.sleep(3 if p.exists() and p.read_text()=="slow" else .35)\n')
        player.chmod(0o700)
        with running(ROOT/'target/debug/dogido-rust',folder,dep,player=player) as (base,process,log):
            seq=0
            def send(sid, *, gone=False, stale=False, **kw):
                nonlocal seq
                seq+=1; e=event(0,**kw); e['sequence']=seq
                e['observed_at']=(datetime.now(timezone.utc)-timedelta(seconds=30 if stale else 0)).isoformat()
                if gone:e['visual_threats']=[]
                return request(base,'/api/v1/game-events',e,sid=sid)
            def warnings(sid):return [r for r in snapshot(base)['utterances'] if r['session_id']==sid and r['category']=='callout']
            def fresh():return register(base,preview=False)
            def close(sid):request(base,'/api/v1/adapter-sessions/'+sid,method='DELETE')
            def llm_count():return sum(r['path']=='/v1/chat/completions' for r in seen)

            sid=fresh(); send(sid,gone=True); (folder/'mode').write_text('slow')
            chat=submit(base,sid); wait_for(lambda:row(base,chat,{'started'})); calls=llm_count()
            (folder/'mode').write_text('ok'); send(sid)
            warning=wait_for(lambda:warnings(sid))[-1]; wt=warning['turn_id']
            wait_for(lambda:row(base,wt,{'started'}))
            for _ in range(5):send(sid)
            wait_for(lambda:row(base,wt,{'completed'}))
            assert row(base,chat,{'cancelled'})['cancel_reason']=='visual_hostile'
            assert len(warnings(sid))==1 and llm_count()==calls
            assert all(r['role']!='assistant' for r in snapshot(base)['sessions'][0]['history'])
            passed.append('chat_cancel_then_warning_repeat_ticks_no_llm_no_history'); close(sid)

            sid=fresh(); (folder/'mode').write_text('slow'); send(sid)
            wt=wait_for(lambda:warnings(sid))[-1]['turn_id']; wait_for(lambda:row(base,wt,{'started'}))
            (folder/'mode').write_text('ok'); send(sid,gone=True)
            wait_for(lambda:row(base,wt,{'completed'}))
            passed.append('started_warning_finishes_after_target_disappears'); close(sid)

            # 未再生なら方向を更新。第一声が始まった導火警告は最後まで配送。
            for kind,fuse,held in [('zombie',None,'前にゾンビおるで。'),('creeper',True,' クリーパー膨らんどる、爆発するでぇ！')]:
                sid=fresh(); gate=threading.Event(); control['tts_gates'][held]=gate
                # entity z1の候補0で固定。fuse本文では第一文「前！」の後の合成を保留。
                before=len(seen); send(sid,kind=kind,entity='z1',direction='front',fuse=fuse)
                old=wait_for(lambda:warnings(sid))[-1]['turn_id']
                try:
                    wait_for(lambda:any(r['path'].startswith('/synthesis') and r['body'].get('test_text')==held for r in seen[before:]))
                except AssertionError:
                    print(kind, json.dumps(seen[before:],ensure_ascii=False)); print(log.read_text()[-6000:]); raise
                if fuse: wait_for(lambda:row(base,old,{'started'}))
                send(sid,kind=kind,entity='z1',direction='right',fuse=fuse)
                gate.set()
                if fuse:
                    done=wait_for(lambda:row(base,old,{'completed'})); assert '前' in done['text'],done
                    assert len(warnings(sid))==1
                    passed.append('started_fuse_warning_finishes_across_direction_change')
                else:
                    assert wait_for(lambda:row(base,old,{'cancelled'})).get('started_at') is None
                    new=wait_for(lambda:next((r for r in warnings(sid) if r['turn_id']!=old),None))
                    done=wait_for(lambda:row(base,new['turn_id'],{'completed'})); assert '右' in done['text'],done
                    assert len(warnings(sid))==2
                    passed.append('unstarted_warning_direction_updated_during_synthesis')
                assert llm_count()==calls
                close(sid)

            sid=fresh(); (folder/'mode').write_text('slow'); send(sid,direction='front')
            old=wait_for(lambda:warnings(sid))[-1]['turn_id']; wait_for(lambda:row(base,old,{'started'}))
            (folder/'mode').write_text('ok'); send(sid,direction='right')
            done=wait_for(lambda:row(base,old,{'completed'})); assert '前' in done['text']
            assert len(warnings(sid))==1
            passed.append('started_normal_warning_finishes_across_direction_change'); close(sid)

            # 未再生の警告は最新方向へ差し替える。別sessionの再生でserialを塞ぐ。
            blocker=fresh(); send(blocker,gone=True); (folder/'mode').write_text('slow')
            blocked=submit(base,blocker); wait_for(lambda:row(base,blocked,{'started'}))
            sid=fresh(); send(sid,entity='queued-z',direction='front')
            old=wait_for(lambda:warnings(sid))[-1]['turn_id']
            assert row(base,old,{'queued'})
            send(sid,entity='queued-z',direction='right')
            assert wait_for(lambda:row(base,old,{'cancelled'})).get('started_at') is None
            (folder/'mode').write_text('ok'); close(blocker)
            new=wait_for(lambda:next((r for r in warnings(sid) if r['turn_id']!=old),None))
            done=wait_for(lambda:row(base,new['turn_id'],{'completed'})); assert '右' in done['text'],done
            calls=llm_count()
            passed.append('unstarted_warning_uses_latest_direction'); close(sid)

            sid=fresh(); (folder/'mode').write_text('slow'); send(sid,distance=4)
            old=wait_for(lambda:warnings(sid))[-1]['turn_id']; wait_for(lambda:row(base,old,{'started'}))
            (folder/'mode').write_text('ok'); send(sid,kind='creeper',entity='c1',distance=4,fuse=True)
            new=wait_for(lambda:next((r for r in warnings(sid) if r['turn_id']!=old),None))
            assert wait_for(lambda:row(base,old,{'cancelled'}))['cancel_reason']=='higher_priority_warning'
            for _ in range(5):send(sid,kind='creeper',entity='c1',distance=4,fuse=True)
            wait_for(lambda:row(base,new['turn_id'],{'completed'})); assert len(warnings(sid))==2
            send(sid,kind='creeper',entity='c1',distance=4,fuse=False)
            send(sid,kind='creeper',entity='c1',distance=4,fuse=True)
            third=wait_for(lambda:warnings(sid) if len(warnings(sid))==3 else None)[-1]
            wait_for(lambda:row(base,third['turn_id'],{'completed'}))
            passed.append('fuse_interrupt_edge_rearm'); close(sid)

            # stale観測で新警告を出さず、実再生中にも時計の失効を適用。
            sid=fresh(); send(sid,stale=True); time.sleep(.1); assert not warnings(sid)
            held='左にゾンビおるで。'; gate=threading.Event(); control['tts_gates'][held]=gate
            # キャッシュを避ける個体候補1。
            held='ひっ、左にゾンビおる。'; control['tts_gates'][held]=gate
            send(sid,entity='z2'); wt=wait_for(lambda:warnings(sid))[-1]['turn_id']
            stopped=wait_for(lambda:row(base,wt,{'cancelled'}),timeout=12)
            assert stopped['cancel_reason']=='stale_observation'; gate.set()
            passed.append('stale_input_and_observation_timeout'); close(sid)

            sid=fresh(); (folder/'mode').write_text('slow'); send(sid)
            wt=wait_for(lambda:warnings(sid))[-1]['turn_id']; wait_for(lambda:row(base,wt,{'started'}))
            request(base,'/api/v1/rust-dialogue/interrupt',{'session_id':sid})
            assert wait_for(lambda:row(base,wt,{'cancelled'}))['cancel_reason']=='manual_interrupt'
            passed.append('manual_warning_stop'); close(sid)

            sid=fresh(); send(sid); wt=wait_for(lambda:warnings(sid))[-1]['turn_id']; wait_for(lambda:row(base,wt,{'started'}))
            close(sid); assert wait_for(lambda:row(base,wt,{'cancelled'}))['cancel_reason']=='session_closed'
            passed.append('session_close_reaps_warning')

            sid=fresh(); send(sid); wt=wait_for(lambda:warnings(sid))[-1]['turn_id']; wait_for(lambda:row(base,wt,{'started'}))
        assert re.search(r'event="warning_status".*turn_id="'+wt+r'" playback_status="cancelled"',log.read_text()),log.read_text()
        passed.append('shutdown_joins_warning_and_player')
    path=ROOT/'reports/warning-check.json'; path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({'passed':passed,'llm':'mock','tts':'mock','playback':'mock','all_owned_processes_stopped':True},ensure_ascii=False,indent=2)+'\n')
    print(f'PASS {len(passed)} warning lifecycle checks; all owned processes stopped')

if __name__=='__main__':main()
