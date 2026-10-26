import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import bookshelf_public_data as data
import bookshelf_public_repository as publish
from publish_bookshelf_data import text_file, write_file, check_files, REPOSITORY, REMOTE, URL


ARCHIVE = '00000000-0000-4000-8000-000000000001'


def source_plan(root, *, capture=True, text='あさのひ', journey='PRIVATE-JOURNEY-SENTINEL'):
    secret = 'PRIVATE-CONTEXT-SENTINEL'
    files = []
    def add(path, value):
        file = text_file(path, json.dumps(value, ensure_ascii=False)); files.append(file)
        return {k: file[k] for k in ('path', 'bytes', 'sha256')}
    record = {'original': {'text': text, 'reading_text': text, 'lines': [], 'author': secret,
                          'materials_snapshot': secret, 'internal_capture': {'requests': [{'request_index': 1, 'kind': 'haiku_draft'}]}},
              'current': {'revised_text': text, 'revised_surface_text': text, 'comment': secret},
              'revisions': [{'revised_text': text, 'comment': secret, 'source': secret}],
              'dialogue': [{'player': secret, 'dogido': secret}], 'player': {'note': secret},
              'photos': [{'directory': '/Users/' + secret}], 'future_unknown': secret}
    snapshot = add('old/snapshot.json', {'identity': {'local': secret}, 'record': record})
    requests = []
    if capture:
        raw = root / 'hidden.f32'; raw.write_bytes(bytes(range(64)))
        raw_ref = {'path': 'old/hidden.f32', 'bytes': raw.stat().st_size, 'sha256': hashlib.sha256(raw.read_bytes()).hexdigest()}
        files.append(dict(raw_ref, source=str(raw)))
        manifest = add('old/manifest.json', {'schema': 'dogido.internal_capture.v1', 'backend': 'test', 'response_id': 'response',
            'shape': [1, 2, 8], 'dtype': 'float32-le', 'boundary': 'decoder_layer_output_pre_final_norm',
            'hidden_sha256': raw_ref['sha256'], 'hidden_bytes': 64, 'prompt_token_ids': [11, 12, 13],
            'predicted_token_ids': [7], 'positions': [{'generation_index': 0, 'input_position': 3, 'predicted_token_id': 7, 'context': secret}],
            'rendered_text': secret, 'identity': {'model': {'path': '/Users/' + secret + '/model.gguf', 'sha256': 'a' * 64, 'size_bytes': 128},
                'generation_options': {'-m': secret}, 'collector': {'path': '/Users/' + secret + '/collector', 'sha256': 'b' * 64}},
            'generation_settings': {'temperature': 0.7, 'generation_prompt': secret, 'future': secret}})
        readout = add('old/lens.json', {'schema': 'dogido.j_lens.v1', 'response_id': 'response', 'manifest_sha256': manifest['sha256'],
            'hidden_sha256': raw_ref['sha256'], 'shape': [1, 2, 8], 'model_sha256': 'a' * 64, 'private': secret,
            'positions': [{'phase': 'before', 'position': 0, 'input_position': 3, 'predicted_token_id': 7, 'predicted_piece': '空',
                'layers': [{'layer': 0, 'method': 'transferred_j_lens', 'candidates': [{'id': 7, 'piece': '空', 'score': 9.25, 'logprob': -0.5, 'extra': secret}]}]}]})
        translation = add('old/translation.json', {'source': {'readout_sha256': readout['sha256'], 'phase': 'before', 'layer': 0,
                    'prompt_sha256': 'c' * 64, 'private': secret}, 'model': {'endpoint': secret},
                    'translations': [{'id': 7, 'piece': '空', 'text': '空', 'method': 'original', 'private': secret}]})
        requests = [{'request_index': 1, 'response_id': 'response', 'capture_status': 'complete',
                     'manifest': manifest, 'hidden': raw_ref, 'j_lens': readout, 'translations': [translation]}]
    journey_ref=add('journeys/journey_private/snapshots/example.json', {'record':{'synopses':[journey],'id':'journey_private'},'note':'PRIVATE-JOURNEY-NOTE'})
    add('index.json', {'archive_id': ARCHIVE, 'journeys':[{'id':'journey_private','snapshot':journey_ref}], 'poems': [{'id': 'private-session:poem', 'snapshot': snapshot, 'requests': requests}]})
    return {'repository': REPOSITORY, 'checkout': str(root / 'state'), 'files': files, 'poems': 1, 'captures': int(capture)}


