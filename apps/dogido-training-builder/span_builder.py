"""語句抽出の原文CSVを検査し、学習・評価を分離したColab教材を作る。"""
from __future__ import annotations

from collections import Counter
import csv
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unicodedata
import uuid
import zipfile

SCHEMA = 'edit_span_project.v1'
INPUT_FORMAT = 'current_verse_original_chars_v1'
HEADERS = ['使う', 'ID', '用途', 'グループ', '句ID', '現在の句', '発話', '変更元', '元の出現番号',
           '変更先', '先の出現番号', '抽出なし', '種別', 'メモ']
SPLITS = {'': 'train', '学習': 'train', 'train': 'train', '調整': 'dev', 'dev': 'dev',
          'テスト': 'test', 'test': 'test', '回帰': 'regression', 'regression': 'regression'}
KINDS = {'': ('real', 'real'), '実例': ('real', 'real'), '合成・正表記': ('synthetic', 'canonical'),
         '合成・認識表記': ('synthetic', 'stt_variant')}
MODEL = 'tohoku-nlp/bert-base-japanese-char-v3'
REVISION = 'ccddee593a9e03a589ae1feedcd4d1eb06d6ba02'


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def load_project(folder):
    folder = Path(folder).resolve()
    config = json.loads((folder / 'project.json').read_text(encoding='utf-8-sig'))
    if (config.get('schema') != SCHEMA or not config.get('id') or not config.get('name')
            or config.get('input_format') != INPUT_FORMAT):
        raise ValueError('語句抽出プロジェクトのID・名前・入力形式を確認してください。')
    name = config.get('dataset_file', '教材.csv')
    if Path(name).name != name or not name.endswith('.csv'):
        raise ValueError('教材ファイルはプロジェクト直下のCSVにしてください。')
    return {'config': config, 'folder': folder}


def occurrences(text, value):
    """重なる出現も原文上の開始位置順に数える。文字の正規化はしない。"""
    return [i for i in range(len(text) - len(value) + 1) if text.startswith(value, i)]


def make_span(text, value, occurrence, role, where):
    if not value:
        if occurrence.strip():
            raise ValueError(f'{where}: {role}の語句が空なのに出現番号があります。')
        return None
    found = occurrences(text, value)
    if not found:
        raise ValueError(f'{where}: {role}「{value}」が発話原文にありません。誤字も原文どおり記入してください。')
    index = occurrence.strip()
    if not index:
        if len(found) != 1:
            raise ValueError(f'{where}: {role}「{value}」は{len(found)}回あります。出現番号を1から指定してください。')
        number = 1
    elif not index.isascii() or not index.isdecimal() or not 1 <= int(index) <= len(found):
        raise ValueError(f'{where}: {role}の出現番号は1〜{len(found)}です。')
    else:
        number = int(index)
    start = found[number - 1]
    return {'role': role, 'start': start, 'end': start + len(value), 'text': value}


def normalized(value):
    # 分割の重複照合だけに使う。モデルへ渡す原文・正解範囲は変更しない。
    return ''.join(c for c in unicodedata.normalize('NFKC', value).casefold() if not c.isspace())


def row_keys(row):
    keys = [('group', row['group_id']), ('text', normalized(row['text']))]
    if row.get('entry_id'):
        keys.append(('poem', row['entry_id']))
    if row['current_verse']:
        keys.append(('verse', normalized(row['current_verse'])))
    keys += [(s['role'], normalized(s['text'])) for s in row['spans'] if normalized(s['text'])]
    return keys


