"""検証済みの保全planから、公開を選んだ項目だけを組み立てる。通信・生成はしない。"""
import hashlib
import json
from pathlib import Path
import uuid

from publish_bookshelf_data import text_file

SCHEMA = 'dogido.public_research.v1'
MARKER = '.dogido-public-data.json'
README = '''# ドギドの研究データ

句・J-lens候補・採取した内部表現の公開データです。

`contributors/<GitHubの数値ID>/<保管庫ID>/index.json` が句、版、採取要求、配列、J-lens、手動翻訳を結びます。
句・JSONは通常のGit、`hidden.f32` はGit LFSで保存します。clone後に `git lfs pull` を実行し、各indexのbytes・SHA-256で照合してください。

対話、プレイヤーメモ、材料本文、画像、端末パス、入力token列は含めません。元の保全記録とは別の公開用形式です。
句や候補語、内部表現そのものに入力の影響は残ります。匿名化を保証するデータではありません。
途中層のJ-lensは試験的転用で、候補語はモデルの確定した思考ではありません。翻訳は原文・スコアと分けて保存します。

提供は本棚のアップロードアイコンから本人のGitHubアカウントで行い、PRで受け付けます。自動送信・自動mergeはしません。
必要な準備と対象範囲は [提供方法](CONTRIBUTING.md) を参照してください。

本体: [DokiDoki_Dogido](https://github.com/yukincom/DokiDoki_Dogido)
'''
CONTRIBUTING = '''# データの提供

1. Git、Git LFS、GitHub CLIを用意し、`gh auth login --hostname github.com --web --git-protocol https` で自分のアカウントにログインします。
2. 本棚の研究保存先への保全を完了します。J-lensの採取済みデータがある場合は、既存のJ-lens読出し設定も必要です。
3. 句や候補語に公開したくない内容がないか確認し、メモ下のアップロードアイコンを押します。初回に公開する範囲を確認します。
4. 自分のforkへ全句の未送信分をまとめて送り、このリポジトリへのPRを作成・更新します。forkへpushした時点で公開されます。GitHubアイコンから送信したPRを開けます。

GitHubアカウントは提供者として公開されます。Gitの作者メールはGitHubのnoreply形式を使います。
会話・メモ・材料本文・入力token列・端末パスは送信対象外ですが、句、J-lens候補、出力token、内部表現にそれらの影響が残る可能性はあります。
アップロード後もMac・SSDの原本は残ります。元の非公開記録の履歴は公開用リポジトリへ混ぜません。

PRは自分の数値IDと保管庫IDのフォルダだけを変更してください。他の利用者のデータ、既存の内容hash付き記録、運用ファイルは上書きしません。
LFSの実体を別cloneで再取得して照合した後にPRを作ります。LFSの保存量・転送量は親リポジトリの利用枠に算入されます。
提供は任意です。権利や本人の同意を確認できないデータは提供しないでください。
'''


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def select(value, fields):
    """名前を列挙したscalarだけ。将来追加される自由文・入れ子は公開へ流さない。"""
    return {k: value[k] for k in fields if k in value and isinstance(value[k], (str, int, float, bool))}


def numeric_list(value):
    if not isinstance(value, list) or not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in value):
        raise ValueError('公開データの数値列が不正です。')
    return value


def version(value):
    result = {'surface_text': value.get('revised_surface_text') or value.get('surface_text') or value.get('revised_text') or value.get('text', ''),
              'reading_text': value.get('revised_text') or value.get('reading_text', '')}
    if not all(isinstance(v, str) for v in result.values()):
        raise ValueError('句の本文が不正です。')
    result['lines'] = [select(line, ('line_id', 'line_index', 'position', 'surface_text', 'reading_text'))
                       for line in value.get('lines', [])]
    return result


def file_identity(value):
    result = select(value, ('sha256', 'size_bytes'))
    if isinstance(value.get('path'), str):
        result['filename'] = value['path'].replace('\\', '/').rsplit('/', 1)[-1]
    return result


