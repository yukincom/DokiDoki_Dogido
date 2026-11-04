"""見込み: 原文CSVから位置を求めれば、語尾・改行を保ってColabへ教材を渡せる。
合格: 教師範囲の往復・用途の分離・ZIPのhashとコードが一致。実モデル/T4は別試験。
"""
import ast
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / 'apps/dogido-training-builder'
sys.path.insert(0, str(SOURCE))
import span_builder


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load('span_route_builder', SOURCE / 'builder.py')
packager = load('span_app_packager', SOURCE / 'Tools/build_app.py')
prepare = load('span_workspace_prepare', SOURCE / 'Tools/prepare_span_workspace.py')


class SpanBuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='span-builder-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        prepare.prepare(self.project)
        self.rows = []
        for number, (split, old, new) in enumerate([
            ('学習', '山の朝', '朝の道'), ('調整', '川の音', '海の音'),
            ('テスト', '夜の星', '冬の星'), ('回帰', '村の門', '里の門')], 1):
            self.rows.append({'使う': 'はい', 'ID': 'r' + str(number), '用途': split,
                'グループ': 'g' + str(number), '現在の句': old + '\nゆれる景色\nここに立つ',
                '発話': old + 'から' + new + 'に変えて', '変更元': old, '変更先': new})

    def write(self, rows=None, filename='教材.csv'):
        path = self.project / filename
        with path.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=span_builder.HEADERS, lineterminator='\n')
            writer.writeheader()
            writer.writerows(self.rows if rows is None else rows)
        return path

    # 見込み: 「に」が語句の一部でも、原文に指定した範囲だけを教師にできる。
    # 合格: 末尾のに・カンマ・改行・異体Unicodeを保持し、重複語の出現指定も正しい。
    def test_original_offsets_particles_and_newlines(self):
        self.rows[0].update({'発話': '花を花に変えて\n「広場,の中に」を選ぶ',
                             '変更元': '花', '元の出現番号': '1', '変更先': '花', '先の出現番号': '2'})
        self.rows[1].update({'発話': '川の音から\n海の音にに変えて', '変更先': '海の音に'})
        splits, skipped, _ = span_builder.read_csv([self.write()])
        self.assertEqual(skipped, [])
        self.assertEqual(splits['train'][0]['spans'], [
            {'role': 'from', 'start': 0, 'end': 1, 'text': '花'},
            {'role': 'to', 'start': 2, 'end': 3, 'text': '花'}])
        self.assertEqual(splits['train'][0]['text'], self.rows[0]['発話'])
        self.assertEqual(splits['dev'][0]['spans'][1]['text'], '海の音に')
        self.assertEqual(splits['dev'][0]['spans'][1]['start'], 6)
        # 結合文字や補助平面の文字もPython codepointで往復する。
        span = span_builder.make_span('空から𠮷のがにして', '𠮷のが', '', 'to', 'fixture')
        self.assertEqual((span['start'], span['end']), (3, 7))

    # 見込み: 曖昧な出現・原文にない修復語を自動採用しなければ誤った教師を作らない。
    # 合格: 未指定の複数出現、範囲外番号、欠落語、from/toの同位置重複を生成前に拒否。
    def test_ambiguous_missing_and_overlapping_ranges_rejected(self):
        cases = [({'発話': '花を花にして', '変更元': '花', '変更先': '花'}, '出現番号'),
                 ({'変更元': '存在しない語'}, '原文にありません'),
                 ({'元の出現番号': '0'}, '出現番号'),
                 ({'発話': '山の朝を変えて', '変更元': '山の朝', '変更先': '山の朝'}, '重なって')]
        for patch, error in cases:
            rows = [dict(r) for r in self.rows]
            rows[0].update(patch)
            with self.subTest(patch=patch), self.assertRaisesRegex(ValueError, error):
                span_builder.read_csv([self.write(rows)])

    # 見込み: 未注釈と「抽出なし」の確認済み例を区別すれば空ラベルの誤学習を防げる。
    # 合格: 保留は除外、確認済み負例は空spans、空の正解未確認と矛盾する指定は拒否。
    def test_pending_and_explicit_negative_are_distinct(self):
        pending = {'使う': '保留', '発話': 'あとで注釈する'}
        negative = {'ID': 'negative', 'グループ': 'free-chat', '発話': 'きれいだね', '抽出なし': 'はい'}
        splits, skipped, _ = span_builder.read_csv([self.write(self.rows + [pending, negative])])
        self.assertEqual(len(skipped), 1)
        self.assertEqual(splits['train'][-1]['spans'], [])
        negative['抽出なし'] = ''
        with self.assertRaisesRegex(ValueError, '未注釈'):
            span_builder.read_csv([self.write(self.rows + [negative])])
        self.rows[0]['抽出なし'] = 'はい'
        with self.assertRaisesRegex(ValueError, '抽出なし'):
            span_builder.read_csv([self.write()])

    # 見込み: 同じ句・改訂・変更内容を用途間に出さなければ派生成績の水増しを防げる。
    # 合格: group/句ID/現在句/原文/変更元/先の交差を各々拒否。意味類似は人がgroup指定。
    def test_related_material_cannot_cross_any_split(self):
        cases = [('グループ', 'g1'), ('句ID', 'same-poem'), ('現在の句', self.rows[0]['現在の句']),
                 ('発話', self.rows[0]['発話']), ('変更元', '山の朝'), ('変更先', '朝の道')]
        for key, value in cases:
            rows = [dict(r) for r in self.rows]
            if key == '句ID':
                rows[0]['句ID'] = value
            rows[1][key] = value
            if key == '発話':
                rows[1]['変更元'], rows[1]['変更先'] = rows[0]['変更元'], rows[0]['変更先']
            elif key in ('変更元', '変更先'):
                rows[1]['発話'] = rows[1]['変更元'] + 'から' + rows[1]['変更先'] + 'に変えて'
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, '混入'):
                span_builder.read_csv([self.write(rows)])

    # 見込み: 同じCSVの重複投入では教材を水増しせず、矛盾は入力者へ戻す。
    # 合格: 同内容の2ファイルが4行へ縮約。用途・教師・IDの食い違いは拒否。
    def test_duplicates_are_not_oversampled_and_conflicts_rejected(self):
        one = self.write()
        two = self.write(filename='別コピー.csv')
        splits, skipped, _ = span_builder.read_csv([one, two])
        self.assertEqual(sum(map(len, splits.values())), 4)
        self.assertEqual(len(skipped), 4)
        duplicate = dict(self.rows[0]);duplicate['変更元'] = '山'
        with self.assertRaisesRegex(ValueError, 'ID|異なる正解'):
            span_builder.read_csv([self.write(self.rows + [duplicate])])

    # 見込み: 原文CSVと抽出器だけを同梱すれば、既存分類器の教師と混ざらず再現できる。
    # 合格: ZIPの全hash、調整/テストの範囲、Notebook構文、3周停止、元CSV不変を確認。
    def test_zip_routes_span_only_and_notebook_matches(self):
        assets = self.root / 'assets'
        packager.prepare_assets(REPO, assets)
        path = self.write()
        original = path.read_bytes()
        output = builder.build([path], assets, project=self.project)
        with zipfile.ZipFile(output / '教材.zip') as archive:
            for name, expected in json.loads(archive.read('input-sha256.json')).items():
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), expected)
            names = archive.namelist()
            self.assertFalse(any(name.startswith(('evidence/', 'baseline/', 'data/normal/', 'data/senryu/')) for name in names))
            self.assertEqual(archive.read('sources/01.csv'), original)
            protocol = json.loads(archive.read('training-protocol.json'))
            self.assertEqual(protocol['schema'], 'edit_span_csv_colab.v1')
            self.assertEqual(protocol['training']['early_stopping_patience'], 3)
            self.assertEqual(protocol['profiles']['span']['counts'], {'train': 1, 'dev': 1, 'test': 1, 'regression': 1})
            self.assertIn('span_train_colab.py', names)
            self.assertIn('shared/span_model.py', names)
        notebook = json.loads((output / '語句抽出_T4学習.ipynb').read_text())
        self.assertIn(hashlib.sha256((output / '教材.zip').read_bytes()).hexdigest(), json.dumps(notebook))
        for cell in notebook['cells']:
            if cell['cell_type'] == 'code' and not ''.join(cell['source']).startswith('%pip'):
                ast.parse(''.join(cell['source']))
        self.assertEqual(path.read_bytes(), original)
        again = builder.build([path], assets, project=self.project)
        self.assertNotEqual(output, again)
        self.assertTrue((output / '教材.zip').is_file())

    # 見込み: 別プロジェクトの教材や不足した分割を、そのまま別条件の学習へ流さない。
    # 合格: 複数project混入とtestなしを拒否し、失敗時に出力フォルダを作らない。
    def test_project_mixing_and_missing_split_fail_before_output(self):
        second = self.root / 'other'
        prepare.prepare(second)
        first = self.write()
        (second / '教材.csv').write_bytes(first.read_bytes())
        with self.assertRaisesRegex(ValueError, '別のプロジェクト'):
            builder.build([first, second / '教材.csv'], self.root / 'unused-assets', project=self.project)
        with self.assertRaisesRegex(ValueError, 'test: 教材が空'):
            builder.build([self.write(self.rows[:2])], self.root / 'unused-assets', project=self.project)
        self.assertFalse((self.project / '出力').exists())
        with self.assertRaisesRegex(ValueError, '上書きしません'):
            prepare.prepare(self.project)


if __name__ == '__main__':
    unittest.main()