def read_csv(paths):
    rows, skipped, sources = [], [], []
    seen_ids, seen_inputs = {}, {}
    for file_number, path in enumerate(paths, 1):
        path = Path(path).expanduser().resolve(strict=True)
        if path.suffix.lower() != '.csv':
            raise ValueError('教材CSVを指定してください: ' + path.name)
        raw = path.read_bytes()
        archive_name = f'sources/{file_number:02d}.csv'
        reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig'), newline=''), strict=True)
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)) or not {'発話', '変更元', '変更先', 'グループ'} <= set(fields):
            raise ValueError(f'{path.name}: 発話・変更元・変更先・グループの列を重複なく用意してください。')
        if set(fields) - set(HEADERS):
            raise ValueError(f'{path.name}: 未対応の列: ' + ', '.join(sorted(set(fields) - set(HEADERS))))
        sources.append((archive_name, raw, path.name))
        for row in reader:
            where = f'{path.name}:{reader.line_num}行目'
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f'{where}: 列数が合いません。カンマ・改行を含むセルは引用符で囲んでください。')
            if not any(v.strip() for v in row.values()):
                continue
            row = {key: row.get(key, '') for key in HEADERS}
            if any('\0' in v for v in row.values()):
                raise ValueError(f'{where}: NUL文字は使えません。')
            use = row['使う'].strip().lower()
            if use in ('保留', 'いいえ', 'no', 'false', '0'):
                skipped.append({'where': where, 'reason': '保留・不使用'})
                continue
            if use not in ('', 'はい', 'yes', 'true', '1'):
                raise ValueError(f'{where}: 使う列は「はい・保留・いいえ」です。')
            text, context = row['発話'], row['現在の句']
            if not text.strip():
                raise ValueError(f'{where}: 発話原文が空です。未注釈の行は保留にしてください。')
            if len(context) + len(text) + 3 > 512:
                raise ValueError(f'{where}: 現在の句と発話が長すぎます（特殊文字込み512文字まで）。原文を自動で切り詰めません。')
            group = row['グループ'].strip()
            if not group:
                raise ValueError(f'{where}: 同じ句・変更内容の派生をまとめるグループ名を記入してください。')
            if row['用途'].strip() not in SPLITS:
                raise ValueError(f'{where}: 用途は学習・調整・テスト・回帰です。')
            split = SPLITS[row['用途'].strip()]
            spans = [s for s in [make_span(text, row['変更元'], row['元の出現番号'], 'from', where),
                                make_span(text, row['変更先'], row['先の出現番号'], 'to', where)] if s is not None]
            none = row['抽出なし'].strip()
            if none not in ('', 'はい', 'いいえ') or (none == 'はい' and spans) or (not spans and none != 'はい'):
                raise ValueError(f'{where}: 抽出する語がない正解だけ「抽出なし」を「はい」にしてください。未注釈は保留です。')
            if len(spans) == 2 and max(s['start'] for s in spans) < min(s['end'] for s in spans):
                raise ValueError(f'{where}: 変更元と変更先が同じ文字位置に重なっています。出現番号を確認してください。')
            if row['種別'].strip() not in KINDS:
                raise ValueError(f'{where}: 種別は「実例」「合成・正表記」「合成・認識表記」のいずれかです。')
            kind, form = KINDS[row['種別'].strip()]
            identity = (context, text)
            rid = row['ID'].strip() or 'csv-' + sha(encoded(identity))[:20]
            value = {'id': rid, 'text': text, 'current_verse': context, 'spans': spans,
                     'group_id': group, 'entry_id': row['句ID'].strip(), 'split': split,
                     'kind': kind, 'form': form, 'source_csv': archive_name, 'source_line': reader.line_num}
            signature = {k: v for k, v in value.items() if k not in ('source_csv', 'source_line')}
            previous = seen_ids.get(rid)
            if previous is not None and previous != signature:
                raise ValueError(f'{where}: ID「{rid}」が別の内容・用途・グループにも使われています。')
            prior = seen_inputs.get(identity)
            if prior is not None:
                if any(prior[k] != value[k] for k in ('spans', 'split', 'group_id', 'entry_id', 'kind', 'form')):
                    raise ValueError(f'{where}: 同じ原文と現在句に異なる正解・用途・グループがあります。')
                skipped.append({'where': where, 'reason': '同じ原文と現在句の重複', 'retained_id': prior['id']})
                continue
            rows.append(value)
            seen_ids[rid] = signature
            seen_inputs[identity] = value
    if not rows:
        raise ValueError('使用する注釈済み教材がありません。')
    # 明示グループ以外に、同一の句・原文・変更元先も保守的に同じ分割へ限定する。
    seen_keys = {}
    for row in rows:
        for key in row_keys(row):
            prior = seen_keys.get(key)
            if prior is not None and prior['split'] != row['split']:
                raise ValueError(f"同じ{key[0]}が{prior['split']}と{row['split']}に混入しています: {prior['id']} / {row['id']}。派生は同じグループ・用途へまとめてください。")
            seen_keys[key] = row
    splits = {s: [r for r in rows if r['split'] == s] for s in ('train', 'dev', 'test', 'regression')}
    for s in ('train', 'dev', 'test'):
        if not splits[s]:
            raise ValueError(f'{s}: 教材が空です。学習・調整・テストの各用途を用意してください。')
    if not any(r['spans'] for r in splits['train']) or not any(r['spans'] for r in splits['dev']):
        raise ValueError('学習と調整には、変更元または変更先のある例を入れてください。')
    return splits, skipped, sources