class ProjectionTests(unittest.TestCase):
    def test_whitelist_removes_private_context_and_preserves_values_and_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); plan = source_plan(root); before = copy.deepcopy(plan)
            public = data.projection(plan, 42)
            self.assertEqual(plan, before)
            json_text = '\n'.join(f.get('content', '') for f in public['files'])
            for forbidden in ('PRIVATE-CONTEXT-SENTINEL', 'PRIVATE-JOURNEY-SENTINEL', 'PRIVATE-JOURNEY-NOTE', 'journey_private', 'prompt_token_ids', 'rendered_text', '/Users/', 'generation_options', 'private-session:poem'):
                self.assertNotIn(forbidden, json_text)
            self.assertTrue(all(f['path'].startswith(f'contributors/42/{ARCHIVE}/') for f in public['files']))
            row = public['index']['poems'][0]['requests'][0]
            by_path = {f['path']: f for f in public['files']}
            lens = json.loads(by_path[row['j_lens']['path']]['content'])
            self.assertEqual(lens['positions'][0]['layers'][0]['candidates'], [{'id': 7, 'piece': '空', 'score': 9.25, 'logprob': -0.5}])
            self.assertEqual(lens['manifest_sha256'], row['manifest']['sha256'])
            translated = json.loads(by_path[row['translations'][0]['path']]['content'])
            self.assertEqual(translated['source']['readout_sha256'], row['j_lens']['sha256'])
            output = root / 'preview'; output.mkdir()
            for f in public['files']: write_file(output, f)
            check_files(output, public['files'])
            self.assertEqual((output / row['hidden']['path']).read_bytes(), (root / 'hidden.f32').read_bytes())
            self.assertEqual(public, data.projection(plan, 42))
            self.assertEqual(public, data.projection(source_plan(root, journey='変更した非公開の旅'), 42))

    def test_corruption_and_cross_request_links_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); plan = source_plan(root)
            plan['files'][0]['content'] += ' '
            with self.assertRaisesRegex(ValueError, '変わっています'):
                data.projection(plan, 42)
            with self.assertRaises(ValueError): data.projection(source_plan(root), True)
            self.assertEqual(data.projection(source_plan(root, capture=False), 42)['captures'], 0)


