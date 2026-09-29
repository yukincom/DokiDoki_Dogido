#!/usr/bin/env python3
"""句の想起条件（場所・壁時計）の純粋比較。記憶ファイルを読まない。"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import unicodedata
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'dogido-rust/scripts'))
from dogido_server import entry_catalog as e
from dogido_server.player_input import guardrails as g
from reading_overlay import apply_reading_snapshot
from memory_query import recall_query


def policy():
    return {'entries':[{'id':id, 'label':v.get('label',id), 'reading':v.get('reading',''), 'group':v.get('group_id','')}
                       for id,v in e.biome_entries().items()],
            'groups':e.BIOME_PLACE_GROUP_HINTS, 'recall_keywords':g.HAIKU_RECALL_KEYWORDS,
            'digits':{chr(i):unicodedata.decimal(chr(i)) for i in range(0x110000) if unicodedata.category(chr(i))=='Nd'}}


def cases():
    cases=[]
    def add(text,now='2026-09-29T15:16:17.123456+09:00',overlay=None):
        overlay=overlay or [];apply_reading_snapshot(overlay)
        result=recall_query(text,now)
        if result:
            for key in ['since','until']:
                if result[key]: result[key]=datetime.fromisoformat(result[key]).astimezone(timezone.utc).isoformat().replace('+00:00','Z')
        cases.append({'text':text,'now':now,'offset_seconds':int(datetime.fromisoformat(now).astimezone().utcoffset().total_seconds()),
                      'overlay':overlay,'expected':result})
    for row in policy()['entries']:
        for name in [row['label'],row['reading'],row['id'],row['id'].replace('_','')]:
            if name: add(f'{name}の句を思い出して')
    for phrases,_,_ in e.BIOME_PLACE_GROUP_HINTS:
        for phrase in phrases: add(f'{phrase}の川柳')
    for text in ['こんにちは','この句はどういう意味？','句思い出して','今日の句','昨日の句','今月の句','ここひと月の句','ここ一カ月の句',
                 '先週の句を思い出して','7月の句','１２月の句','2月29日の句','13月の句','0月の句','9月31日の句','10月1日の句',
                 '2026年9月1日の句','１月２日の句','١月٢日の句','\x1c雪のタイガの句\x1f','草地と森の句','暖かい所の句','どこで詠んだ']:
        add(text)
    for now in ['2024-03-01T01:02:03+09:00','2025-01-01T00:00:00+09:00','2026-12-31T23:59:59.999999+09:00']:
        for text in ['昨日の句','今月の句','ここひと月の句','12月の句','1月1日の句']:add(text,now)
    add('すばらしそうちの句',overlay=[{'surface':'草地','reading':'すばらしそうち'}])
    add('くさちの句',overlay=[{'surface':'草地','reading':'すばらしそうち'}])
    apply_reading_snapshot([])
    return cases


def main():
    p=argparse.ArgumentParser();p.add_argument('--check',action='store_true');args=p.parse_args()
    for path,v in [(ROOT/'dogido-rust/src/recall-query-policy.json',policy()),(ROOT/'dogido-rust/fixtures/recall-query.json',cases())]:
        raw=json.dumps(v,ensure_ascii=False,indent=2)+'\n'
        if args.check: assert path.read_text()==raw, path.name
        else:path.write_text(raw)
        print(path.name,len(v))
if __name__=='__main__':main()
