import importlib.util
import json
from pathlib import Path
import tempfile
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


if __name__ == '__main__': unittest.main()
