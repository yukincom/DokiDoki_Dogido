"""Raw-result/token handshake only. No SDK, HTTP model, or audio calls."""
import io
import json
import sys
from types import SimpleNamespace
import pytest
import dialogue_helper as helper
import language_helper
from dogido_server import tts_reading as canonical
from test_input_helper import frame


def fail(*_args, **_kw):
    raise AssertionError('unexpected dictionary, model, or legacy formatter')


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch):
    monkeypatch.setattr(canonical, 'prepare_text_for_tts', fail)
    monkeypatch.setattr(canonical, '_get_unidic_tagger', fail)
    monkeypatch.setattr(helper, 'BridgeLLM', fail)
    monkeypatch.setattr(helper, 'DogidoStateMachine', fail)


def test_raw_result_no_request_is_eof_without_dictionary(monkeypatch):
    emitted=[]
    monkeypatch.setattr(helper, 'emit', emitted.append)
    monkeypatch.setattr(helper.sys, 'stdin', io.StringIO(''))
    for text in ('', 'かな', ' 朝鮮の朝 ', '今朝は元気や。'):
        original={'op':'result','text':text,'repair':{'target_turn_id':'raw'}}
        helper.emit_result(original)
        assert emitted[-1] == original
        assert 'spoken_text' not in original


def test_one_matching_token_request_is_projected_without_reading_policy(monkeypatch):
    emitted=[]
    request={'op':'tts_tokens','schema_version':1,'request_id':'r','text':'朝鮮の猫'}
    source=io.StringIO(json.dumps(request)+'\n'+json.dumps(request)+'\n')
    monkeypatch.setattr(helper.sys, 'stdin', source)
    monkeypatch.setattr(helper, 'emit', emitted.append)
    word=SimpleNamespace(surface='朝鮮',feature=SimpleNamespace(goshu='漢',pos1='名詞',kana='チョウセン',pron='チョーセン'))
    monkeypatch.setattr(canonical, '_get_unidic_tagger', lambda:lambda _:[word])
    helper.emit_result({'op':'result','text':'\x1c 朝鮮の猫 \n','repair':None})
    assert emitted==[{'op':'result','text':'\x1c 朝鮮の猫 \n','repair':None},
        {'schema_version':1,'request_id':'r','status':'ok','tokens':[{'surface':'朝鮮','goshu':'漢','pos1':'名詞','kana':'チョウセン','pron':'チョーセン'}]}]
    assert source.readline()  # Exactly one optional request per final result.


@pytest.mark.parametrize('token_request', [
    {}, {'op':'tts_tokens','text':'other'}, {'op':'reading','text':'猫'},
    {'op':'tts_tokens','schema_version':True,'request_id':'r','text':'猫'},
    {'op':'tts_tokens','schema_version':1,'request_id':'r','text':'猫','overlay':[]},
])
def test_bad_token_request_does_not_get_dictionary(monkeypatch, token_request):
    monkeypatch.setattr(helper,'emit',lambda _:None)
    monkeypatch.setattr(helper.sys,'stdin',io.StringIO(json.dumps(token_request)+'\n'))
    with pytest.raises(ValueError): helper.emit_result({'op':'result','text':'猫'})


@pytest.mark.parametrize('wire', ['{','{}', 'x'*1_000_001+'\n', json.dumps({'op':'tts_tokens','text':'猫'})])
def test_partial_or_oversized_request_fails(monkeypatch, wire):
    monkeypatch.setattr(helper,'emit',lambda _:None)
    monkeypatch.setattr(helper.sys,'stdin',io.StringIO(wire))
    with pytest.raises(ValueError): helper.emit_result({'op':'result','text':'猫'})


@pytest.mark.parametrize('value', [None, {}, {'op':'result','text':None}, {'op':'result','text':'猫','spoken_text':'ねこ'}])
def test_already_formatted_or_nontext_result_is_not_emitted(monkeypatch,value):
    monkeypatch.setattr(helper,'emit',fail)
    with pytest.raises(ValueError): helper.emit_result(value)


