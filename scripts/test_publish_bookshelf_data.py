import importlib.util
import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('publish', Path(__file__).with_name('publish_bookshelf_data.py'))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


class PublishTests(unittest.TestCase):
    def test_public_destination_stops_before_creating_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / 'unused'
            with patch.object(m, 'run', return_value=type('R', (), {'stdout': json.dumps({'private': False, 'full_name': m.REPOSITORY}).encode()})()):
                with self.assertRaisesRegex(ValueError, '非公開'):
                    m.publish({'repository': m.REPOSITORY, 'checkout': str(root)})
            self.assertFalse(root.exists())

    def test_exact_bytes_roundtrip_and_existing_research_files_are_immutable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            row = m.text_file('2026/10251200/snapshots/test.json', '{"text":"宝"}\n')
            m.write_file(root, row); m.write_file(root, row); m.check_files(root, [row])
            with self.assertRaisesRegex(ValueError, '上書き'):
                m.write_file(root, m.text_file(row['path'], 'changed'))
            m.check_files(root, [row])

    def test_wrong_hash_and_symlink_never_replace_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); row = m.text_file('raw/file.json', 'source')
            row['sha256'] = '0' * 64
            with self.assertRaisesRegex(ValueError, '変わりました'):
                m.write_file(root, row)
            self.assertFalse((root / 'raw/file.json').exists())
            (root / 'link').symlink_to(root / 'raw')
            with self.assertRaisesRegex(ValueError, 'リンク'):
                m.safe_path(root, 'link/file.json')
            for path in ['../escape', '/absolute', '.git/config', 'x/../a']:
                with self.assertRaises(ValueError): m.safe_path(root, path)

    def test_batch_push_sends_all_changes_and_skips_an_unchanged_batch(self):
        # 本物のGitと一時bare repositoryで差分を検証する。GitHub通信とLFS転送だけを模擬。
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve(); remote = base / 'remote.git'; checkout = base / 'checkout'
            subprocess.run(['git', 'init', '--bare', '--initial-branch=main', str(remote)], check=True, capture_output=True)
            calls = []
            def local_git(root, *args, **kwargs):
                calls.append(args)
                if args[:2] == ('remote', 'get-url'):
                    return subprocess.CompletedProcess(args, 0, (m.REMOTE + '\n').encode(), b'')
                if args[0] == 'lfs':
                    return subprocess.CompletedProcess(args, 0, b'', b'')
                args = tuple(str(remote) if x == m.REMOTE else x for x in args)
                return subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                    '-c', 'commit.gpgsign=false', '-C', str(root), *args],
                    capture_output=True, check=kwargs.get('check', True))
            def plan(versions):
                files=[]; rows=[]
                for label, version in versions.items():
                    file=m.text_file(f'2026/10251200/snapshots/{label}-{version}.json', str(version))
                    files.append(file)
                    rows.append({'id':label,'snapshot':{k:file[k] for k in ('path','bytes','sha256')},'requests':[]})
                files.append(m.text_file('index.json',json.dumps({'poems':rows})))
                return {'repository':m.REPOSITORY,'checkout':str(checkout),'files':files,'poems':len(rows),'captures':0}
            github=subprocess.CompletedProcess([],0,json.dumps({'private':True,'full_name':m.REPOSITORY}).encode(),b'')
            with patch.object(m,'git',side_effect=local_git), patch.object(m,'run',return_value=github):
                first=m.publish(plan({'one':1,'two':1}))
                self.assertEqual((first['status'],first['poems']),('uploaded',2))
                calls.clear()
                same=m.publish(plan({'one':1,'two':1}))
                self.assertEqual(same['status'],'up_to_date')
                self.assertEqual(same['commit'],first['commit'])
                self.assertFalse(any(c[0] in ('push','clone') for c in calls))
                changed=m.publish(plan({'one':1,'two':2,'three':1}))
                self.assertEqual((changed['status'],changed['poems']),('uploaded',2))
                self.assertNotEqual(changed['commit'],first['commit'])
                self.assertTrue((checkout/'2026/10251200/snapshots/two-1.json').exists())
                m.journal(checkout/'.git/dogido-publish-pending.json',{'commit':changed['commit'],'remote_base':first['commit']})
                calls.clear()
                retried=m.publish(plan({'one':1,'two':2,'three':1}))
                self.assertEqual(retried['status'],'uploaded')
                self.assertTrue(any(c[0]=='clone' for c in calls))


if __name__ == '__main__': unittest.main()
