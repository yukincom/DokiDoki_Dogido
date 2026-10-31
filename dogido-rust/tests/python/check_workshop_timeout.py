#!/usr/bin/env python3
"""相談の生成・音声終了後から無操作時間を数え、移動・道具使用では延長しない。"""
import json
import threading
import time
from check_dialogue import register, submit_text, display_row, acknowledge, row, wait_for
from check_haiku_runtime import fixture
from check_workshop_runtime import install, step


def main():
    with fixture(workshop_idle_ms=1800) as (base, _, _, control, _, _, _, _, send, hud, rows, stored, folder):
        sid=register(base, preview=False, voice_enabled=True);send(sid)
        wait_for(lambda:any(r["turn_id"].endswith(":poem") and r["playback_status"]=="completed" for r in rows(sid)))
        entered=threading.Event();release=threading.Event()
        def slow_reply(text,prompt,n):
            entered.set();release.wait(timeout=8)
            return step(text,speech="この句の景色を一緒に考えよか。")
        install(control,slow_reply)
        (folder/'player_mode').write_text('slow')
        turn=submit_text(base,sid,'この句の意味を考えたい')
        try:
            wait_for(entered.is_set)
            time.sleep(2.1)
            assert hud(sid)['state']=='open','expired while generating'
            release.set()
            shown=wait_for(lambda:display_row(base,turn));acknowledge(base,sid,shown)
            wait_for(lambda:row(base,turn,{'started'}))
            time.sleep(2.1)
            assert hud(sid)['state']=='open','expired during audio after early text ACK'
            wait_for(lambda:row(base,turn,{'completed'}))
            (folder/'player_mode').write_text('ok')
            ended=time.monotonic()
            while time.monotonic()-ended<1.0:
                send(sid,player={'name':'試験','dimension':'minecraft:overworld',
                    'held_item':'minecraft:netherite_axe','position':{'x':time.monotonic()-ended,'y':64,'z':0},
                    'block_breaking_active':True})
                assert hud(sid)['state']=='open','wait did not start after audio completion'
                time.sleep(.15)
            while hud(sid)['state']=='open' and time.monotonic()-ended<3.2:
                send(sid,player={'name':'試験','dimension':'minecraft:overworld','held_item':'minecraft:cherry_leaves'})
                time.sleep(.15)
            assert hud(sid)['state']=='closed','game observations extended discussion time'
            assert len(stored(sid))==1
            print(json.dumps({'passed':True,'generation_pause':True,'audio_after_text_ack_pause':True,
                'idle_after_reply_ms':1800,'movement_does_not_extend':True,'saved_verse_retained':True}))
        finally:release.set()

if __name__=='__main__':main()