def cell(kind, source):
    result = {'cell_type': kind, 'id': sha((kind + source).encode())[:12], 'metadata': {}, 'source': source.splitlines(keepends=True)}
    if kind == 'code':
        result.update(execution_count=None, outputs=[])
    return result


def make_notebook(zip_digest, bundle_id, project_name, counts):
    cells = [cell('markdown', f'''# {project_name}：語句抽出の学習・評価

1. ランタイムのタイプを **T4 GPU** に変更し、上からセルを実行します。
2. このNotebookと同時にできた **教材.zip** をアップロードします。
3. **学習する → 評価・テストする → 結果ZIPを保存** の順に押します。保存したモデルで1件ずつ試すこともできます。

学習 {counts['train']}件 / 調整 {counts['dev']}件 / テスト {counts['test']}件 / 回帰 {counts['regression']}件。
公開の日本語文字BERTから学習し直します。変更元・変更先の原文範囲だけを学習し、漢字変換や句の修正実行はしません。
調整成績（全範囲完全一致数、同数なら範囲F1）が3周改善しなければ停止し、最良の重みを残します。上限20周、seed42です。
テスト・回帰は重みやepochの選択に使いません。「評価・テストする」では再学習しません。
回帰は既知の問題の再確認です。テストを見て教材を調整した後は、そのテストも既知の診断として扱います。

[Colab公式FAQ](https://research.google.com/colaboratory/faq.html)のとおり、無料GPUの割り当て・実行時間は保証されません。
このNotebookは自分で選んだ教材をColabへアップロードして使います。本体への採用は別作業です。
'''), cell('code', "%pip -q install 'torch>=2.6' 'transformers==5.19.0' 'safetensors==0.8.0' 'ipywidgets==8.1.9'\n"), cell('code', '''import torch
if not torch.cuda.is_available():
    raise RuntimeError('ランタイムをT4 GPUに変更して接続してください。')
print('使用GPU:', torch.cuda.get_device_name(0))
'''), cell('code', f'''from google.colab import files
from pathlib import Path
import hashlib, io, json, zipfile
uploaded = files.upload()
if len(uploaded) != 1:
    raise ValueError('このNotebookと組の教材ZIPを1つ選んでください。')
payload = next(iter(uploaded.values()))
if hashlib.sha256(payload).hexdigest() != {zip_digest!r}:
    raise ValueError('このNotebookと組の教材.zipではありません。')
ROOT = Path('/content/edit-span-{bundle_id}')
ROOT.mkdir(exist_ok=True)
with zipfile.ZipFile(io.BytesIO(payload)) as archive:
    for name in archive.namelist():
        if not (ROOT / name).resolve().is_relative_to(ROOT.resolve()):
            raise ValueError('ZIPの保存先が不正です。')
    archive.extractall(ROOT)
for name, expected in json.loads((ROOT / 'input-sha256.json').read_text()).items():
    if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
        raise ValueError('教材が変更されています: ' + name)
print('教材を確認しました:', ROOT)
print(json.loads((ROOT / 'training-protocol.json').read_text())['profiles']['span']['counts'])
'''), cell('code', '''#@title 保存先と操作ボタン
Google_Driveに保存する = True #@param {type:"boolean"}
if Google_Driveに保存する:
    from google.colab import drive
    drive.mount('/content/drive')
    OUTPUT = Path('/content/drive/MyDrive/ドギド語句抽出学習') / ROOT.name
else:
    OUTPUT = ROOT / 'results'
import sys
sys.path.insert(0, str(ROOT))
from span_notebook_controls import show_controls
controls = show_controls(ROOT, OUTPUT)
''')]
    return encoded({'nbformat': 4, 'nbformat_minor': 5, 'metadata': {
        'colab': {'name': '語句抽出_T4学習.ipynb', 'provenance': []}, 'accelerator': 'GPU',
        'kernelspec': {'display_name': 'Python 3', 'name': 'python3'}, 'language_info': {'name': 'python'}}, 'cells': cells})