@pytest.mark.parametrize('kind', ['address','quiet','language','ordinary','combat'])
def test_all_text_result_paths_send_raw_text_before_optional_tokens(monkeypatch,kind):
    emitted=[]
    monkeypatch.setattr(helper,'emit',emitted.append)
    monkeypatch.setattr(helper.sys,'stdin',io.StringIO(''))
    data=frame('こんにちは', model='unused',max_tokens=72,history=[],conversation_history='',event={})
    if kind=='address':
        data['address_reply']='朝鮮の朝や。'
    elif kind=='quiet':
        data=frame('静かにして',model='unused',max_tokens=72)
    elif kind=='language':
        data['language_requested']=True
        monkeypatch.setattr(language_helper,'run',lambda _:{'status':'answer','text':'朝鮮の朝や。'})
    else:
        monkeypatch.setattr(helper,'GameEvent',SimpleNamespace(model_validate=lambda value:value))
        if kind=='ordinary':
            monkeypatch.setattr(helper,'BridgeLLM',lambda *_:object())
            monkeypatch.setattr(helper,'DogidoStateMachine',lambda *_args,**_kw:SimpleNamespace(
                _render_player_chat_reply=lambda _: '朝鮮の朝や。',player_chat_repair=None))
        else:
            data.update(kind='ambient',details={},fallback_text='元の文',temperature=0.6)
            monkeypatch.setattr(helper,'BridgeLLM',lambda *_args,**_kw:SimpleNamespace(generate_leaf_text=lambda _: '朝鮮の朝や。'))
    (helper.run_combat_leaf if kind=='combat' else helper.run_turn)(data)
    assert len(emitted)==1 and emitted[0]['op']=='result'
    assert emitted[0]['text']==('' if kind=='quiet' else '朝鮮の朝や。')
    assert 'spoken_text' not in emitted[0]


@pytest.mark.parametrize('text', ['句を思い出して','剣に持ち替えて'])
def test_nontext_handoff_never_waits_for_token_request(monkeypatch,text):
    emitted=[]
    monkeypatch.setattr(helper,'emit',emitted.append)
    monkeypatch.setattr(helper.sys,'stdin',SimpleNamespace(readline=fail))
    helper.run_turn(frame(text,model='unused',max_tokens=72))
    assert len(emitted)==1 and 'text' not in emitted[0]


@pytest.mark.parametrize('mode', ['eof','tokens','wrong_text'])
def test_actual_helper_stdio_keeps_one_process_and_projects_mock_tokens(tmp_path,mode):
    import subprocess
    from pathlib import Path
    path=Path(helper.__file__).resolve()
    calls=tmp_path/'calls'
    program='''
import json,runpy,sys,types
from pathlib import Path
script,calls=sys.argv[1:]
def factory():
    with open(calls,'a') as f: f.write('factory\\n')
    def tagger(text):
        with open(calls,'a') as f: f.write(text+'\\n')
        return [types.SimpleNamespace(surface='猫',feature=types.SimpleNamespace(goshu='和',pos1='名詞',kana='ネコ',pron='ネーコ'))]
    return tagger
sys.modules['fugashi']=types.SimpleNamespace(Tagger=factory)
sys.path.insert(0,str(Path(script).parent))
runpy.run_path(script,run_name='__main__')
'''
    wire=json.dumps({'address_reply':' 猫 '})+'\n'
    if mode!='eof':
        wire+=json.dumps({'op':'tts_tokens','schema_version':1,'request_id':'r','text':'猫' if mode=='tokens' else '別の文'})+'\n'
    completed=subprocess.run([sys.executable,'-c',program,str(path),str(calls)],input=wire,
        text=True,capture_output=True,timeout=5,check=False)
    rows=[json.loads(line) for line in completed.stdout.splitlines()]
    assert rows[0]=={'op':'result','text':' 猫 '}
    if mode=='tokens':
        assert completed.returncode==0
        assert len(rows)==2 and rows[1]['request_id']=='r' and rows[1]['tokens'][0]['surface']=='猫'
        assert calls.read_text().splitlines()==['factory','朝','猫']
    elif mode=='eof':
        assert completed.returncode==0 and len(rows)==1
        assert not calls.exists()
    else:
        assert completed.returncode==1 and rows[-1]['op']=='error'
        assert not calls.exists()
