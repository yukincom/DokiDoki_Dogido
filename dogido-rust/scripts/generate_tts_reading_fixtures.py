#!/usr/bin/env python3
"""Pure comparison; substitutes fixed UniDic responses and never loads the dictionary."""
import argparse
import itertools
import json
from pathlib import Path
import sys
from unittest.mock import patch

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--source-root',type=Path,required=True);ap.add_argument('--output-dir',type=Path,required=True);args=ap.parse_args()
    sys.path.insert(0,str(args.source_root))
    from dogido_server import tts_reading as p
    strings=['',' ','\x1c\x1d\x1e\x1f','朝から元気でええねん。','朝鮮半島の朝。','今日は一日、草地におる。',
        '「大人の上手な人参」','今朝と昨日、昨夜と明日。','アサとｱｻ、㍻、𠮷、かな。','\u3000朝\n草地\u3000',
        '\u0085朝\u2028\u2029','朝を朝まで朝が朝に朝は朝の朝だ朝や','中\u200b朝\u200b',
        '朝\x1f草地','漢字なしのかな。','\u4dff','\u4e00','\u9fff','\ua000']
    strings.extend(surface for surface,_ in p._TTS_HIRAGANA_REPLACEMENTS)
    cases=[]
    for text,engine,environment,output in itertools.product(strings,(None,'off','auto','unidic',' OFF ','\x1cUNIDIC\x1f','','nope'),(None,'off','unidic','bad'),(None,'','あさの空','  今朝\n')):
        env={} if environment is None else {'DOGIDO_TTS_READING_ENGINE':environment}
        calls=[]
        def synthetic(source):
            calls.append(source)
            return source if output is None else output
        with patch.dict(p.os.environ,env,clear=True),patch.object(p,'apply_unidic_reading',synthetic):
            resolved=p.resolve_tts_reading_engine(engine)
            expected=p.prepare_text_for_tts(text,engine=engine)
        cases.append({'text':text,'engine':engine,'environment':environment,'resolved':resolved,'unidic_output':output,
            'manual':p.apply_manual_tts_replacements(text),'source':text.strip(),'expected':expected,'calls':len(calls)})
    args.output_dir.mkdir(parents=True,exist_ok=True)
    for name,value in [('replacements.json',p._TTS_HIRAGANA_REPLACEMENTS),('fixtures.json',cases)]:
        (args.output_dir/name).write_text(json.dumps(value,ensure_ascii=False,separators=(',',':'))+'\n')
    print(f'{len(cases)} pure TTS cases; no UniDic or voice invoked')
if __name__=='__main__':main()
