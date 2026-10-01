"""Compare Rust token policy to canonical apply_unidic_reading using fake tokens."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import itertools
import json
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from dogido_server import tts_reading as canonical
OUT=ROOT/'dogido-rust/src/tts_reading'
(OUT/'preferred.json').write_text(json.dumps(canonical._PREFERRED_SURFACE_READINGS,ensure_ascii=False,indent=2)+'\n')
surfaces=list(canonical._PREFERRED_SURFACE_READINGS)+['猫','松明','関西','かな','カナ','ｶﾅ','𠮷','㍻','A','']
readings=[(None,None),('',None),(None,'ネコ'),('ネコ','ネーコ'),(' \x1c ','ネコ'),('\x1cアサ\x1f',None),('ヴァヷヵヶーｱ',None)]
cases=[]
for surface,goshu,pos,(kana,pron) in itertools.product(surfaces,[None,'','和','混','漢','外'],[None,'','名詞','助詞','助動詞','補助記号','記号','空白'],readings):
 row=dict(surface=surface,goshu=goshu,pos1=pos,kana=kana,pron=pron)
 token=SimpleNamespace(surface=surface,feature=SimpleNamespace(**{k:v for k,v in row.items() if k!='surface'}))
 with patch.object(canonical,'_get_unidic_tagger',return_value=lambda _:[token]):
  output=canonical.apply_unidic_reading('元の全文')
 cases.append({'tokens':[row],'expected':output})
for rows in [[],[{'surface':'猫','goshu':'和','pos1':'名詞','kana':'ネコ','pron':None},{'surface':'が','goshu':'和','pos1':'助詞','kana':'ガ','pron':None},{'surface':'来た','goshu':'和','pos1':'動詞','kana':'キタ','pron':None}]]:
 tokens=[SimpleNamespace(surface=r['surface'],feature=SimpleNamespace(**{k:v for k,v in r.items() if k!='surface'})) for r in rows]
 with patch.object(canonical,'_get_unidic_tagger',return_value=lambda _:tokens):output=canonical.apply_unidic_reading('元の全文')
 cases.append({'tokens':rows,'expected':output})
(OUT/'token-fixtures.json').write_text(json.dumps(cases,ensure_ascii=False,separators=(',',':'))+'\n')
print(f'{len(cases)} token policy cases; dictionary is mocked')
