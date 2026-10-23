"""採取準備と記録の境界だけを検査する。実モデル・実サービス・通常記憶は使わない。"""
import importlib.util
import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('experiment', HERE / 'experiment.py')
experiment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiment)


class PreparationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'original'
        target = self.source / 'sessions/original/long_term'
        target.mkdir(parents=True)
        self.entries = [dict(id=f'hk_{i}', author='dogido', kind='agent_haiku',
                             created_at=f'2026-10-{i:02d}T00:00:00+00:00',
                             text=f'原句{i}\n中七\n下五', surface_text=f'原句{i}\n中七\n下五',
                             lines=[{}, {}, {}]) for i in range(1, 5)]
        (target / 'haiku_entries.jsonl').write_text('\n'.join(json.dumps(row) for row in self.entries) + '\n')
        (target / 'haiku_revisions.jsonl').write_text('{"human_revision":"人間の添削"}\n')
        self.design = experiment.read(HERE / 'design.json')

    def prepare(self, name='prepared'):
        return experiment.prepare(self.source, self.root / name, self.design, '同一の指示\n')

    def test_matrix_and_source_isolation(self):
        before = {str(p): p.read_bytes() for p in self.source.rglob('*') if p.is_file()}
        result = self.prepare()
        runs = result['ordered_runs']
        self.assertEqual(len(runs), 27)
        self.assertEqual({c['entry_id'] for c in result['cases']}, {'hk_2', 'hk_3', 'hk_4'})
        self.assertEqual(len({(r['case_id'], r['player_model_requested'], r['reasoning_effort']) for r in runs}), 27)
        self.assertEqual(len({r['player_prompt_sha256'] for r in runs}), 1)
        files = list((self.root / 'prepared/runs').glob('*/memory/sessions/*/long_term/haiku_entries.jsonl'))
        self.assertEqual(len(files), 27)
        self.assertFalse(list((self.root / 'prepared').rglob('haiku_revisions.jsonl')))
        untouched = files[1].read_bytes()
        files[0].write_text('別条件の変更')
        self.assertEqual(files[1].read_bytes(), untouched)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.source.rglob('*') if p.is_file()})

    def test_no_overwrite_or_nested_output(self):
        self.prepare()
        with self.assertRaises(FileExistsError):
            self.prepare()
        with self.assertRaises(ValueError):
            experiment.prepare(self.source, self.source / 'nested', self.design, 'x')

    def test_reproducible_order(self):
        first = self.prepare('first')
        second = self.prepare('second')
        self.assertEqual(first['ordered_runs'], second['ordered_runs'])

    def test_wrong_server_rejected(self):
        result = self.prepare()
        run = self.root / 'prepared/runs' / result['ordered_runs'][0]['run_id']
        with self.assertRaises(ValueError):
            experiment.bind(run, 5055)
        class Fake:
            def call(self, *args, **kwargs):
                return [{'key': 'regular-player:hk_4'}]
        with patch.object(experiment, 'client', return_value=Fake()), self.assertRaises(ValueError):
            experiment.bind(run, 5067)

    def test_packet_hides_evaluation_internals(self):
        result = self.prepare()
        record = result['ordered_runs'][0]
        run = self.root / 'prepared/runs' / record['run_id']
        experiment.log(run, {'event': 'opened', 'session_id': 'private'})
        experiment.log(run, {'event': 'player_sent', 'text': 'どういう意味？'})
        experiment.log(run, {'event': 'dogido_reply', 'text': 'こんな意味やで', 'response': {'internal': 'hidden'}})
        packet = experiment.packet(run, record, {'workshop': {'canonical_lines': ['上五', '中七', '下五'], 'pending_lines': []}, 'private': 'hidden'})
        self.assertEqual(len(packet['conversation']), 2)
        self.assertNotIn('hidden', json.dumps(packet))
        self.assertNotIn('player_model_requested', packet)

    def test_fixed_dogido_settings_reject_drift(self):
        path = self.root / 'baseline.json'
        experiment.fixed_file(path, {'model': 'same', 'max_tokens': 72})
        experiment.fixed_file(path, {'model': 'same', 'max_tokens': 72})
        with self.assertRaises(ValueError):
            experiment.fixed_file(path, {'model': 'changed', 'max_tokens': 72})

    def fake_session(self):
        result = self.prepare()
        record = result['ordered_runs'][0]
        run = self.root / 'prepared/runs' / record['run_id']
        class Fake:
            sent = []
            closed = False
            fail = False
            def call(self, endpoint, body=None, base=None):
                if endpoint == 'poems': return [{'key': record['poem_key']}]
                if endpoint == 'prompts': return {'settings': {'system': '固定'}, 'version': 0}
                if endpoint == 'open': return {'session_id': 'text_test'}
                if endpoint == 'snapshot':
                    lines = record['original_text'].splitlines()
                    if self.sent: lines[1] = '変わった中七'
                    return {'workshop': {'state': 'open', 'canonical_lines': lines, 'pending_lines': []},
                            'text_state': {'lines': [{'surface_text': line} for line in lines]}}
                if endpoint == 'last-prompt': return {'model': 'fixed'}
                if endpoint == 'close': self.closed = True; return {'ok': True}
                raise AssertionError(endpoint)
            def speak(self, sid, text, base):
                self.sent.append(text)
                if self.fail: raise TimeoutError('pending')
                return {'turn': {'text': '直したで', 'playback_status': 'audio_disabled',
                                 'text_delivery_acknowledged': True}}
        api = Fake()
        args = argparse.Namespace(run_dir=run, port=5067, command='open', player_agent_id='agent-test',
                                  player_model_reported='unreported')
        return run, api, args

    def test_recording_round_trip_and_final_verse(self):
        run, api, args = self.fake_session()
        with patch.object(experiment, 'client', return_value=api), redirect_stdout(io.StringIO()):
            experiment.interact(args)
            args.command = 'say'
            args.text_file = self.root / 'input.txt'
            args.text_file.write_text('中七を変えて')
            experiment.interact(args)
            args.command, args.reason = 'finish', 'player_done'
            experiment.interact(args)
        self.assertEqual(api.sent, ['中七を変えて'])
        self.assertTrue(api.closed)
        self.assertEqual(experiment.read(run / 'summary.json')['changed_lines'], [2])
        self.assertEqual(experiment.read(run / 'run.json')['status'], 'finished')

    def test_uncertain_delivery_is_not_resent(self):
        run, api, args = self.fake_session()
        with patch.object(experiment, 'client', return_value=api), redirect_stdout(io.StringIO()):
            experiment.interact(args)
            args.command = 'say'
            args.text_file = self.root / 'input.txt'
            args.text_file.write_text('変えて')
            api.fail = True
            with self.assertRaises(TimeoutError): experiment.interact(args)
            with self.assertRaises(ValueError): experiment.interact(args)
        self.assertEqual(api.sent, ['変えて'])
        self.assertEqual(experiment.read(run / 'run.json')['status'], 'awaiting_reply')


if __name__ == '__main__':
    unittest.main()