def public_manifest(value, source_hash):
    result = select(value, ('backend', 'response_id', 'choice_index', 'status', 'boundary', 'dtype',
                            'total_generated_tokens', 'max_positions', 'hidden_bytes', 'hidden_sha256'))
    result.update(schema='dogido.public_internal_capture.v1', source_manifest_sha256=source_hash,
                  hidden_file='hidden.f32', shape=numeric_list(value.get('shape', [])),
                  predicted_token_ids=numeric_list(value.get('predicted_token_ids', [])),
                  positions=[select(p, ('generation_index', 'input_position', 'predicted_token_id')) for p in value.get('positions', [])])
    identity = value.get('identity', {})
    result['identity'] = {k: file_identity(identity[k]) for k in ('model', 'collector', 'server_binary', 'source_patch') if isinstance(identity.get(k), dict)}
    result['identity']['libraries'] = [file_identity(p) for p in identity.get('libraries', [])]
    result['generation_settings'] = {k: v for k, v in select(value.get('generation_settings', {}),
        ('seed', 'temperature', 'top_k', 'top_p', 'min_p', 'repeat_last_n', 'repeat_penalty', 'presence_penalty',
         'frequency_penalty', 'max_tokens', 'n_predict', 'n_keep', 'n_discard', 'ignore_eos')).items()
        if isinstance(v, (int, float, bool))}
    return result


def public_readout(value, manifest):
    result = select(value, ('response_id', 'hidden_sha256', 'model_sha256', 'boundary', 'backend', 'status',
                            'processor_sha256', 'binding_sha256', 'lens_sha256', 'token_format', 'score_kind'))
    result.update(schema='dogido.public_j_lens.v1', source_manifest_sha256=value['manifest_sha256'],
                  manifest_sha256=manifest['sha256'], shape=numeric_list(value['shape']))
    result['positions'] = []
    for p in value['positions']:
        position = select(p, ('phase', 'position', 'input_position', 'predicted_token_id', 'predicted_piece'))
        position['layers'] = []
        for layer in p['layers']:
            row = select(layer, ('layer', 'method'))
            row['candidates'] = [select(c, ('id', 'piece', 'score', 'logprob')) for c in layer['candidates']]
            position['layers'].append(row)
        result['positions'].append(position)
    result['numeric_implementation'] = select(value.get('numeric_implementation', {}),
        ('numpy', 'dtype', 'gguf_source_revision', 'output_head', 'rms_epsilon', 'logit_softcap'))
    result['native_head_validation'] = select(value.get('native_head_validation', {}),
        ('generated_positions', 'top1_token_matches', 'all_available_top16_ids_order_equal', 'max_logprob_error'))
    return result