class ContributionTests(unittest.TestCase):
    def test_real_lfs_setup_with_disabled_repository_hooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            publish.git(root, 'init', '-b', 'main')
            publish.git(root, 'lfs', 'install', '--local', '--skip-smudge', '--skip-repo')
            (root / '.gitattributes').write_text('*.f32 filter=lfs diff=lfs merge=lfs -text\n')
            raw = bytes(range(64)); (root / 'probe.f32').write_bytes(raw)
            publish.git(root, 'add', '.gitattributes', 'probe.f32')
            pointer = publish.git(root, 'show', ':probe.f32').stdout.decode()
            self.assertIn('oid sha256:' + hashlib.sha256(raw).hexdigest(), pointer)
            self.assertIn('size 64', pointer)

    def test_real_git_fork_batch_retry_and_untrusted_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); upstream = root / 'upstream.git'; fork = root / 'fork.git'; seed = root / 'seed'
            def command(*args, cwd=None, check=True):
                return subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=42+test@users.noreply.github.com',
                    '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null', *args], cwd=cwd, check=check, capture_output=True)
            command('init', '--bare', '--initial-branch=main', str(upstream))
            seed.mkdir(); command('init', '-b', 'main', cwd=seed)
            for file in data.seed_files(): write_file(seed, file)
            command('add', '.', cwd=seed); command('commit', '-m', '公開用の正本を準備', cwd=seed)
            command('push', str(upstream), 'HEAD:main', cwd=seed)
            command('clone', '--bare', str(upstream), str(fork))
            user = {'login': 'tester', 'id': 42}
            repository = {'full_name': REPOSITORY, 'id': 100, 'private': False, 'default_branch': 'main', 'owner': {'id': 1}}
            fork_meta = {'full_name': 'tester/DokiDoki-Dogido-data', 'id': 101, 'private': False, 'owner': {'id': 42}, 'source': {'id': 100}}
            fork_url = 'https://github.com/tester/DokiDoki-Dogido-data.git'
            prs = []; calls = []; fail_push = False
            def local_git(directory, *args, **kwargs):
                calls.append(args)
                if args[0] == 'lfs': return subprocess.CompletedProcess(args, 0, b'', b'')
                if args[0] == 'push' and fail_push: raise RuntimeError('通信失敗の模擬')
                args = tuple(str(upstream) if x == REMOTE else str(fork) if x == fork_url else x for x in args)
                return command('-C', str(directory), *args, check=kwargs.get('check', True))
            def github(path, *, method='GET', payload=None, **kwargs):
                if path == 'user': return user
                if path == f'repos/{REPOSITORY}': return repository
                if path == 'repos/tester/DokiDoki-Dogido-data': return fork_meta
                if '/pulls?' in path:
                    if prs:
                        branch = prs[0]['head']['ref']; prs[0]['head']['sha'] = command('rev-parse', branch, cwd=fork).stdout.decode().strip()
                    return prs
                if path.endswith('/pulls') and method == 'POST':
                    branch = payload['head'].split(':', 1)[1]
                    pr = {'html_url': URL + '/pull/1', 'base': {'repo': repository},
                          'head': {'repo': fork_meta, 'ref': branch, 'sha': command('rev-parse', branch, cwd=fork).stdout.decode().strip()}}
                    prs.append(pr); return pr
                raise AssertionError(path)
            ok = subprocess.CompletedProcess([], 0, b'', b'')
            with patch.object(publish, 'api', side_effect=github), patch.object(publish, 'git', side_effect=local_git), patch.object(publish, 'run', return_value=ok):
                first = publish.publish_public(source_plan(root, capture=False))
                self.assertEqual(first['status'], 'submitted'); self.assertEqual(len(prs), 1)
                calls.clear()
                same = publish.publish_public(source_plan(root, capture=False, journey='旅とメモだけを変更'))
                self.assertEqual(same['status'], 'up_to_date'); self.assertEqual(same['commit'], first['commit'])
                self.assertFalse(any(c[0] in ('push', 'clone') for c in calls))
                fail_push = True
                with self.assertRaisesRegex(RuntimeError, '通信失敗'):
                    publish.publish_public(source_plan(root, capture=False, text='ゆうひ'))
                fail_push = False
                next_result = publish.publish_public(source_plan(root, capture=False, text='ゆうひ'))
                self.assertEqual(next_result['status'], 'submitted'); self.assertEqual(len(prs), 1)
                self.assertEqual(next_result['poems'], 1)
                # 外部で加筆し、その後削除してtipを戻しても未知の履歴を取り込まない。
                branch = prs[0]['head']['ref']; intruder = root / 'intruder'
                command('clone', '--branch', branch, str(fork), str(intruder))
                (intruder / 'secret').write_text('private'); command('add', 'secret', cwd=intruder); command('commit', '-m', '追加', cwd=intruder)
                command('rm', 'secret', cwd=intruder); command('commit', '-m', '削除', cwd=intruder)
                command('push', 'origin', branch, cwd=intruder)
                with self.assertRaisesRegex(ValueError, '作っていない履歴'):
                    publish.publish_public(source_plan(root, capture=False, text='よる'))


if __name__ == '__main__': unittest.main()
