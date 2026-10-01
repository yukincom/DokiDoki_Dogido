#!/usr/bin/env python3
"""Compare native player_chat validation against exact Python source, without services."""
import argparse
import json
import logging
from pathlib import Path
import sys
from chat_validation_helper import leaf_input


def main():
    p=argparse.ArgumentParser();p.add_argument('--source-root',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,default=Path(__file__).resolve().parents[1]/'src/chat_validation')
    args=p.parse_args();sys.path.insert(0,str(args.source_root))
    from dogido_server.config import Settings
    from dogido_server.llm.client import DogidoLLM
    from dogido_server.llm.types import LeafGenerationRequest
    from dogido_server.llm.prompts import build_messages
    from dogido_server.llm.sanitize import (clean_output,usability_rejection_reason,is_style_acceptable,
        player_chat_style_rejection_reason,contains_forbidden_mob_advice,contains_unsupported_olfactory_claim)
    from dogido_server.dialogue.chat_policy import catalog_speech_labels, rewrite_observed_speech_names, catalog_labels_mentioned_in_text
    settings=Settings(_env_file=None,llm_enabled=True,llm_backend='chat_completions',llm_provider='local',
        llm_model='fixture',llm_max_tokens=72,audio_enabled=False,memory_enabled=False)
    llm=DogidoLLM(settings)
    raw=['','うん','せやな、ありがとう。','ドギド: せやな、ありがとう。','ごめん、俺が取り違えたわ。',
        '「ほっとしたわ。」','「前はこうや」ってことやな。','自分のことをドギドと呼んでるんや。',
        'これは例や、本番も同じやで。','Final answer: ほっとしたわ。','assistant: ほっとしたわ。',
        '<think>試案\n検討</think>\nセリフ：ほっとしたわ。<|im_end|>',
        'Here is a thinking process\n日本語の説明です\n最後は安心やな。','English explanation',
        'Player_1、OK、ええ感じやな。','ああああ、何やねん。','せやからせやからせやからな。',
        'やわ！！やん！！やろ。','んかやんか、怖いわ。',
        '村人ゾンビとゾンビが近くにおるで。','エルダーガーディアンとガーディアンやで。',
        'ピリジャーと前哨基地やで。','川に落ちたゾンビはダメなんか。','村の人とハエ、猫を見たで。',
        'ゾンビはそこでじっとしとるで。','その場で待ってな。','溶岩に飛び込んだらどうや。',
        'もうほっとけ。','話しかけんといて。','向こうへ行ってや。','そうなんや、黙れ！',
        'うるさい音やな。','うるさいわ。','ほっとけという台詞なんや。',
        'もう少し先に行こうや。','冒険しようや。','探索はやめて、家に帰ろうや。',
        '遠出を控えて、先へ行かんとこう。','いまの遠出は不安やな。','先へ進もうや。',
        '土の匂いがするわ。','匂いがするなら分かるわ。','匂いがしたなら分かるわ。',
        '匂いがしてたら教えてな。','匂いがしてれば分かるわ。','匂いが漂うわ。',
        '何かくさいな。','なんかものすごくくさいで。','匂うなら大変やな。',
        '臭ってたら知らせてな。','臭ったら気がつくやろ。','臭ったんやで。','香るなら教えてな。',
        '草が香るんやな。','匂いがしたん？','何か香りが漂うの？','くさいか？','生臭いと思う？',
        '匂いがするん？ ほな手を洗おうか。','この句は土の匂いがするわ。','ゾンビは臭そうやな。',
        'くさっ、すごいで。','鼻につく表現やな。','匂いはするなら、風はどうや。',
        '匂いがするなら大丈夫、でも草の匂いがしてるで。',
        'AAAA\nRole: helper','例2：そうなんやな。','モンスター！？','１２３４','²なら助かるわ。']
    raw += ['Player_1','OK NG','本番：大丈夫やで。','サクラ','あアー！']
    raw += [label+'と'+label+'の話やな。' for label in catalog_speech_labels()]
    contexts=[{}, {'player_name':'Player_1'}, {'mode':'panic'}, {'mode':'alert'}, {'character_mode':'battle'},
        {'combat_active':True},{'has_visual_threats':True},{'nearby_hostile_types':['creeper']},
        {'nearby_mob_ids':['enderman']},{'event_digest':'敵の視認中'}, {'forbidden_advice':['家']},
        {'speech_whitelist_enforce':True,'allowed_speech_labels':[]},
        {'speech_whitelist_enforce':True,'allowed_speech_labels':['村人ゾンビ','前哨基地']},
        {'speech_whitelist_enforce':True,'allowed_speech_labels':['村人ゾンビ'],'speech_name_corrections':{'ゾンビ':'村人ゾンビ'}},
        {'speech_name_corrections':{'ガーディアン':'エルダーガーディアン'}},
        {'speech_name_corrections':{'村人ゾンビ':'ゾンビ','ゾンビ':'村人ゾンビ'}},
        {'user_text':'ゾンビはどんな匂いがするの？'}, {'user_text':'この句はどんな匂いを感じる？'},
        {'user_text':'土の香りがするならどうする？'}, {'user_text':'こんにちは'},
        {'player_turn_plan':'return_home'}, {'safety_priority':'seek_safe_place'},
        {'user_text':None,'speech_name_corrections':None,'allowed_speech_labels':None},
        {'speech_name_corrections':{' unknown ':'知らん名前',' ゾンビ ':' 村人ゾンビ ',' ':'空'}}]
    logging.disable(logging.CRITICAL)
    candidates=[]
    for details in contexts:
        req=LeafGenerationRequest(kind='player_chat',fallback_text='そやな、話を聞いとるで。',details=details,temperature=.65)
        for text in raw:
            clean=clean_output(text); corrected,applied=rewrite_observed_speech_names(clean,details.get('speech_name_corrections'))
            c,reason,issue=llm._validate_leaf_candidate(req,text)
            candidates.append({'raw':text,'details':details,'cleaned':c,'reason':reason,'issue':issue,
                'corrections':applied if usability_rejection_reason(clean,details) is None else [],
                'mentioned':catalog_labels_mentioned_in_text(clean),
                'corrected':corrected,'style':is_style_acceptable('player_chat',corrected,details),
                'style_reason':player_chat_style_rejection_reason(corrected,details)})
    class Probe(DogidoLLM):
        def __init__(self,outputs):super().__init__(settings);self.outputs=iter(outputs);self.calls=[]
        def _generate_backend_text(self,request):
            self.calls.append({'messages':build_messages(request),'temperature':request.temperature,
                'max_tokens':request.max_tokens or self.settings.llm_max_tokens})
            value=next(self.outputs)
            if value is None:raise RuntimeError('fixture backend failure')
            return value
    sequences=[['せやな、ありがとう。'],[None],['English explanation','せやな、ありがとう。'],
        ['English explanation','English twice'],['English explanation',None],['','せやな、ありがとう。'],
        ['ゾンビがおるで。','見えてないんやな。'],['土の匂いがするわ。','その話は聞いとるで。'],
        ['溶岩に飛び込んだらええで。','えらいこっちゃな。'],['村人ゾンビとゾンビやで。'],
        ['やわ、やん、やろ。','せやな、ありがとう。'],['a'*1000,'せやな、ありがとう。'],['あ'*700,'せやな、ありがとう。']]
    turns=[]
    for d in contexts:
        for outputs in sequences:
            probe=Probe(outputs)
            req=LeafGenerationRequest(kind='player_chat',fallback_text='そやな、話を聞いとるで。',details=d,temperature=.65)
            selected=probe.generate_leaf_text(req)
            # Preserve narration's final pass exactly; this is a second boundary, not a retry.
            corrected,_=rewrite_observed_speech_names(selected,d.get('speech_name_corrections'))
            final=corrected if is_style_acceptable('player_chat',corrected,d) else req.fallback_text
            turns.append({'input':leaf_input(req,'fixture',72),'outputs':outputs,'selected':selected,'final':final,'calls':probe.calls})
    pool=[];seen={}
    def intern(v):
        k=json.dumps(v,ensure_ascii=False,sort_keys=True)
        if k not in seen:seen[k]=len(pool);pool.append(v)
        return seen[k]
    packed={'pool':pool,'candidates':[{k:intern(v) for k,v in r.items()} for r in candidates],
        'turns':[{k:intern(v) for k,v in r.items()} for r in turns]}
    args.output_dir.mkdir(exist_ok=True,parents=True)
    for name,value in [('labels.json',catalog_speech_labels()),('fixtures.json',packed)]:
        (args.output_dir/name).write_text(json.dumps(value,ensure_ascii=False,separators=(',',':'))+'\n')
    print(f'candidate comparisons={len(candidates)} turns={len(turns)} labels={len(catalog_speech_labels())}')
if __name__=='__main__':main()
