"""データ検査が分割混入や教師情報漏洩を見逃さないことを確認する。

保存済みデータのコピーを一件ずつ壊し、検査の失敗を確かめる。
モデル・ネットワーク・稼働中本体を使わず、元データを書き換えない。
"""
from copy import deepcopy
import unittest

from dataset import model_example, read_splits, validate


class DatasetIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = read_splits()

    def test_release_is_valid(self):
        validate(self.data)

    def test_group_leakage_is_rejected(self):
        data = deepcopy(self.data)
        data['test'][0]['group_id'] = data['train'][0]['group_id']
        with self.assertRaisesRegex(ValueError, 'group crosses splits'):
            validate(data)

    def test_surface_variation_cannot_hide_split_leakage(self):
        data = deepcopy(self.data)
        data['test'][0]['text'] = data['train'][0]['text'].replace('ドギド', 'どぎど') + '！！  '
        with self.assertRaisesRegex(ValueError, 'utterance crosses splits'):
            validate(data)

    def test_conflicting_duplicate_input_is_rejected(self):
        data = deepcopy(self.data)
        duplicate = deepcopy(data['train'][0])
        duplicate['id'] = 'train-999'
        duplicate['label'] = 'equip_sword'
        data['train'].append(duplicate)
        with self.assertRaisesRegex(ValueError, 'duplicate input'):
            validate(data)

    def test_ambiguous_action_label_is_rejected(self):
        data = deepcopy(self.data)
        row = next(r for r in data['train'] if r['needs_clarification'])
        row['label'] = 'recall_mount'
        with self.assertRaisesRegex(ValueError, 'unresolved request'):
            validate(data)

    def test_user_example_is_accepted_without_becoming_an_observation(self):
        data = deepcopy(self.data)
        data['train'][0]['source'] = 'user_example'
        validate(data)
        data['train'][0]['source'] = 'observed_text'
        with self.assertRaisesRegex(ValueError, 'observed text outside regression'):
            validate(data)

    def test_unknown_provenance_is_rejected(self):
        data = deepcopy(self.data)
        data['train'][0]['source'] = 'unverified_source'
        with self.assertRaisesRegex(ValueError, 'unknown source'):
            validate(data)

    def test_annotation_metadata_never_enters_model_text(self):
        row = deepcopy(next(r for r in self.data['train'] if r['context']))
        before = model_example(row)
        for field in ('id', 'group_id', 'source', 'rationale'):
            row[field] = 'DO_NOT_FEED_TO_MODEL'
        row['tags'] = ['DO_NOT_FEED_TO_MODEL']
        row['needs_clarification'] = True
        row['label'] = 'other_request'
        after = model_example(row)
        self.assertEqual(before['text'], after['text'])
        self.assertNotIn('DO_NOT_FEED_TO_MODEL', after['text'])
        self.assertNotEqual(before['labels'], after['labels'])
        self.assertTrue(after['text'].endswith(row['text']))


if __name__ == '__main__':
    unittest.main()
