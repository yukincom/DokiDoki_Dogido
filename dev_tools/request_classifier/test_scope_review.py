"""全件の採否台帳から現行データを復元し、旧原文と分割・出自の保持を検査する。"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
import unittest

from dataset import ROOT, SPLITS, model_example, read_splits

CURATION = ROOT / 'curation' / '2026-10-14-functional-scope'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def row_digest(row):
    payload = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


class FunctionalScopeReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((CURATION / 'manifest.json').read_text(encoding='utf-8'))
        cls.decisions = [json.loads(line) for line in
                         (CURATION / 'decisions.jsonl').read_text(encoding='utf-8').splitlines()]
        cls.original = read_splits(CURATION / 'original')
        cls.active = read_splits()

    def test_original_and_release_files_match_recorded_hashes(self):
        for name, expected in self.manifest['original_files'].items():
            self.assertEqual(digest(CURATION / name), expected, name)
        for name, expected in self.manifest['active_files'].items():
            self.assertEqual(digest(ROOT / name), expected, name)
        for name, expected in self.manifest['validation_files'].items():
            self.assertEqual(digest(CURATION / name), expected, name)
        self.assertEqual(digest(CURATION / 'decisions.jsonl'), self.manifest['decisions_sha256'])
        self.assertEqual(digest(CURATION / 'criteria.md'), self.manifest['criteria_sha256'])
        archived_report = json.loads((CURATION / 'original/data_report.json').read_text(encoding='utf-8'))
        for split in SPLITS:
            self.assertEqual(digest(CURATION / 'original' / f'{split}.jsonl'),
                             archived_report[split]['sha256'])

    def test_every_original_has_one_decision_and_reconstructs_release(self):
        original = {r['id']: r for rows in self.original.values() for r in rows}
        decisions = {d['id']: d for d in self.decisions}
        self.assertEqual(len(self.decisions), 762)
        self.assertEqual(len(decisions), len(self.decisions))
        self.assertEqual(set(original), set(decisions))
        self.assertEqual(dict(Counter(d['action'] for d in self.decisions)), self.manifest['actions'])
        for split in SPLITS:
            reconstructed = []
            for old in self.original[split]:
                decision = decisions[old['id']]
                self.assertEqual(decision['split'], split)
                self.assertEqual(decision['group_id'], old['group_id'])
                self.assertEqual(decision['before_sha256'], row_digest(old))
                self.assertTrue(decision['reason'].strip())
                self.assertTrue(decision['evidence'])
                if decision['action'] == 'exclude':
                    self.assertIn(decision['scope'], ('workshop', 'dedicated_learning'))
                    self.assertIsNone(decision['after_sha256'])
                    self.assertEqual(decision['changes'], {})
                    self.assertEqual(old['source'], 'synthetic')
                    continue
                self.assertIn(decision['action'], ('keep', 'revise'))
                new = deepcopy(old)
                new.update(decision['changes'])
                self.assertEqual(bool(decision['changes']), decision['action'] == 'revise')
                self.assertEqual(decision['after_sha256'], row_digest(new))
                for field in ('id', 'group_id', 'source', 'label'):
                    self.assertEqual(new[field], old[field], (old['id'], field))
                if old['source'] != 'synthetic':
                    self.assertEqual(model_example(new), model_example(old), old['id'])
                reconstructed.append(new)
            self.assertEqual(reconstructed, self.active[split], split)
        protected = Counter(r['source'] for rows in self.active.values() for r in rows
                            if r['source'] != 'synthetic')
        self.assertEqual(protected, {'user_example': 32, 'observed_text': 5})

    def test_scope_annotations_are_consistent_and_never_model_input(self):
        decisions = {d['id']: d for d in self.decisions}
        for split, rows in self.active.items():
            tagged = 0
            for row in rows:
                boundary = decisions[row['id']]['scope'] == 'unsupported_boundary'
                self.assertEqual('unsupported_request' in row['tags'], boundary, row['id'])
                self.assertIn(decisions[row['id']]['scope'], ('ordinary', 'unsupported_boundary'))
                if boundary:
                    tagged += 1
                    self.assertEqual(row['label'], 'other_request')
                    untagged = deepcopy(row)
                    untagged['tags'].remove('unsupported_request')
                    self.assertEqual(model_example(untagged), model_example(row))
            self.assertEqual(tagged, self.manifest['splits'][split]['unsupported_requests'])


if __name__ == '__main__':
    unittest.main()