def build(paths, assets, output_parent, project):
    splits, skipped, sources = read_csv(paths)
    assets = Path(assets)
    checksums = json.loads((assets / 'baseline-sha256.json').read_text())
    code_names = ['shared/' + name for name in ('train.py', 'span_model.py', 'early_stopping.py', 'download_base.py')]
    code_names += ['span_train_colab.py', 'span_notebook_controls.py']
    files = {}
    for name in code_names:
        source_name = 'span/' + name
        raw = (assets / source_name).read_bytes()
        if checksums.get(source_name) != sha(raw):
            raise ValueError('同梱の語句抽出コードが変更されています。アプリを作り直してください: ' + name)
        files[name] = raw
    for split, rows in splits.items():
        if rows:
            files[f'data/{split}.jsonl'] = ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows).encode()
    files['data/dataset-status.json'] = encoded({'training_allowed': True, 'status': 'reviewed_csv', 'scope': 'CSVの指定用途を保持。regressionは学習外。'})
    for name, raw, original in sources:
        files[name] = raw
    files['project/project.json'] = encoded(project['config'])
    counts = {s: len(rs) for s, rs in splits.items()}
    bundle_id = datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    protocol = {'schema': 'edit_span_csv_colab.v1', 'bundle_id': bundle_id,
                'project': {k: project['config'][k] for k in ('id', 'name', 'input_format')},
                'profiles': {'span': {'counts': counts,
                    'groups': {s: len({r['group_id'] for r in rs}) for s, rs in splits.items()},
                    'forms': {s: dict(Counter(r['form'] for r in rs)) for s, rs in splits.items()},
                    'explicit_no_span': {s: sum(not r['spans'] for r in rs) for s, rs in splits.items()}}},
                'training': {'model': MODEL, 'revision': REVISION, 'seed': 42, 'epochs': 20,
                             'batch_size': 16, 'learning_rate': 3e-5, 'early_stopping_patience': 3},
                'split_integrity': {'cross_split_overlap': 0, 'group_required': True,
                    'checked_keys': ['group_id', 'entry_id', 'current_verse', 'text', 'from_text', 'to_text'],
                    'semantic_limit': '意味が似た別表現は自動判定できない。同じ変更内容の派生は同じグループ・用途を指定する。'},
                'source_csvs': [{'archive_path': n, 'filename': o, 'sha256': sha(b)} for n, b, o in sources],
                'skipped_csv_rows': skipped, 'runtime_adoption': False,
                'scope': '原文範囲抽出のモデル試験。ASR再認識・編集実行・本体採用は対象外。'}
    files['training-protocol.json'] = encoded(protocol)
    files['input-sha256.json'] = encoded({name: sha(raw) for name, raw in sorted(files.items())})
    parent = Path(output_parent) if output_parent else Path(paths[0]).expanduser().resolve().parent / '出力'
    parent.mkdir(parents=True, exist_ok=True)
    destination = parent / ('語句抽出教材-' + bundle_id)
    with tempfile.TemporaryDirectory(prefix='.building-', dir=parent) as temporary:
        stage = Path(temporary)
        with zipfile.ZipFile(stage / '教材.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, raw in sorted(files.items()):
                archive.writestr(name, raw)
        (stage / '語句抽出_T4学習.ipynb').write_bytes(make_notebook(sha((stage / '教材.zip').read_bytes()), bundle_id, project['config']['name'], counts))
        (stage / '生成結果.json').write_bytes(encoded(protocol))
        (stage / '教材の中身').mkdir()
        for split, rows in splits.items():
            if rows:
                (stage / '教材の中身' / f'{split}.jsonl').write_bytes(files[f'data/{split}.jsonl'])
        guide = ['# ' + project['config']['name'], '',
                 '語句抽出_T4学習.ipynbをGoogle Colabで開き、T4 GPUを選んで上から実行します。',
                 '教材.zipをアップロードし「学習する」→「評価・テストする」→「結果ZIPを保存」を押します。', '',
                 f"学習{counts['train']}件・調整{counts['dev']}件・テスト{counts['test']}件・回帰{counts['regression']}件。",
                 '3周改善なしで停止。調整だけで最良のepochを選び、テストと回帰は選択後に評価します。',
                 '変更元/先を範囲ごとに採点し、全範囲完全一致・適合率・再現率・F1と全失敗を保存します。',
                 '学習済みモデルを使って自由な発話を1件試すこともできます。',
                 '元CSVとコード・入力のhashを保存しています。以前の出力・原文CSVは上書きしていません。', '',
                 protocol['split_integrity']['semantic_limit'],
                 '学習済みの例の成績と、学習外のテスト・既知回帰の成績は分けて読みます。',
                 protocol['scope'], '']
        (stage / '最初に読む.md').write_text('\n'.join(guide), encoding='utf-8')
        stage.rename(destination)
    return destination