def projection(plan, contributor_id):
    if type(contributor_id) is not int or contributor_id <= 0:
        raise ValueError('GitHubの利用者IDを確認できません。')
    by_path = {file['path']: file for file in plan['files']}
    if len(by_path) != len(plan['files']):
        raise ValueError('保全planのパスが重複しています。')

    def load(ref):
        file = by_path[ref['path']]
        if any(file[k] != ref[k] for k in ('bytes', 'sha256')):
            raise ValueError('保全planの対応が一致しません。')
        if 'content' in file:
            data = file['content'].encode('utf-8')
        else:
            source = Path(file['source'])
            if not source.is_absolute() or any(p.is_symlink() for p in [source, *source.parents]):
                raise ValueError('保全元を確認できません。')
            data = source.read_bytes()
        if len(data) != file['bytes'] or hashlib.sha256(data).hexdigest() != file['sha256']:
            raise ValueError('保全planの内容が変わっています。')
        return json.loads(data)

    index = load(by_path['index.json'])
    archive_id = str(uuid.UUID(index['archive_id']))
    prefix = f'contributors/{contributor_id}/{archive_id}'
    files = {}

    def add(file):
        if file['path'] in files and file['sha256'] != files[file['path']]['sha256']:
            raise ValueError('公開パスの内容が衝突しています。')
        files[file['path']] = file
        return {k: file[k] for k in ('path', 'bytes', 'sha256')}

    def document(directory, value):
        content = encoded(value)
        sha = hashlib.sha256(content.encode()).hexdigest()
        return add(text_file(f'{prefix}/{directory}/{sha}.json', content))

    poems = []
    for poem in index['poems']:
        source = load(poem['snapshot'])['record']
        public_id = hashlib.sha256(poem['id'].encode()).hexdigest()
        snapshot = {'schema': 'dogido.public_poem.v1', 'id': public_id,
                    'original': version(source['original']), 'current': version(source['current']),
                    'revisions': [version(r) for r in source.get('revisions', [])]}
        row = {'id': public_id, 'snapshot': document('poems', snapshot), 'requests': []}
        source_requests = (source['original'].get('internal_capture') or {}).get('requests', [])
        for request in poem['requests']:
            entry = select(request, ('request_index', 'response_id', 'capture_status', 'availability'))
            kind = next((q.get('kind') for q in source_requests if q.get('request_index') == request['request_index']), None)
            if kind in ('haiku_irony', 'haiku_scene', 'haiku_draft', 'haiku_line_regeneration'):
                entry['kind'] = kind
            if 'manifest' in request:
                manifest = public_manifest(load(request['manifest']), request['manifest']['sha256'])
                # manifestと配列を同じhash付きディレクトリに置く。
                mh = hashlib.sha256(encoded(manifest).encode()).hexdigest()
                directory = f'{prefix}/captures/{mh}'
                entry['manifest'] = add(text_file(f'{directory}/manifest.json', encoded(manifest)))
                if 'hidden' in request:
                    raw = dict(by_path[request['hidden']['path']])
                    if raw['sha256'] != manifest['hidden_sha256'] or raw['bytes'] != manifest['hidden_bytes']:
                        raise ValueError('公開する配列とmanifestが一致しません。')
                    raw['path'] = f'{directory}/hidden.f32'
                    entry['hidden'] = add(raw)
                if 'j_lens' in request:
                    readout = load(request['j_lens'])
                    if readout['manifest_sha256'] != request['manifest']['sha256'] or readout['hidden_sha256'] != manifest['hidden_sha256']:
                        raise ValueError('J-lensと配列の対応が一致しません。')
                    entry['j_lens'] = document('j-lens', public_readout(readout, entry['manifest']))
                    entry['translations'] = []
                    for ref in request.get('translations', []):
                        translation = load(ref)
                        if translation['source']['readout_sha256'] != request['j_lens']['sha256']:
                            raise ValueError('翻訳とJ-lensの対応が一致しません。')
                        src = select(translation['source'], ('phase', 'layer', 'prompt_sha256', 'dictionary_worker_sha256'))
                        src.update(readout_sha256=entry['j_lens']['sha256'], manifest_sha256=entry['manifest']['sha256'])
                        translated = {'schema': 'dogido.public_j_lens_translation.v1', 'source': src,
                            'translations': [select(t, ('id', 'piece', 'text', 'method')) for t in translation['translations']]}
                        entry['translations'].append(document('translations', translated))
                    entry['translations'].sort(key=lambda x: x['path'])
            row['requests'].append(entry)
        poems.append(row)
    public_index = {'schema': SCHEMA, 'contributor_id': contributor_id, 'archive_id': archive_id,
                    'scope': 'poems_jlens_hidden_without_dialogue_notes', 'poems': poems}
    add(text_file(f'{prefix}/index.json', encoded(public_index)))
    return {'repository': plan['repository'], 'prefix': prefix, 'index': public_index,
            'index_path': f'{prefix}/index.json', 'files': sorted(files.values(), key=lambda x: x['path']),
            'poems': len(poems), 'captures': sum('hidden' in q for p in poems for q in p['requests'])}


def seed_files():
    """公開前の空の正本。既存のprivate履歴は取り込まない。"""
    return [text_file(MARKER, encoded({'schema': SCHEMA})), text_file('README.md', README),
            text_file('CONTRIBUTING.md', CONTRIBUTING),
            text_file('.gitattributes', '*.f32 filter=lfs diff=lfs merge=lfs -text\n'),
            text_file('.gitignore', '.DS_Store\n')]
