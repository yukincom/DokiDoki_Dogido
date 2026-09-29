"""Shared-token contract tests. The optional SDK is replaced before any call."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dogido_server import tts_reading as canonical
import tts_shared_tokens as shared


def word(surface='猫', **features):
    return SimpleNamespace(surface=surface, feature=SimpleNamespace(**features))


def request(text='猫', **extra):
    return dict(op='tts_tokens', schema_version=1, request_id='request', text=text, **extra)


class SharedTokenTests(unittest.TestCase):
    def setUp(self):
        canonical.reset_unidic_tagger_for_tests()
        self.sdk = patch.dict(sys.modules, {'fugashi': SimpleNamespace(Tagger=self.forbidden)})
        self.sdk.start()

    def tearDown(self):
        self.sdk.stop()
        canonical.reset_unidic_tagger_for_tests()

    @staticmethod
    def forbidden():
        raise AssertionError('real SDK initialization is forbidden')

    def test_neutral_transform_and_tts_share_one_factory_and_warmup(self):
        for first in ('tts', 'neutral'):
            with self.subTest(first=first):
                canonical.reset_unidic_tagger_for_tests()
                calls=[]
                def tagger(text):
                    calls.append(text)
                    return [word(text, kana='ネコ', pron='ネーコ', goshu='和', pos1='名詞')]
                def factory():
                    calls.append('factory')
                    return tagger
                with patch.dict(sys.modules, {'fugashi':SimpleNamespace(Tagger=factory)}):
                    if first == 'neutral':
                        self.assertEqual(canonical.hiraganize_japanese_text('猫'), 'ねこ')
                    result=shared.handle(request())
                    if first == 'tts':
                        self.assertEqual(canonical.hiraganize_japanese_text('猫'), 'ねこ')
                    again=shared.handle(request('朝鮮'))
                self.assertEqual(calls, ['factory','朝','猫','猫','朝鮮'])
                self.assertEqual(result['tokens'][0]['surface'], '猫')
                self.assertEqual(result['tokens'][0]['kana'], 'ネコ')
                self.assertEqual(again['tokens'][0]['surface'], '朝鮮')
                self.assertNotIn('spoken_text', result)

    def test_initialization_failure_is_shared_and_cached(self):
        calls=[]
        def failed():
            calls.append('factory')
            raise RuntimeError('optional dictionary unavailable')
        with patch.dict(sys.modules, {'fugashi': SimpleNamespace(Tagger=failed)}):
            for _ in range(2):
                self.assertEqual(shared.handle(request())['status'], 'unavailable')
                self.assertEqual(canonical.hiraganize_japanese_text('猫'), '猫')
        self.assertEqual(calls, ['factory'])

    def test_token_parse_failure_is_atomic_and_dictionary_is_retained(self):
        calls=[]
        def tagger(text):
            calls.append(text)
            if text == '朝': return []
            if text == '失敗':
                def partial():
                    yield word(kana='ネコ')
                    raise RuntimeError('broken parse')
                return partial()
            return [word(text, kana='ネコ')]
        with patch.dict(sys.modules, {'fugashi':SimpleNamespace(Tagger=lambda: tagger)}):
            self.assertEqual(shared.handle(request('失敗')), {'schema_version':1,'request_id':'request','status':'parse_error','tokens':[]})
            self.assertEqual(shared.handle(request())['status'], 'ok')
        self.assertEqual(calls, ['朝', '失敗', '猫'])

    def test_empty_success_is_not_unavailable_and_optional_fields_are_explicit(self):
        with patch.object(canonical, '_get_unidic_tagger', return_value=lambda _: []):
            self.assertEqual(shared.handle(request())['status'], 'ok')
        with patch.object(canonical, '_get_unidic_tagger', return_value=lambda _: [word()]):
            row=shared.handle(request())['tokens'][0]
            self.assertEqual(row, {'surface':'猫','goshu':None,'pos1':None,'kana':None,'pron':None})

    def test_malformed_frame_is_rejected_before_getting_dictionary(self):
        with patch.object(canonical, '_get_unidic_tagger', side_effect=AssertionError('forbidden')) as getter:
            for frame in [None, {}, dict(request(), op='reading'), dict(request(), schema_version=True), request(overlay=[]), dict(request(), text=None)]:
                with self.subTest(frame=frame), self.assertRaises(ValueError):
                    shared.handle(frame)
            getter.assert_not_called()

    def test_workshop_dispatch_is_token_only(self):
        import workshop_helper
        with patch.object(canonical, '_get_unidic_tagger', return_value=lambda _: [word(kana='ネコ')]) as getter:
            self.assertEqual(workshop_helper.handle(request())['tokens'][0]['surface'], '猫')
            getter.assert_called_once()
            for op in ('reading_overlay', 'reading', 'whole_verse', 'knowledge_route', 'player_edit', 'prepare_details', 'fragment_candidate'):
                with self.subTest(op=op), self.assertRaises(ValueError):
                    workshop_helper.handle({'op':op,'text':'猫','rows':[]})
        self.assertNotIn('workshop_oracle', workshop_helper.__dict__)


if __name__ == '__main__': unittest.main()
