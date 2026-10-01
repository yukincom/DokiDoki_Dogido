"""Dictionary mocks only; this test never imports fugashi or synthesizes audio."""
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('tts_adapter',Path(__file__).with_name('tts_unidic_adapter.py'))
adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)

def word(surface='猫',**fields):
    return SimpleNamespace(surface=surface,feature=SimpleNamespace(**fields))

def request(text='猫',**fields):
    return dict(schema_version=1,request_id='r',text=text,**fields)

class AdapterTests(unittest.TestCase):
    def test_lazy_initialization_and_raw_feature_projection(self):
        calls=[]
        def factory():
            calls.append('factory')
            def tagger(text):
                calls.append(text)
                return [word(kana='ネコ',pron='ネーコ',goshu='和',pos1='名詞')]
            return tagger
        reader=adapter.Unidic(factory)
        self.assertEqual(calls,[])
        result=adapter.handle(request(),reader)
        self.assertEqual(calls,['factory','朝','猫'])
        self.assertEqual(result,{'schema_version':1,'request_id':'r','status':'ok','tokens':[{'surface':'猫','goshu':'和','pos1':'名詞','kana':'ネコ','pron':'ネーコ'}]})
        adapter.handle(request('朝鮮'),reader)
        self.assertEqual(calls,['factory','朝','猫','朝鮮'])
        # No conversion, preferred mapping, or exception-table substitution here.
        self.assertEqual(result['tokens'][0]['surface'],'猫')

    def test_init_failure_is_cached_and_does_not_parse(self):
        calls=[]
        def factory():calls.append('factory');raise RuntimeError('optional missing')
        reader=adapter.Unidic(factory)
        for _ in range(2):
            self.assertEqual(adapter.handle(request(),reader)['status'],'unavailable')
        self.assertEqual(calls,['factory'])

    def test_warmup_failure_is_unavailable(self):
        def factory():
            def tagger(text):raise RuntimeError('warmup')
            return tagger
        self.assertEqual(adapter.handle(request(),adapter.Unidic(factory))['status'],'unavailable')

    def test_mid_parse_failure_does_not_return_partial_tokens(self):
        def factory():
            def tagger(text):
                if text=='朝':return iter([])
                def partial():
                    yield word(kana='ネコ')
                    raise RuntimeError('parse')
                return partial()
            return tagger
        result=adapter.handle(request(),adapter.Unidic(factory))
        self.assertEqual(result['status'],'parse_error')
        self.assertEqual(result['tokens'],[])

    def test_empty_success_and_missing_feature_values(self):
        for tokens in [[],[word()]]:
            result=adapter.handle(request(),adapter.Unidic(lambda:lambda _:tokens))
            self.assertEqual(result['status'],'ok')
            self.assertEqual(len(result['tokens']),len(tokens))
            if tokens:self.assertEqual(result['tokens'][0]['kana'],None)

    def test_invalid_protocol_never_initializes_dictionary(self):
        def forbidden():raise AssertionError('must not initialize')
        reader=adapter.Unidic(forbidden)
        for bad in [None,{},dict(request(),schema_version=True),dict(request(),schema_version=2),dict(request(),text=1),request(overlay=[]),dict(request(),request_id='')]:
            with self.assertRaises(ValueError):adapter.handle(bad,reader)
            self.assertFalse(reader.attempted)

    def test_cli_protocol_writes_only_complete_frames(self):
        reader=adapter.Unidic(lambda:lambda _:[word(kana='ネコ')])
        source=io.TextIOWrapper(io.BytesIO((json.dumps(request())+'\n').encode()))
        output=io.TextIOWrapper(io.BytesIO(),write_through=True)
        with patch.object(adapter,'Unidic',return_value=reader),patch.object(adapter.sys,'stdin',source),patch.object(adapter.sys,'stdout',output):
            self.assertEqual(adapter.main(),0)
        output.flush(); output.buffer.seek(0)
        response=json.loads(output.buffer.read())
        self.assertEqual(response['status'],'ok')
        self.assertEqual(response['request_id'],'r')

if __name__=='__main__':unittest.main()
